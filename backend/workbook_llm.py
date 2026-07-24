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
    from .workbook_enrichment import CALCULATION_VERSION, GROQ_PROMPT_VERSION, RAG_INDEX_VERSION
    from .workbook_rag import retrieve_evidence
except ImportError:
    from financial_engine import build_financial_result
    from workbook_enrichment import CALCULATION_VERSION, GROQ_PROMPT_VERSION, RAG_INDEX_VERSION
    from workbook_rag import retrieve_evidence


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_INSTRUCTION = (
    "You explain an already calculated provider financial prediction. All numeric "
    "values, classifications, ranges, probabilities, sample sizes and recommended "
    "actions are final backend values. Never calculate, alter, round differently, "
    "combine or invent a value. Use only the supplied result and retrieved workbook evidence."
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


def clear_llm_caches():
    with _LOCK:
        _ANALYSIS_CACHE.clear()
        _CHAT_CACHE.clear()


def _money(value):
    return f"${float(value or 0):,.2f}"


def _normalized_question(value):
    return " ".join(str(value or "").strip().lower().split())


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
        "potentially_avoidable_spend_supported": summary["potentially_avoidable_spend_supported"],
        "predicted_provider_payment": snapshot["predicted_provider_payment"],
        "predicted_contractual_adjustment": snapshot["predicted_contractual_adjustment"],
        "future_denial_exposure": summary["future_denial_exposure"],
        "future_repeat_payment_exposure": summary["future_repeat_payment_exposure"],
        "best_action": summary["best_action"],
        "formula_trace": summary["calculation_trace"],
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
            f"Potentially avoidable episode spend currently supported is {_money(summary['potentially_avoidable_spend_supported'])}. "
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
            {"role": "system", "content": SYSTEM_INSTRUCTION + " Return the supplied authoritative answer exactly, without adding or removing text."},
            {"role": "user", "content": json.dumps({
                "authoritative_answer": authoritative_answer,
                "canonical_financial_result": safe_result,
            }, separators=(",", ":"))},
        ],
        "temperature": 0,
        "max_completion_tokens": 300,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "workbook_claim_answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            },
        },
    }).encode("utf-8")
    request = Request(
        GROQ_CHAT_COMPLETIONS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PayerPayee-Workbook-Assistant/1.0",
        },
    )
    try:
        with urlopen(request, timeout=float(os.getenv("GROQ_TIMEOUT_SECONDS", "15"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
        generated = json.loads(payload["choices"][0]["message"]["content"])
        answer = str(generated.get("answer") or "").strip()
        if answer != authoritative_answer:
            raise ValueError("Groq changed an authoritative backend value or explanation.")
        return answer, "groq_exact_copy", model
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return authoritative_answer, "deterministic_backend", model


def _plain_language_explanation(result):
    summary = result["supported_money_summary"]
    snapshot = result["financial_prediction_snapshot"]
    actual = result["actual_claim_facts"]
    action = summary["best_action"]
    supported = result["supported_financial_opportunities"]
    primary = supported[0] if supported else None
    predicted_paid = snapshot["predicted_provider_payment"]

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
    )
    with _LOCK:
        cached = _ANALYSIS_CACHE.get(cache_key)
        if cached:
            return cached
    rag = retrieve_evidence(database, result, "provider financial prediction explanation")
    result = {**result, "rag": rag, "rag_evidence": rag["retrieved_chunks"]}
    plain_language = _plain_language_explanation(result)
    authoritative = plain_language["summary"]
    narrative, source, model = _groq_exact_answer(authoritative, result, rag)
    explanation = {
        **plain_language,
        "summary": narrative,
        "source": source,
        "system_instruction": SYSTEM_INSTRUCTION,
    }
    response = {
        **result,
        "configured": bool(os.getenv("GROQ_API_KEY", "").strip()),
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
    )
    with _LOCK:
        cached = _CHAT_CACHE.get(cache_key)
        if cached:
            return {**cached, "conversation_id": conversation_id, "cached": True}
    rag = retrieve_evidence(database, result, question)
    result = {**result, "rag": rag, "rag_evidence": rag["retrieved_chunks"]}
    authoritative = _authoritative_answer(result, question)
    answer, source, model = _groq_exact_answer(authoritative, result, rag)
    response = {
        "answer": answer,
        "financial_explanation": _financial_explanation(result),
        "claim_id": result["claim_id"],
        "episode_id": result["episode_id"],
        "conversation_id": conversation_id,
        "model": model,
        "prompt_version": GROQ_PROMPT_VERSION,
        "explanation_source": source,
        "evidence_claim_ids": _financial_explanation(result)["evidence_claim_ids"],
        "rag": rag,
        "limitations": result["limitations"],
        "suggested_questions": SUGGESTED_QUESTIONS,
        "cached": False,
    }
    with _LOCK:
        _CHAT_CACHE[cache_key] = {key: value for key, value in response.items() if key != "conversation_id"}
    return response
