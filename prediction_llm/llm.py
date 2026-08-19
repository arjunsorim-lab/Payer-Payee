"""Local Ollama narrative layer constrained to deterministic analytics evidence."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


class GroundedNarrator:
    def __init__(self) -> None:
        self.base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model = os.environ.get("OLLAMA_MODEL", "gemma3:1b")

    def _request(self, path: str, payload: dict[str, Any] | None = None, timeout: int = 90) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def status(self) -> dict[str, Any]:
        try:
            tags = self._request("/api/tags", timeout=3)
            models = [item.get("name") for item in tags.get("models", [])]
            return {"available": self.model in models, "model": self.model, "installed_models": models}
        except (OSError, urllib.error.URLError, ValueError, TimeoutError) as error:
            return {"available": False, "model": self.model, "error": str(error)}

    def explain(self, analysis: dict[str, Any], question: str | None = None) -> dict[str, Any]:
        system_prompt = """
You are a claims analytics explanation assistant. Use ONLY the supplied evidence brief.

Required behavior:
- Write one concise overview of no more than 75 words.
- Describe ICD-10 as a recorded diagnosis code and CPT as a recorded procedure/service code.
- Mention at least one Claim_ID when the evidence brief supplies one.
- State when the available records are insufficient for the user's question.
- Never provide clinical advice, diagnose, recommend medication/treatment, infer why care occurred,
  assess medical necessity, or claim that one treatment caused another event.
- Never invent a claim, field, code, amount, date, provider, relationship, or clinical rationale.
- If the question asks for an unsupported clinical explanation, explicitly say the claims data is insufficient.

Return only valid JSON with one key: overview (string).
""".strip()
        deterministic = self.fallback(analysis)
        evidence = {
            "facts": deterministic["facts"],
            "patterns": deterministic["patterns"],
            "insufficient_evidence": deterministic["insufficient_evidence"],
            "evidence_claim_ids": deterministic["evidence_claim_ids"][:10],
        }
        prompt = {
            "user_question": question or "Explain the claims analysis in concise, plain language.",
            "analytics_evidence": evidence,
        }
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": json.dumps(prompt, separators=(",", ":"), default=str),
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 120},
        }
        try:
            response = self._request("/api/generate", payload, timeout=45)
            parsed = json.loads(response.get("response", "{}"))
            if not isinstance(parsed.get("overview"), str) or not parsed["overview"].strip():
                raise ValueError("Local model response omitted the grounded overview")
            overview = parsed["overview"].strip()
            forbidden = ("caused by", "should take", "should receive", "recommend treatment", "medication failed")
            if any(phrase in overview.lower() for phrase in forbidden):
                raise ValueError("Local model response crossed a clinical-inference guardrail")
            supplied_ids = set(deterministic["evidence_claim_ids"])
            mentioned_ids = set(re.findall(r"CLM\d+", overview))
            if not mentioned_ids.issubset(supplied_ids):
                raise ValueError("Local model cited a Claim_ID outside the supplied evidence")
            anchor_id = analysis.get("claim_id")
            if anchor_id and anchor_id not in overview:
                overview = f"Claim {anchor_id}: {overview}"
            deterministic["overview"] = overview
            deterministic["model"] = self.model
            deterministic["mode"] = "local_llm_grounded"
            deterministic.pop("fallback_reason", None)
            return deterministic
        except (OSError, urllib.error.URLError, ValueError, TimeoutError, json.JSONDecodeError) as error:
            return self.fallback(analysis, reason=str(error))

    def fallback(self, analysis: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        claim_ids = sorted(
            {
                str(record.get("Claim_ID"))
                for record in analysis.get("evidence_records", [])
                if record.get("Claim_ID")
            }
        )
        facts = analysis.get("facts", {})
        if analysis.get("analysis_type") == "claim_analysis":
            target = facts.get("target_claim", {})
            overview = (
                f"Claim {target.get('Claim_ID')} records ICD-10 {target.get('ICD10_Diagnosis_Code')} and "
                f"CPT {target.get('CPT_Code')}; the result below is based only on stored claims and computed comparisons."
            )
            fact_lines = [
                f"Claim {target.get('Claim_ID')} for member {target.get('Member_ID')} has service date {target.get('Service_Date_From')}.",
                f"The recorded diagnosis code is ICD-10 {target.get('ICD10_Diagnosis_Code')} and the recorded procedure/service code is CPT {target.get('CPT_Code')}.",
                f"Recorded amounts are charge ${target.get('Charge_Amount', 0):,.2f}, allowed ${target.get('Allowed_Amount', 0):,.2f}, paid ${target.get('Paid_Amount', 0):,.2f}, and patient responsibility ${target.get('Patient_Responsibility', 0):,.2f}.",
                f"The same member has {facts.get('same_member_prior_claim_count', 0)} earlier available claim(s), including {facts.get('same_member_prior_same_CPT_count', 0)} with the same CPT code.",
            ]
        else:
            overview = (
                f"Member {analysis.get('member_id')} has {facts.get('claim_count', 0)} available claim records in the selected history."
            )
            totals = facts.get("financial_totals", {})
            date_range = facts.get("date_range", {})
            top_cpt = ", ".join(
                f"{item.get('value')} ({item.get('count')})" for item in facts.get("top_procedure_codes", [])[:3]
            ) or "none"
            top_icd = ", ".join(
                f"{item.get('value')} ({item.get('count')})" for item in facts.get("top_diagnosis_codes", [])[:3]
            ) or "none"
            fact_lines = [
                f"Member {analysis.get('member_id')} has {facts.get('claim_count', 0)} available claims from {date_range.get('from')} to {date_range.get('to')}.",
                f"Totals are charge ${totals.get('Charge_Amount', 0):,.2f}, allowed ${totals.get('Allowed_Amount', 0):,.2f}, paid ${totals.get('Paid_Amount', 0):,.2f}, and patient responsibility ${totals.get('Patient_Responsibility', 0):,.2f}.",
                f"Most frequent recorded CPT codes: {top_cpt}.",
                f"Most frequent recorded ICD-10 codes: {top_icd}.",
            ]
        return {
            "overview": overview,
            "facts": fact_lines,
            "patterns": [item.get("statement", "") for item in analysis.get("patterns", []) if item.get("statement")],
            "insufficient_evidence": analysis.get("insufficient_evidence", []),
            "evidence_claim_ids": claim_ids,
            "safety_note": analysis.get("guardrail"),
            "model": self.model,
            "mode": "deterministic_fallback",
            "fallback_reason": reason,
        }
