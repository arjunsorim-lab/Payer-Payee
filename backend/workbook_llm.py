"""Workbook-grounded prediction explanation and claim-scoped chat."""

from __future__ import annotations

import json
import os
import re
import time
from hashlib import sha256
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .financial_engine import build_financial_result
    from .ollama_service import OllamaClient, OllamaError
    from .workbook_enrichment import CALCULATION_VERSION, GROQ_PROMPT_VERSION, RAG_INDEX_VERSION
    from .workbook_rag import retrieve_evidence
except ImportError:
    from financial_engine import build_financial_result
    from ollama_service import OllamaClient, OllamaError
    from workbook_enrichment import CALCULATION_VERSION, GROQ_PROMPT_VERSION, RAG_INDEX_VERSION
    from workbook_rag import retrieve_evidence


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_INSTRUCTION = (
    "You are the explanation layer for a data-driven healthcare claims prediction system. "
    "Use only the supplied structured prediction results and retrieved workbook evidence. "
    "Clearly distinguish facts directly present in claims data, historical patterns identified "
    "by the backend, predictions calculated by Python, supported financial opportunities, and "
    "areas where evidence is limited. Never diagnose a patient, provide clinical advice, "
    "recommend treatment or medication, assume why a service occurred, infer treatment failure "
    "or medical necessity, invent payer policy or financial values, recalculate predictions, or "
    "alter supplied values. For repeated claims report only observable dates, codes, financial "
    "patterns and time differences. If the clinical reason is not established, state that it "
    "cannot be determined from the available claims data. Do not perform arithmetic."
)
SUGGESTED_QUESTIONS = [
    "How much can be saved?",
    "How much can be recovered?",
    "What is the best action?",
    "Why is this amount shown?",
]
_ANALYSIS_CACHE = {}
_CHAT_CACHE = {}
_LOCK = RLock()
_CURRENCY_PATTERN = re.compile(r"\$[\d,]+(?:\.\d+)?")
_PERCENT_PATTERN = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%")
OLLAMA_EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "what_claim_data_shows": {"type": "string", "maxLength": 500},
        "historical_patterns_found": {"type": "string", "maxLength": 500},
        "prediction_summary": {"type": "string", "maxLength": 500},
        "financial_impact": {"type": "string", "maxLength": 500},
        "supporting_evidence_summary": {"type": "string", "maxLength": 500},
        "limitations": {"type": "string", "maxLength": 500},
    },
    "required": [
        "what_claim_data_shows",
        "historical_patterns_found",
        "prediction_summary",
        "financial_impact",
        "supporting_evidence_summary",
        "limitations",
    ],
}


def clear_llm_caches():
    with _LOCK:
        _ANALYSIS_CACHE.clear()
        _CHAT_CACHE.clear()


def _money(value):
    return f"${float(value or 0):,.2f}"


def _normalized_question(value):
    lowered = str(value or "").strip().lower()
    without_punctuation = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(without_punctuation.split())


def _financial_explanation(result):
    summary = result["supported_money_summary"]
    snapshot = result["financial_prediction_snapshot"]
    categories = result["financial_opportunities"]
    evidence_fields = list(dict.fromkeys(
        field
        for category in categories.values()
        for field in category["evidence_fields"]
    ))
    evidence_ids = list(dict.fromkeys(
        claim_id
        for category in categories.values()
        for claim_id in category["evidence_claim_ids"]
    ))
    return {
        "recoverable_now": summary["recoverable_now"],
        "predicted_avoidable_spend": snapshot[
            "predicted_avoidable_spend"
        ],
        "predicted_avoidable_provider_payment": snapshot[
            "predicted_avoidable_provider_payment"
        ],
        "validated_avoidable_spend": result[
            "validated_avoidable_spend"
        ],
        "potentially_avoidable_spend_supported": summary["potentially_avoidable_spend_supported"],
        "predicted_provider_payment": snapshot["predicted_provider_payment"],
        "predicted_contractual_adjustment": snapshot["predicted_contractual_adjustment"],
        "future_denial_exposure": summary["future_denial_exposure"],
        "future_denial_exposure_detail": snapshot[
            "future_denial_exposure"
        ],
        "future_repeat_payment_exposure": summary["future_repeat_payment_exposure"],
        "best_action": summary["best_action"],
        "formula_trace": [
            *summary["calculation_trace"],
            {
                "category": "predicted_avoidable_spend",
                "amount": snapshot["predicted_avoidable_spend"]["value"],
                "formula": snapshot["avoidable_formula_trace"]["formula"],
                "reason_code": "PREDICTED_90_DAY_AVOIDABLE_SPEND",
                "components": snapshot["avoidable_formula_trace"],
            },
        ],
        "evidence_fields": evidence_fields,
        "evidence_claim_ids": evidence_ids,
        "rag_retrieval": result["rag"]["retrieved_chunks"],
        "confidence": result["confidence"],
        "consistency_check": result["consistency_check"],
        "limitations": result["limitations"],
    }


