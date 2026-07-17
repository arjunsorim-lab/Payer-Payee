"""Groq-backed, schema-validated provider episode explanation.

The browser never receives the API key. Only a de-identified deterministic
forecast, coded claim evidence, and provider-side administrative facts are sent.
"""

import json
import logging
import os
import re
import time
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .provider_prediction import build_provider_prediction_payload
except ImportError:
    from provider_prediction import build_provider_prediction_payload


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
PROMPT_VERSION = "provider-groq-v2.10"
_ANALYSIS_CACHE = {}
logger = logging.getLogger(__name__)

OUTPUT_FIELDS = (
    "provider_summary",
    "financial_forecast_summary",
    "risk_drivers",
    "recommended_actions",
    "evidence_used",
    "limitations",
    "unsupported_assumptions",
)


def _analysis_cache_key(model, case_input):
    return sha256(json.dumps({"model": model, "prompt": PROMPT_VERSION, "case": case_input}, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.IGNORECASE)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or set(payload) != set(OUTPUT_FIELDS):
        raise ValueError("Unexpected analysis fields")
    for field in ("risk_drivers", "recommended_actions", "evidence_used", "unsupported_assumptions"):
        if not isinstance(payload[field], list):
            raise ValueError(f"{field} must be an array")
        payload[field] = [str(item).strip() for item in payload[field] if str(item).strip()][:5]
    for field in ("provider_summary", "financial_forecast_summary", "limitations"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
        payload[field] = payload[field].strip()
    return payload


def _validate_grounded_scope(analysis, case_input):
    combined = " ".join(
        [analysis["provider_summary"], analysis["financial_forecast_summary"]]
        + analysis["risk_drivers"] + analysis["recommended_actions"]
    ).lower()
    prohibited = (
        "clinical justification", "medical necessity", "treatment plan", "payment plan",
        "preventive visit", "preventive care", "symptom", "lifestyle", "diagnose",
        "chart to support", "audit risk", "care plan",
    )
    if any(term in combined for term in prohibited):
        raise ValueError("Analysis exceeded provider administrative scope")
    selected_claim = case_input.get("actual_claim_facts", {})
    pos_description = str(selected_claim.get("place_of_service_description") or "").lower()
    cpt_description = str(selected_claim.get("cpt_description") or "").lower()
    if "office" in combined and "office" not in pos_description and "office" not in cpt_description:
        raise ValueError("Analysis inferred an unsupported place of service")
    if len(analysis["provider_summary"].split()) > 75 or len(analysis["financial_forecast_summary"].split()) > 60:
        raise ValueError("Analysis exceeded concise output limits")
    if len([item for item in re.findall(r"[^.!?]+(?:[.!?]+|$)", analysis["provider_summary"]) if item.strip()]) > 5:
        raise ValueError("Provider summary exceeded five sentences")


def _validate_numeric_grounding(analysis, case_input):
    numeric_values = []
    def collect(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            numeric_values.append(float(value))
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(case_input)
    allowed_currency = numeric_values
    allowed_percent = numeric_values + [value * 100 for value in numeric_values if 0 <= value <= 1]
    text = f"{analysis['provider_summary']} {analysis['financial_forecast_summary']}"
    for match in re.findall(r"\$([\d,]+(?:\.\d+)?)", text):
        value = float(match.replace(",", ""))
        if not any(abs(value - allowed) <= .011 for allowed in allowed_currency):
            raise ValueError("LLM introduced an unsupported currency value")
    for match in re.findall(r"(\d+(?:\.\d+)?)\s*%", text):
        value = float(match)
        if not any(abs(value - allowed) <= .11 for allowed in allowed_percent):
            raise ValueError("LLM introduced an unsupported percentage")


def _remove_unsupported_authorization_claims(summary):
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    kept = []
    for sentence in sentences:
        normalized = re.sub(r"[^a-z]+", " ", sentence.lower())
        unsupported = "authorization" in normalized and any(term in normalized for term in (" requirement ", " required ", " missing "))
        if not unsupported:
            kept.append(sentence)
    return " ".join(kept).strip()


def _constrain_provider_summary(summary):
    sentences = [item.strip() for item in re.findall(r"[^.!?]+(?:[.!?]+|$)", summary) if item.strip()][:5]
    constrained = " ".join(sentences)
    words = constrained.split()
    if len(words) > 75:
        constrained = " ".join(words[:75]).rstrip(".,;:") + "."
    return constrained


def _remove_numeric_forecast_sentences(text, replacement):
    protected = re.sub(r"(?<=\d)\.\s*(?=\d)", "<DECIMAL>", text)
    sentences = [item.replace("<DECIMAL>", ".").strip() for item in re.findall(r"[^.!?]+(?:[.!?]+|$)", protected) if item.strip()]
    unsupported_comparisons = (
        "close to", "similar to", "materially higher", "materially lower", "narrow range", "wide range",
        "slightly higher", "slightly lower", "higher than", "lower than",
        "no potential savings", "no savings", "no avoidable spend",
    )
    grounded = [
        sentence for sentence in sentences
        if "$" not in sentence
        and not re.search(r"\d+(?:\.\d+)?\s*%", sentence)
        and not re.search(r"\d+\s*\.\s*\d+", sentence)
        and not any(phrase in sentence.lower() for phrase in unsupported_comparisons)
    ]
    return " ".join(grounded).strip() or replacement


def build_llm_input(scenario):
    selected = scenario.get("selectedClaim") or scenario.get("anchor") or {}
    structured = build_provider_prediction_payload(scenario)
    return {
        "episode_id": scenario.get("episodeId"),
        "member_reference": scenario.get("memberReference"),
        "provider": {
            "billing_npi": selected.get("billingProviderNpi"),
            "rendering_npi": selected.get("renderingProviderNpi"),
            "payer_id": selected.get("payerId"),
        },
        "actual_claim_facts": structured["actual_claim_facts"],
        "deterministic_forecast": structured["forecast"],
        "prediction_basis": structured["prediction_basis"],
        "deterministic_risk_drivers": structured["risk_drivers"],
        "allowed_provider_actions": structured["recommended_actions"],
        "evidence_trace": structured["evidence_used"],
        "limitations": structured["limitations"],
    }


def _fallback_analysis(scenario, reason):
    structured = build_provider_prediction_payload(scenario)
    forecast = structured["forecast"]
    actual = structured["actual_claim_facts"]
    repeat = forecast["repeat_service_risk"]
    denial = forecast["denial_risk"]
    confidence = forecast["confidence"]
    return {
        "provider_summary": f"Actual adjudicated result: {actual.get('claim_status') or 'status unavailable'} with no recorded denial reason. Predicted denial probability is {denial.get('percentage', 0):.1f}% and predicted 90-day repeat-service probability is {(repeat.get('probability_90d') or 0) * 100:.1f}%. The forecast uses {confidence.get('peer_sample_size', 0)} earlier adjudicated peer claims.",
        "financial_forecast_summary": f"Retrospective current-claim estimate: predicted allowed ${forecast['predicted_allowed'].get('value', 0):,.2f} and predicted paid ${forecast['predicted_paid'].get('value', 0):,.2f}. These are estimates, not the actual adjudicated amounts.",
        "risk_drivers": scenario.get("riskDrivers", [])[:4],
        "recommended_actions": scenario.get("recommendedActions", [])[:4],
        "evidence_used": [f"Claim {item.get('claim_id')} — service date, CPT, diagnosis family, place of service, status and actual adjudication amounts" for item in structured["evidence_used"][:4]],
        "limitations": "Deterministic fallback shown because the Groq response was unavailable or invalid. This is administrative decision support, not a medical-necessity decision.",
        "unsupported_assumptions": [reason],
    }


def _schema():
    string_array = {"type": "array", "items": {"type": "string"}, "maxItems": 5}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "provider_summary": {"type": "string"},
            "financial_forecast_summary": {"type": "string"},
            "risk_drivers": string_array,
            "recommended_actions": string_array,
            "evidence_used": string_array,
            "limitations": {"type": "string"},
            "unsupported_assumptions": string_array,
        },
        "required": list(OUTPUT_FIELDS),
    }


def _request_groq(api_key, model, case_input, repair=False):
    instruction = (
        "You are a provider claims decision-support assistant. You receive actual claim facts and numeric forecasts generated by deterministic models. "
        "Explain the supplied forecasts without changing or recalculating any value. Clearly label actual historical facts separately from predictions. "
        "Do not invent money, percentages, payer requirements, denial codes, clinical conclusions, medical necessity, authorization rules or future certainty. "
        "Use only allowed_provider_actions; recommendations must remain conditional on supplied evidence. When evidence is insufficient, say so. "
        "The provider summary must contain no more than five short sentences. Every evidence item must cite an exact supplied claim ID using readable labels. "
        "Do not repeat currency amounts or percentages in provider_summary or financial_forecast_summary; refer to the structured prediction snapshot so rounding cannot alter a value. "
        "Return concise JSON matching the schema and record unsupported conclusions in unsupported_assumptions."
    )
    if repair:
        instruction += (
            " This is a validation-repair retry: return every required field with the exact required type and no extra fields. "
            "Keep provider_summary under 75 words and financial_forecast_summary under 60 words. "
            "Do not suggest clinical chart justification, medical care, preventive visits, member payment plans, or infer a place-of-service description from its code."
        )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(case_input, separators=(",", ":"))},
        ],
        "max_completion_tokens": 850,
        "reasoning_effort": "low",
        "temperature": 0.1,
        "response_format": {"type": "json_schema", "json_schema": {"name": "provider_episode_analysis", "strict": True, "schema": _schema()}},
    }).encode("utf-8")
    request = Request(GROQ_CHAT_COMPLETIONS_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "PayerPayee-Provider-Assistant/2.0",
    })
    with urlopen(request, timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "15"))) as response:
        raw = json.loads(response.read().decode("utf-8"))
    analysis = _parse_json(raw["choices"][0]["message"]["content"])
    analysis["provider_summary"] = _remove_unsupported_authorization_claims(analysis["provider_summary"])
    analysis["provider_summary"] = _remove_numeric_forecast_sentences(
        analysis["provider_summary"],
        "The actual adjudicated result and deterministic forecasts are shown separately in the structured sections below.",
    )
    separation_statement = "Actual adjudicated facts and deterministic predictions are displayed separately."
    normalized_summary = analysis["provider_summary"].lower()
    separates_values = "actual adjudicated" in normalized_summary and "deterministic" in normalized_summary and "separate" in normalized_summary
    if not separates_values:
        analysis["provider_summary"] = f"{separation_statement} {analysis['provider_summary']}"
    analysis["financial_forecast_summary"] = _remove_numeric_forecast_sentences(
        analysis["financial_forecast_summary"],
        "The retrospective financial estimates are shown in the Prediction Snapshot and remain separate from actual adjudicated amounts.",
    )
    analysis["provider_summary"] = _constrain_provider_summary(analysis["provider_summary"])
    if not analysis["provider_summary"]:
        raise ValueError("Provider summary contained only unsupported authorization claims")
    # The deterministic engine selects actions and risk drivers. Groq explains
    # them but cannot introduce new clinical or member-facing advice.
    analysis["risk_drivers"] = [
        f"{item.get('title')}: {item.get('value')} — {item.get('reason')}"
        for item in case_input.get("deterministic_risk_drivers", [])[:5]
    ]
    analysis["recommended_actions"] = [
        f"{item.get('title')}: {item.get('reason')}"
        for item in case_input.get("allowed_provider_actions", [])[:5]
    ]
    trace = case_input.get("evidence_trace", [])
    if trace:
        # Evidence strings are canonicalized so the UI always shows an exact
        # database claim identifier and the fields supporting the explanation.
        analysis["evidence_used"] = [
            f"Claim {item.get('claim_id')} — service date, CPT, diagnosis family, place of service, claim status and actual adjudication amounts"
            for item in trace[:5]
        ]
    _validate_grounded_scope(analysis, case_input)
    _validate_numeric_grounding(analysis, case_input)
    return analysis