def _authoritative_answer(result, question):
    normalized = _normalized_question(question)
    summary = result["supported_money_summary"]
    snapshot = result["financial_prediction_snapshot"]
    best = summary["best_action"]
    avoidable = snapshot["predicted_avoidable_spend"]
    validated = result["validated_avoidable_spend"]
    if "avoidable" in normalized:
        return (
            f"The expected avoidable repeat cost over the next 90 days is "
            f"{_money(avoidable['value'])}. In plain language, this is the average "
            f"extra allowed cost the model expects from a related repeat that might "
            f"have been avoided; it is not confirmed savings or money recoverable now. "
            f"Python calculated it as {avoidable['repeat_probability_90d'] * 100:.1f}% "
            f"repeat risk × {avoidable['avoidable_given_repeat_probability'] * 100:.1f}% "
            f"chance the repeat is avoidable × "
            f"{_money(avoidable['expected_extra_repeat_allowed_cost'])} extra repeat "
            f"cost. The likely range is {_money(avoidable['low'])} to "
            f"{_money(avoidable['high'])}."
        )
    if "predicted" in normalized and any(term in normalized for term in ("paid", "payment")):
        return (
            f"Predicted provider payment is {_money(snapshot['predicted_provider_payment']['value'])}, "
            f"with backend range {_money(snapshot['predicted_provider_payment']['low'])} to "
            f"{_money(snapshot['predicted_provider_payment']['high'])}."
        )
    if "denial" in normalized and "exposure" in normalized:
        return (
            f"Predicted denial revenue exposure is {_money(snapshot['predicted_denial_revenue_exposure'])}. "
            f"The final backend denial probability is {snapshot['denial_probability'] * 100:.1f}%."
        )
    if "repeat" in normalized and "exposure" in normalized:
        return (
            f"Predicted repeat-payment exposure is {_money(snapshot['predicted_repeat_payment_exposure'])}. "
            f"The final backend 90-day repeat probability is {snapshot['repeat_probability_90d'] * 100:.1f}%."
        )
    if any(term in normalized for term in ("save", "recover", "amount", "how much")):
        return (
            f"Recoverable money currently supported by the workbook is {_money(summary['recoverable_now'])}. "
            f"Predicted avoidable spend over 90 days is {_money(avoidable['value'])}. "
            f"Retrospectively validated avoidable spend is {_money(validated['value'])}. "
            f"The best action is {best['stage']} and addresses {_money(best['amount_addressed'])}."
        )
    if "best action" in normalized or "what should" in normalized:
        return (
            f"The best supported action is {best['stage']}: {best['action']} "
            f"The workbook-supported amount addressed is {_money(best['amount_addressed'])}."
        )
    if "why" in normalized:
        opportunity_name = summary["top_supported_opportunity_type"]
        category = result["financial_opportunities"].get(opportunity_name)
        if category:
            return (
                f"The amount is shown because {category['reason']} "
                f"Calculation: {category['formula']} Reason code: {category['reason_code']}."
            )
        return (
            f"The workbook currently supports {_money(summary['recoverable_now'])} recoverable now. "
            f"The category-specific reasons and formulas are listed in the calculation trace."
        )
    return (
        f"The workbook supports {_money(summary['recoverable_now'])} recoverable now. "
        f"Best action: {best['stage']}. Future denial exposure is {_money(summary['future_denial_exposure'])} "
        f"and future repeat-payment exposure is {_money(summary['future_repeat_payment_exposure'])}; both remain separate from recoverable money."
    )