def generate_provider_llm_analysis(scenario):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
    if not api_key:
        return {"configured": False, "model": model, "message": "Add GROQ_API_KEY to the backend .env file to enable the Groq provider analysis."}
    case_input = build_llm_input(scenario)
    cache_ttl = max(int(os.getenv("GROQ_CACHE_TTL_SECONDS", "900")), 0)
    cache_key = _analysis_cache_key(model, case_input)
    cached = _ANALYSIS_CACHE.get(cache_key)
    if cached and time.monotonic() - cached["createdAt"] <= cache_ttl:
        return {**cached["response"], "cached": True, "latencyMs": 0}
    started = time.monotonic()
    repaired = False
    try:
        try:
            analysis = _request_groq(api_key, model, case_input)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            repaired = True
            analysis = _request_groq(api_key, model, case_input, repair=True)
        result = {
            "configured": True,
            "model": model,
            "promptVersion": PROMPT_VERSION,
            "analysis": analysis,
            "cached": False,
            "repaired": repaired,
            "fallback": False,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
        _ANALYSIS_CACHE[cache_key] = {"createdAt": time.monotonic(), "response": result}
        logger.info("provider_llm_success episode=%s model=%s latency_ms=%s repaired=%s", scenario.get("episodeId"), model, result["latencyMs"], repaired)
        return result
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        try:
            error_payload = json.loads(detail).get("error") or detail
            message = error_payload.get("message") if isinstance(error_payload, dict) else str(error_payload)
        except json.JSONDecodeError:
            message = f"Groq HTTP {error.code}"
        if error.code == 401:
            message = "The configured Groq API key was rejected. Replace GROQ_API_KEY and restart the backend."
        logger.warning("provider_llm_http_error episode=%s code=%s", scenario.get("episodeId"), error.code)
    except (URLError, TimeoutError) as error:
        message = "Groq is currently unreachable or timed out."
        logger.warning("provider_llm_network_error episode=%s type=%s", scenario.get("episodeId"), type(error).__name__)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        message = "Groq returned an invalid structured response after one repair attempt."
        logger.warning("provider_llm_schema_error episode=%s type=%s", scenario.get("episodeId"), type(error).__name__)
    fallback = _fallback_analysis(scenario, message)
    result = {
        "configured": True,
        "model": model,
        "promptVersion": PROMPT_VERSION,
        "analysis": fallback,
        "cached": False,
        "repaired": repaired,
        "fallback": True,
        "message": message,
        "latencyMs": round((time.monotonic() - started) * 1000),
    }
    _ANALYSIS_CACHE[cache_key] = {"createdAt": time.monotonic(), "response": result}
    return result