def _groq_exact_answer(authoritative_answer, result, rag):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
    if not api_key:
        return authoritative_answer, "deterministic_backend", model
    safe_result = {
        "claim_id": result["claim_id"],
        "source": {
            "workbook_hash": result["source"]["workbook_hash"],
            "source_sheet": result["source"]["source_sheet"],
            "source_row": result["source"]["source_row"],
        },
        "actual_claim_facts": result["actual_claim_facts"],
        "financial_prediction_snapshot": result["financial_prediction_snapshot"],
        "supported_financial_opportunities": result["supported_financial_opportunities"],
        "non_actionable_evidence": result["non_actionable_evidence"],
        "supported_money_summary": result["supported_money_summary"],
        "best_action": result["best_action"],
        "confidence": result["confidence"],
        "limitations": result["limitations"],
        "rag": rag,
    }
    body = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTION
                + " Return the supplied authoritative answer exactly.",
            },
            {
                "role": "user",
                "content": json.dumps({
                    "authoritative_answer": authoritative_answer,
                    "canonical_financial_result": safe_result,
                }, separators=(",", ":")),
            },
        ],
        "temperature": 0,
        "max_completion_tokens": 300,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    request = Request(
        GROQ_CHAT_COMPLETIONS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(
            request,
            timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "15")),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        generated = json.loads(payload["choices"][0]["message"]["content"])
        answer = str(generated.get("answer") or "").strip()
        if answer != authoritative_answer:
            raise ValueError("Groq changed the authoritative backend answer.")
        return answer, "groq_exact_copy", model
    except (
        HTTPError,
        URLError,
        TimeoutError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return authoritative_answer, "deterministic_backend", model


def _canonical_numbers(value):
    numbers = []
    if isinstance(value, dict):
        for item in value.values():
            numbers.extend(_canonical_numbers(item))
    elif isinstance(value, list):
        for item in value:
            numbers.extend(_canonical_numbers(item))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers.append(float(value))
    return numbers


def _validate_model_numbers(text, result):
    canonical = _canonical_numbers({
        "actual_claim_facts": result["actual_claim_facts"],
        "financial_prediction_snapshot": result["financial_prediction_snapshot"],
        "supported_money_summary": result["supported_money_summary"],
        "supported_financial_opportunities": result[
            "supported_financial_opportunities"
        ],
        "historical_prediction_basis": result["historical_prediction_basis"],
    })
    allowed_money = {round(number, 2) for number in canonical}
    allowed_percent = {
        round(number * 100, 4)
        for number in canonical
        if 0 <= number <= 1
    }
    introduced = []
    for token in _CURRENCY_PATTERN.findall(text):
        number = round(float(token.replace("$", "").replace(",", "")), 2)
        if number not in allowed_money:
            introduced.append(token)
    for token in _PERCENT_PATTERN.findall(text):
        number = round(float(token[:-1]), 4)
        if number not in allowed_percent:
            introduced.append(token)
    if introduced:
        raise ValueError(
            "Ollama introduced numeric values absent from the canonical prediction: "
            + ", ".join(introduced)
        )


def _ollama_explanation(authoritative_answer, result, rag, question=""):
    client = OllamaClient()
    evidence = [
        {
            "document_id": document.get("document_id"),
            "document_type": document.get("document_type"),
            "claim_id": document.get("claim_id"),
            "source_sheet": document.get("source_sheet"),
            "source_row": document.get("source_row"),
            "reason_code": document.get("reason_code"),
            "fields_used": document.get("fields_used"),
            # The full de-identified text remains in the retrieval response and
            # vector store. A bounded excerpt keeps local chat within small-model
            # context limits without changing any retrieved evidence or numbers.
            "text_excerpt": str(document.get("text") or "")[:280],
        }
        for document in rag.get("retrieved_documents", [])
    ]
    safe_payload = {
        "question": question,
        "authoritative_backend_answer": authoritative_answer,
        "actual_claim_facts": result["actual_claim_facts"],
        "financial_prediction_snapshot": result[
            "financial_prediction_snapshot"
        ],
        "supported_money_summary": result["supported_money_summary"],
        "supported_financial_opportunities": result[
            "supported_financial_opportunities"
        ],
        "historical_prediction_basis": result[
            "historical_prediction_basis"
        ],
        "historical_patterns": result.get("historical_patterns", {}),
        "similar_historical_claims": result.get("similar_historical_claims", []),
        "short_timeframe_patterns": result.get("short_timeframe_patterns", []),
        "rag_evidence": evidence,
    }
    last_error = None
    for attempt in range(2):
        strict = (
            " Return JSON matching the schema. Do not perform arithmetic. "
            "Use the authoritative backend answer verbatim where it contains a "
            "number. Do not introduce any currency or percentage."
            if attempt
            else (
                " Return concise JSON matching the supplied schema. Do not add any "
                "number or clinical explanation absent from the backend payload."
            )
        )
        try:
            response = client.chat(
                [
                    {"role": "system", "content": SYSTEM_INSTRUCTION + strict},
                    {
                        "role": "user",
                        "content": json.dumps(
                            safe_payload, separators=(",", ":")
                        ),
                    },
                ],
                json_schema=OLLAMA_EXPLANATION_SCHEMA,
            )
            structured = json.loads(response["content"])
            if not isinstance(structured, dict) or any(
                key not in structured
                for key in OLLAMA_EXPLANATION_SCHEMA["required"]
            ):
                raise ValueError("Ollama returned an invalid explanation schema.")
            text = " ".join(
                str(structured[key])
                for key in OLLAMA_EXPLANATION_SCHEMA["required"]
            )
            _validate_model_numbers(text, result)
            answer = " ".join(str(structured[key]).strip() for key in OLLAMA_EXPLANATION_SCHEMA["required"] if str(structured[key]).strip())
            if not answer:
                raise ValueError("Ollama returned an empty explanation.")
            return answer, "ollama", response["model"], structured
        except OllamaError as error:
            last_error = error
            break
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    return (
        authoritative_answer,
        "deterministic_backend",
        client.chat_model,
        {
            "limitations": [
                "Local model explanation was skipped; the deterministic backend result is shown."
            ]
            if last_error
            else []
        },
    )


def _provider_explanation(authoritative_answer, result, rag, question=""):
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider == "ollama":
        if os.getenv("OLLAMA_CHAT_ENABLED", "false").strip().lower() not in {
            "1", "true", "yes", "on",
        }:
            return (
                authoritative_answer,
                "deterministic_backend",
                "canonical-claim-assistant",
                {},
            )
        return _ollama_explanation(
            authoritative_answer, result, rag, question
        )
    if provider == "groq":
        answer, source, model = _groq_exact_answer(
            authoritative_answer, result, rag
        )
        return answer, source, model, {}
    return (
        authoritative_answer,
        "deterministic_backend",
        provider or "none",
        {"limitations": [f"Unsupported LLM_PROVIDER: {provider}"]},
    )


def _retrieval_or_fallback(database, result, question):
    try:
        return retrieve_evidence(database, result, question)
    except (OllamaError, RuntimeError):
        return {
            **result["rag"],
            "query": question,
            "retrieved_documents": [],
            "retrieved_chunks": [],
            "retrieval_status": "local_model_temporarily_unavailable",
        }


def _plain_language_explanation(result):
    summary = result["supported_money_summary"]
    snapshot = result["financial_prediction_snapshot"]
    actual = result["actual_claim_facts"]
    action = summary["best_action"]
    supported = result["supported_financial_opportunities"]
    primary = supported[0] if supported else None
    predicted_paid = snapshot["predicted_provider_payment"]
    avoidable = snapshot["predicted_avoidable_spend"]

    simple_summary = (
        f"The model estimates that the provider payment for this claim will be "
        f"{_money(predicted_paid['value'])}. Based on similar earlier workbook records, "
        f"the expected range is {_money(predicted_paid['low'])} to "
        f"{_money(predicted_paid['high'])}. The actual payment recorded in the workbook "
        f"is {_money(actual['paid'])}, so the prediction and the actual result are shown separately."
    )
    if primary:
        opportunity = (
            f"The workbook evidence identifies {_money(primary['amount'])} as the main "
            f"financial amount that can be acted on now. It is classified as "
            f"{primary['label'].lower()}. {primary['reason']}"
        )
        calculation = (
            f"The workbook calculation is {primary['formula']}. This formula uses the "
            f"claim values shown above; the explanation does not recalculate or replace them."
        )
    else:
        opportunity = (
            "The workbook evidence does not select a positive financial action for this "
            "claim. The forecast still describes possible future payment risk, but it is "
            "not counted as money recoverable now."
        )
        calculation = (
            "Each financial category was checked independently. The category-specific "
            "workbook values and reasons are available under “Why Other Actions Were Not Selected.”"
        )
    next_step = (
        f"The suggested operational next step is {action['stage']}. In everyday terms: "
        f"{action['action']} This step addresses {_money(action['amount_addressed'])} "
        f"of workbook-supported money."
    )
    forecast_note = (
        f"The model also estimates {_money(snapshot['predicted_denial_revenue_exposure'])} "
        f"of denial exposure and {_money(snapshot['predicted_repeat_payment_exposure'])} "
        f"of repeat-payment exposure. These are possible future risks, not savings and "
        f"not amounts already owed."
    )
    avoidable_note = (
        f"Predicted avoidable spend is {_money(avoidable['value'])} over the next "
        f"90 days, with a likely range of {_money(avoidable['low'])} to "
        f"{_money(avoidable['high'])}. Python combined the "
        f"{avoidable['repeat_probability_90d'] * 100:.1f}% repeat probability, "
        f"{avoidable['avoidable_given_repeat_probability'] * 100:.1f}% chance that "
        f"a repeat would be potentially avoidable and "
        f"{_money(avoidable['expected_extra_repeat_allowed_cost'])} expected extra "
        f"repeat cost. This is a forecast, not confirmed savings."
    )
    confidence = (
        f"Model confidence is {snapshot['confidence']['score'] * 100:.1f}%. "
        f"{snapshot['confidence']['reason']}"
    )
    return {
        "summary": simple_summary,
        "sections": [
            {"title": "What this prediction means", "body": simple_summary},
            {"title": "Money that can be acted on now", "body": opportunity},
            {"title": "How the amount was determined", "body": calculation},
            {"title": "What to do next", "body": next_step},
            {"title": "What the forecast risk means", "body": forecast_note},
            {"title": "Predicted avoidable spend", "body": avoidable_note},
            {"title": "How confident the model is", "body": confidence},
        ],
    }


def generate_workbook_prediction_explanation(database, claim_id):
    result = build_financial_result(database, claim_id)
    cache_key = (
        database.workbook_hash,
        result["claim_id"],
        result["financial_result_hash"],
        CALCULATION_VERSION,
        RAG_INDEX_VERSION,
        GROQ_PROMPT_VERSION,
        os.getenv("LLM_PROVIDER", "ollama"),
        os.getenv("OLLAMA_CHAT_MODEL", "gemma3"),
        os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma"),
    )
    with _LOCK:
        cached = _ANALYSIS_CACHE.get(cache_key)
        if cached:
            return cached
    rag = _retrieval_or_fallback(
        database,
        result,
        "provider financial prediction explanation",
    )
    result = {**result, "rag": rag, "rag_evidence": rag["retrieved_chunks"]}
    plain_language = _plain_language_explanation(result)
    authoritative = plain_language["summary"]
    narrative, source, model, model_explanation = _provider_explanation(
        authoritative, result, rag
    )
    explanation = {
        **plain_language,
        "summary": narrative,
        "source": source,
        "system_instruction": SYSTEM_INSTRUCTION,
        "model_explanation": model_explanation,
    }
    response = {
        **result,
        "configured": source in {"ollama", "groq_exact_copy"},
        "model": model,
        "prompt_version": GROQ_PROMPT_VERSION,
        "prediction_explanation": explanation,
        "explanation": explanation,
        "suggested_questions": SUGGESTED_QUESTIONS,
        "cached": False,
    }
    with _LOCK:
        _ANALYSIS_CACHE[cache_key] = response
    return response


def generate_workbook_llm_analysis(database, claim_id):
    """Temporary API-compatible alias for the prediction explanation."""
    return generate_workbook_prediction_explanation(database, claim_id)


def generate_workbook_chat_answer(database, claim_id, episode_id, question, conversation_id):
    result = build_financial_result(database, claim_id)
    if result["episode_id"] != episode_id:
        raise ValueError("The episode does not match the selected workbook claim.")
    normalized = _normalized_question(question)
    cache_key = (
        database.workbook_hash,
        result["claim_id"],
        result["episode_id"],
        result["financial_result_hash"],
        normalized,
        CALCULATION_VERSION,
        RAG_INDEX_VERSION,
        GROQ_PROMPT_VERSION,
        os.getenv("LLM_PROVIDER", "ollama"),
        os.getenv("OLLAMA_CHAT_MODEL", "gemma3"),
        os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma"),
    )
    with _LOCK:
        cached = _CHAT_CACHE.get(cache_key)
        if cached:
            return {**cached, "conversation_id": conversation_id, "cached": True}
    local_chat_enabled = os.getenv(
        "OLLAMA_CHAT_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if (
        os.getenv("LLM_PROVIDER", "ollama").strip().lower() == "ollama"
        and not local_chat_enabled
    ):
        rag = {
            **result["rag"],
            "query": question,
            "retrieved_documents": [],
            "retrieved_chunks": [],
            "retrieval_status": "canonical_claim_context",
        }
    else:
        rag = _retrieval_or_fallback(database, result, question)
    result = {**result, "rag": rag, "rag_evidence": rag["retrieved_chunks"]}
    authoritative = _authoritative_answer(result, question)
    answer, source, model, model_explanation = _provider_explanation(
        authoritative, result, rag, question
    )
    financial_explanation = _financial_explanation(result)
    snapshot = result["financial_prediction_snapshot"]
    summary = result["supported_money_summary"]
    prediction_money_breakdown = {
        "recoverable_now": summary["recoverable_now"],
        "supported_avoidable_spend": summary[
            "potentially_avoidable_spend_supported"
        ],
        "predicted_avoidable_spend": snapshot[
            "predicted_avoidable_spend"
        ],
        "predicted_avoidable_provider_payment": snapshot[
            "predicted_avoidable_provider_payment"
        ],
        "validated_avoidable_spend": result[
            "validated_avoidable_spend"
        ],
        "predicted_provider_payment": snapshot[
            "predicted_provider_payment"
        ]["value"],
        "predicted_allowed": snapshot["predicted_allowed"]["value"],
        "predicted_adjustment": snapshot[
            "predicted_contractual_adjustment"
        ]["value"],
        "future_denial_exposure": summary["future_denial_exposure"],
        "future_repeat_payment_exposure": summary[
            "future_repeat_payment_exposure"
        ],
        "best_action": summary["best_action"],
        "formulas": summary["calculation_trace"],
        "confidence": snapshot["confidence"],
    }
    response = {
        "answer": answer,
        "ollama_explanation": model_explanation,
        "prediction_money_breakdown": prediction_money_breakdown,
        "financial_explanation": financial_explanation,
        "claim_id": result["claim_id"],
        "episode_id": result["episode_id"],
        "conversation_id": conversation_id,
        "model": model,
        "prompt_version": GROQ_PROMPT_VERSION,
        "explanation_source": source,
        "evidence_claim_ids": financial_explanation["evidence_claim_ids"],
        "rag_evidence": rag["retrieved_documents"],
        "rag": rag,
        "source": {
            "workbook_hash": database.workbook_hash,
            "prediction_version": result["versions"]["prediction_version"],
            "calculation_version": result["versions"]["calculation_version"],
            "rag_version": rag.get("rag_version")
            or rag.get("index_version")
            or RAG_INDEX_VERSION,
            "embedding_model": rag.get("embedding_model")
            or "canonical-claim-context",
            "chat_model": model,
        },
        "limitations": result["limitations"],
        "suggested_questions": SUGGESTED_QUESTIONS,
        "cached": False,
    }
    with _LOCK:
        _CHAT_CACHE[cache_key] = {key: value for key, value in response.items() if key != "conversation_id"}
    return response
