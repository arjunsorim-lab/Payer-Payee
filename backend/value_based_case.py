"""Deterministic same-member value-based review, independent of payer cohorts.

Claims establish chronology and spend. Recorded indicators support review, not
individual preventability. Synthetic evidence never confirms clinical savings.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import os
import re

try:
    from .intervention_plans import build_intervention_plan
except ImportError:
    from intervention_plans import build_intervention_plan

YES, NO, UNKNOWN = "YES", "NO", "INSUFFICIENT_EVIDENCE"
ALIASES = {
    "Claim_ID": ("claimId", "CLAIMID", "SOURCE_CLAIM_ID"),
    "Member_ID": ("memberId", "PATIENTID", "PATIENT"),
    "Service_Date_From": ("dos", "Service_Date", "Date", "START", "FROMDATE"),
    "ICD10_Diagnosis_Code": ("diagnosisCode", "ICD10", "Diagnosis_Code"),
    "ICD10_Diagnosis_Description": ("diagnosisDescription", "Diagnosis"),
    "CPT_Code": ("cptCode", "CPT", "PROCEDURECODE", "Procedure_Code"),
    "CPT_Description": ("cptDescription", "Service", "DESCRIPTION"),
    "Paid_Amount": ("paid",), "Allowed_Amount": ("allowed",),
    "Episode_ID": ("episodeId",),
    "Encounter_ID": ("encounterId", "ENCOUNTER", "APPOINTMENTID"),
    "Claim_Status_Description": ("status", "Claim_Status"),
    "Units": ("units", "UNITS"),
}


def _text(value):
    return "" if value is None else str(value).strip()


def _field(row, key, default=None):
    fields = row.get("workbookFields", row)
    for name in (key, *ALIASES.get(key, ())):
        if name in fields and fields[name] not in (None, ""):
            return fields[name]
    # Legacy normalization fills missing financial inputs with zero.
    # Preserve raw absence rather than mistaking that default for payment.
    if key in {"Paid_Amount", "Allowed_Amount"} and "workbookFields" in row:
        return default
    for name in (key, *ALIASES.get(key, ())):
        if row.get(name) not in (None, ""):
            return row[name]
    return default


def _flag(value):
    value = _text(value).lower()
    if value in {"y", "yes", "true", "1", "completed", "performed", "resolved"}:
        return True
    if value in {"n", "no", "false", "0", "not performed", "not completed", "unresolved", "incomplete", "missed", "ongoing"}:
        return False
    return None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _decimal(value):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if value is not None else None


def _id(row):
    return _text(_field(row, "Claim_ID"))


def _member(row):
    return _text(_field(row, "Member_ID"))


def _date(row):
    return _day(_field(row, "Service_Date_From"))


def _episode_id(row):
    return _text(_field(row, "Episode_ID"))


def _code(row):
    return _text(_field(row, "ICD10_Diagnosis_Code")).upper()


def _family(row):
    family = _text(_field(row, "ICD10_Family")).upper() or _code(row).split(".")[0]
    return family if re.fullmatch(r"[A-Z][0-9][A-Z0-9]", family) else ""


def _historical(row):
    return _flag(_field(row, "Is_Historical_Reference_Record")) is True


def _order(row):
    return (_date(row) or date.max, _id(row))


def _settings(database, overrides=None):
    config = {
        "episode_window_days": int(os.getenv("VBC_EPISODE_WINDOW_DAYS", "120")),
        "reference_lookback_days": int(os.getenv("VBC_REFERENCE_LOOKBACK_DAYS", "120")),
        "review_groups": json.loads(os.getenv("VBC_REVIEW_GROUPS_JSON", "[]")),
        "require_first_documented_visit": True,
    }
    config.update(getattr(database, "value_based_config", {}) or {})
    config.update(overrides or {})
    for key in ("episode_window_days", "reference_lookback_days"):
        config[key] = int(config[key])
        if config[key] < 1:
            raise ValueError(f"{key} must be positive")
    for group in config["review_groups"]:
        if _flag(group.get("validated")) is not True:
            raise ValueError("Value-based review groups must explicitly be validated")
        if not group.get("code_prefixes") or any(
            not re.fullmatch(r"[A-Z][0-9][A-Z0-9](?:\.[A-Z0-9]+)?", prefix)
            for prefix in group["code_prefixes"]
        ):
            raise ValueError("Review groups require diagnosis prefixes at family level or narrower; ICD chapters are not allowed")
    return config


def _group(left, right, config):
    for group in config["review_groups"]:
        prefixes = tuple(group["code_prefixes"])
        if _code(left).startswith(prefixes) and _code(right).startswith(prefixes):
            return group.get("id") or group.get("label") or ", ".join(prefixes)
    return None


def _relationship(reference, row, config):
    """Conflicting explicit episodes veto all weaker relationship fallbacks."""
    if not _member(reference) or _member(reference) != _member(row):
        return None
    a, b = _episode_id(reference), _episode_id(row)
    if a and b:
        return "explicit_episode_id" if a == b else None
    if _flag(_field(row, "Related_Claim_Flag")) is False:
        return None
    if not _date(reference) or not _date(row):
        return None
    if abs((_date(row) - _date(reference)).days) > config["episode_window_days"]:
        return None
    target = _text(_field(row, "Related_Claim_ID") or _field(row, "Reference_Claim_ID"))
    if target == _id(reference) and _flag(_field(row, "Related_Claim_Flag")) is True:
        return "explicit_related_claim_link"
    group = _group(reference, row, config)
    same_family = bool(_family(reference)) and _family(reference) == _family(row)
    # A bare Y flag cannot link arbitrary diseases in the same member.
    if _flag(_field(row, "Related_Claim_Flag")) is True and (group or same_family):
        return "related_flag_with_diagnosis_scope"
    if group:
        return "validated_review_group_with_time_window"
    if same_family:
        return "same_diagnosis_family_with_time_window"
    return None


def _tables(database, role):
    return tuple(getattr(database, "evidence_tables", {}).get(role, ()))


def _linked(row, claim):
    member = _member(row)
    if member and member != _member(claim):
        return False
    if _id(row):
        return _id(row) == _id(claim)
    encounter = _text(_field(row, "Encounter_ID"))
    if encounter and _field(claim, "Encounter_ID"):
        return encounter == _text(_field(claim, "Encounter_ID"))
    return bool(member and _date(row) and _date(row) == _date(claim))


def _synthetic(database, row):
    explicit = _flag(_field(row, "Synthetic_Flag"))
    if explicit is not None:
        return explicit
    return bool(getattr(database, "report", {}).get("synthetic"))


def _evidence_values(database, row, key):
    result = []
    sources = [row, *[item for role in ("conditions", "procedures", "encounters")
                     for item in _tables(database, role) if _linked(item, row)]]
    for source in sources:
        value = _field(source, key)
        if value not in (None, ""):
            result.append((value, f"{source.get('_source_sheet', 'claims')}:{_id(row)}:{key}={value}"))
    return result


def _office(row):
    service = _text(_field(row, "CPT_Description")).lower()
    category = _text(_field(row, "Service_Category")).lower()
    return category in {"office evaluation", "office visit", "evaluation and management"} or any(
        word in service for word in ("office visit", "office evaluation", "consultation", "evaluation and management")
    )


def _diagnostic(row):
    service = _text(_field(row, "CPT_Description")).lower()
    category = _text(_field(row, "Service_Category")).lower()
    return category in {"laboratory", "imaging", "diagnostic"} or any(
        word in service for word in ("test", "culture", "urinalysis", "imaging", "laboratory", "x-ray", "monitoring", "screening")
    )


def _status(row):
    return " ".join(_text(_field(row, name)).lower() for name in (
        "Claim_Status_Description", "Final_Adjudication_Status", "Payment_Status"))


def _adjudication_exclusions(row):
    reasons = []
    if _historical(row):
        reasons.append("Historical reference-only row")
    for name in ("Duplicate_Claim_Flag", "Reversal_Flag", "Reversed_Flag", "Superseded_Flag"):
        if _flag(_field(row, name)) is True:
            reasons.append(f"{name}=Y")
    if row.get("_superseded") or _field(row, "Superseded_By_Claim_ID"):
        reasons.append("Superseded by a corrected claim")
    if row.get("_ambiguous_duplicate"):
        reasons.append("Conflicting duplicate claim rows require reconciliation")
    if any(word in _status(row) for word in ("reversed", "reversal", "void", "superseded", "denied", "duplicate")):
        reasons.append("Claim adjudication excludes it from payer-spend review")
    if _text(_field(row, "Claim_Frequency_Code")) == "8":
        reasons.append("Void claim frequency")
    corrected = any(_flag(_field(row, key)) is True for key in ("Corrected_Claim_Flag", "Corrected_Claim_Indicator"))
    if corrected and "final" not in _status(row):
        reasons.append("Corrected claim lacks final adjudication")
    return reasons


def _prepare(database):
    grouped = defaultdict(list)
    for row in getattr(database, "claims", database.selectable_claims):
        if _id(row) and _member(row):
            grouped[(_member(row), _id(row))].append(row)
    result, issues = [], []
    for (member, claim_id), copies in grouped.items():
        fingerprints = {json.dumps(row.get("workbookFields", row), sort_keys=True, default=str) for row in copies}
        chosen = dict(sorted(copies, key=lambda row: json.dumps(row.get("workbookFields", row), sort_keys=True, default=str))[0])
        if len(copies) > 1:
            issues.append({"claim_id": claim_id, "member_id": member, "duplicate_row_count": len(copies) - 1,
                           "reason": "Identical duplicate rows counted once" if len(fingerprints) == 1 else "Conflicting duplicate rows excluded pending reconciliation"})
            chosen["_ambiguous_duplicate"] = len(fingerprints) > 1
        result.append(chosen)
    superseded = {(_member(row), _text(_field(row, name))) for row in result
                  for name in ("Replaces_Claim_ID", "Original_Claim_ID", "Corrected_From_Claim_ID")
                  if _field(row, name) and "final" in _status(row) and not _adjudication_exclusions(row)}
    for row in result:
        row["_superseded"] = (_member(row), _id(row)) in superseded
    return sorted(result, key=_order), issues


def _payment(database, row):
    canonical = _decimal(_field(row, "Paid_Amount"))
    result = {"payment_verified": False, "payment_source": "unverified", "paid_amount": None,
              "allowed_amount": _money(_decimal(_field(row, "Allowed_Amount"))),
              "claim_status": _text(_field(row, "Claim_Status_Description")),
              "final_adjudication_status": _text(_field(row, "Final_Adjudication_Status")),
              "payment_evidence": [], "payment_limitations": []}
    remits = [item for item in _tables(database, "remittances") if _linked(item, row)]
    if remits:
        # A line/payer group is a final snapshot, not an additive sequence of
        # versions. Conflicting snapshots require explicit supersession data.
        groups = defaultdict(list)
        for item in remits:
            groups[(_text(item.get("Payer_ID")), _text(item.get("Claim_Line_ID")))].append(item)
        finals = []
        for items in groups.values():
            unique = {json.dumps({k: v for k, v in item.items() if not k.startswith("_source")}, sort_keys=True, default=str): item for item in items}
            active = [item for item in unique.values() if _flag(item.get("Superseded_Flag")) is not True and not item.get("Superseded_By_Remittance_ID")]
            if len(active) != 1 or "final" not in _status(active[0]) or _adjudication_exclusions(active[0]):
                result["payment_limitations"].append("Remittance is nonfinal, reversed, or contains conflicting line/payer snapshots")
                return result
            value = _decimal(active[0].get("Paid_Amount"))
            if value is None or value < 0:
                result["payment_limitations"].append("Final remittance Paid_Amount is missing or invalid")
                return result
            finals.append(active[0])
        if any(not line for payer, line in groups) and any(line for payer, line in groups):
            result["payment_limitations"].append("Mixed claim totals and line amounts cannot be added")
            return result
        paid = sum((_decimal(item["Paid_Amount"]) for item in finals), Decimal(0))
        allowed = [_decimal(item.get("Allowed_Amount")) for item in finals]
        result.update(payment_verified=True, payment_source="final_adjudicated_remittance",
                      paid_amount=_money(paid), final_adjudication_status="Final Paid",
                      allowed_amount=_money(sum(allowed, Decimal(0))) if all(v is not None for v in allowed) else None)
        result["payment_evidence"] = [{key: item.get(key) for key in (
            "Claim_Line_ID", "Payer_ID", "Payer_Name", "Paid_Amount", "Payment_Date", "Final_Adjudication_Status", "_source_sheet", "_source_row")}
            for item in finals]
        if canonical is not None and _money(canonical) != result["paid_amount"]:
            result["payment_limitations"].append("Canonical Paid_Amount differs from final remittance; final remittance used")
    elif canonical is not None and canonical >= 0:
        result.update(payment_verified=True, payment_source="canonical_claim_paid_amount", paid_amount=_money(canonical))
        result["payment_evidence"] = [f"Claims:{_id(row)}:Paid_Amount={canonical}"]
    else:
        result["payment_limitations"].append("Paid_Amount is missing or invalid; charges and other financial fields are not payment")
    return result


def _summary(database, row):
    if row is None:
        return None
    return {"claim_id": _id(row), "member_id": _member(row),
            "service_date": _date(row).isoformat() if _date(row) else None,
            "icd10": _code(row), "icd10_family": _family(row),
            "diagnosis_description": _text(_field(row, "ICD10_Diagnosis_Description")),
            "cpt": _text(_field(row, "CPT_Code")), "procedure_description": _text(_field(row, "CPT_Description")),
            "units": _money(_decimal(_field(row, "Units"))), "episode_id": _episode_id(row),
            "synthetic": _synthetic(database, row), "is_historical_reference": _historical(row),
            **_payment(database, row)}


def _service_review(database, reference, rows):
    encounter = _field(reference, "Encounter_ID")
    linked_claims = [row for row in rows if _member(row) == _member(reference) and (
        _id(row) == _id(reference) or (encounter and _field(row, "Encounter_ID") == encounter))]
    services = []
    sources_by_role = [("claims", linked_claims), *[(role, [r for r in _tables(database, role) if _linked(r, reference)])
                       for role in ("claim_lines", "procedures", "encounters", "labs", "observations", "medications")]]
    for source, sources in sources_by_role:
        for row in sources:
            description = _text(_field(row, "CPT_Description") or row.get("DESCRIPTION") or row.get("Medication"))
            if not description and not _text(_field(row, "CPT_Code") or row.get("CODE")):
                continue
            category = _text(row.get("CATEGORY") or row.get("Category")).lower()
            kind = "medication" if source == "medications" else "diagnostic" if source == "labs" or category == "laboratory" or _diagnostic(row) else "observation" if source == "observations" else "office_evaluation" if _office(row) else "other"
            services.append({"cpt": _text(_field(row, "CPT_Code") or row.get("CODE")), "service": description,
                             "kind": kind, "evidence_source": row.get("_source_sheet", source),
                             "source_claim_ids": [_id(row) or _id(reference)], "source_row": row.get("_source_row")})
    services = list({json.dumps(item, sort_keys=True): item for item in services}.values())
    additional = [item for item in services if item["kind"] in {"medication", "diagnostic", "other"}]
    complete = _flag(_field(reference, "Reference_Service_Complete")) is True or (
        _synthetic(database, reference) and any(_linked(r, reference) for r in _tables(database, "procedures")))
    only = False if additional else True if complete and services and all(item["kind"] in {"office_evaluation", "observation"} for item in services) else None
    return {"services_at_reference": services,
            "medications_at_reference": [s for s in services if s["kind"] == "medication"],
            "diagnostic_tests_at_reference": [s for s in services if s["kind"] == "diagnostic"],
            "office_evaluation_only": only, "service_evidence_complete": complete,
            "evidence": ["All linked services in the configured source were inspected"] + ([] if complete else ["Complete encounter service coverage is not established"])}


def _worsening(database, row, prior_rows=()):
    evidence, positive, negative = [], False, False
    for value, source in _evidence_values(database, row, "Progression_Flag"):
        if _flag(value) is not None:
            positive |= _flag(value) is True
            negative |= _flag(value) is False
            evidence.append(source)
    for value, source in _evidence_values(database, row, "Treatment_Outcome"):
        normalized = _text(value).lower()
        if normalized in {"worsened", "worsening", "deteriorated", "progressed", "escalated"}:
            positive = True
            evidence.append(source)
        elif normalized in {"resolved", "improved", "recovered"}:
            negative = True
            evidence.append(source)
    conflicts = [r for r in prior_rows if _code(r) and _code(r) == _code(row)]
    if conflicts:
        evidence.append("Outcome diagnosis was already recorded before the reference: " + ", ".join(_id(r) for r in conflicts))
    supported = None if positive and negative else True if positive else False if negative else None
    return {"worsening_supported": supported, "evidence": evidence or ["A changed diagnosis or billed test alone does not establish worsening"],
            "new_diagnosis_supported": False if conflicts else None,
            "prior_outcome_diagnosis_claim_ids": [_id(r) for r in conflicts],
            "evidence_scope": "synthetic demonstration" if _synthetic(database, row) else "recorded evidence; causation not established"}


def _select_reference(database, selected, rows, config):
    if _adjudication_exclusions(selected):
        return None, "unavailable", "Selected claim requires adjudication reconciliation before it can anchor a case", []
    if _flag(_field(selected, "Reference_Claim_Flag")) is True:
        return selected, "anchor_first", "Selected claim explicitly has Reference_Claim_Flag=Y", ["Explicit reference flag"]
    earlier = [r for r in rows if _date(r) and _date(selected) and _date(r) < _date(selected)
               and _relationship(r, selected, config) and not _adjudication_exclusions(r)]
    explicit = [r for r in earlier if _flag(_field(r, "Reference_Claim_Flag")) is True]
    if explicit:
        reference = sorted(explicit, key=lambda r: (-int(_episode_id(r) == _episode_id(selected) and bool(_episode_id(r))), -_date(r).toordinal(), _id(r)))[0]
        return reference, "backward_compatible", "Earlier explicit reference in the selected member's episode", ["Same member", "Explicit reference flag"]
    earlier = [r for r in earlier if (_date(selected) - _date(r)).days <= config["reference_lookback_days"]
               and _flag(_field(r, "Reference_Claim_Flag")) is not False]
    if earlier:
        reference = sorted(earlier, key=lambda r: (-int(_office(r)), -_date(r).toordinal(), _id(r)))[0]
        return reference, "backward_compatible", "Earlier same-member reference selected using episode scope, evaluation service and recency", ["Same member", _relationship(reference, selected, config)]
    if _flag(_field(selected, "Reference_Claim_Flag")) is not False and (
        _office(selected) or _flag(_field(selected, "Intervention_Performed")) is False or
        _flag(_field(selected, "Follow_Up_Completed")) is False
    ):
        return selected, "anchor_first", "Selected evaluation or recorded incomplete care is the earliest supported episode reference", ["Deterministic reference rule", "No earlier qualifying reference in the episode window"]
    return None, "unavailable", "No supported same-member reference claim was identified", []


def _interventions(database, reference, episode_rows, history, config, service_review):
    present = {s["cpt"] for s in service_review["services_at_reference"] if s["cpt"]}
    maps = []
    for row in _tables(database, "interventions"):
        target = _text(_field(row, "Reference_Claim_ID") or row.get("Reference claim"))
        if target == _id(reference) or (
            not target and _field(row, "ICD10_Family") and _field(row, "ICD10_Family") == _family(reference)
        ) or (not target and any(g.get("id") == _field(row, "Review_Group_ID") and
              _code(reference).startswith(tuple(g["code_prefixes"])) for g in config["review_groups"])):
            maps.append(row)
    later = [r for r in episode_rows if _date(r) and _date(r) > _date(reference)]
    earlier = [r for r in history if _date(r) and _date(r) < _date(reference) and
               ((_family(r) == _family(reference) and bool(_family(reference))) or _group(reference, r, config))]
    candidates = defaultdict(list)
    for row in (*later, *earlier):
        cpt = _text(_field(row, "CPT_Code"))
        description = _text(_field(row, "CPT_Description"))
        mapped = any(_text(_field(m, "CPT_Code")) == cpt for m in maps)
        supported_service = mapped or _diagnostic(row) or "follow-up" in description.lower() or "follow up" in description.lower() or _flag(_field(row, "Intervention_Performed")) is True
        if cpt and description and cpt not in present and supported_service and not _adjudication_exclusions(row):
            candidates[cpt].append(row)
    results = []
    for cpt, sources in sorted(candidates.items()):
        mapped = next((m for m in maps if _text(_field(m, "CPT_Code")) == cpt), None)
        service = _text(_field(sources[0], "CPT_Description"))
        action = _text(mapped.get("Proposed_Earlier_Action") or mapped.get("Synthetic earlier action")) if mapped else ""
        results.append({"cpt": cpt, "service": service,
                        "proposed_earlier_action": action or f"Review whether {service} should be scheduled earlier after the reference encounter",
                        "evidence_source": mapped.get("_source_sheet", "configured intervention mapping") if mapped else "same-member claim history",
                        "source_claim_ids": sorted({_id(s) for s in sources}), "hypothesis_only": True})
    return results


def _eligibility(database, row):
    exclusions = _adjudication_exclusions(row)
    reason = _text(_field(row, "Repeat_Visit_Reason")).lower()
    if _flag(_field(row, "Related_Claim_Flag")) is False:
        exclusions.append("Explicitly marked unrelated")
    planned = _flag(_field(row, "Planned_Follow_Up_Flag")) is True or reason in {
        "planned follow-up", "planned follow up", "scheduled routine follow-up", "routine follow-up", "scheduled follow-up", "routine monitoring"}
    if planned:
        exclusions.append("Planned or routine follow-up")
    evidence = []
    avoidable = _flag(_field(row, "Avoidable_Flag"))
    if _synthetic(database, row):
        if avoidable is True:
            evidence.append("Synthetic Avoidable_Flag=Y")
        else:
            exclusions.append("Synthetic claim has no affirmative Avoidable_Flag")
    else:
        if avoidable is False:
            exclusions.append("Avoidable_Flag=N")
        for key in ("Condition_Resolved", "Follow_Up_Completed", "Intervention_Performed"):
            if _flag(_field(row, key)) is False:
                evidence.append(f"{key}={_field(row, key)}")
        if _text(_field(row, "Treatment_Outcome")).lower() in {"worsened", "worsening", "no change", "unchanged", "persistent"}:
            evidence.append(f"Treatment_Outcome={_field(row, 'Treatment_Outcome')}")
        if reason in {"incomplete prior treatment", "persistent symptoms", "symptom recurrence", "unplanned return", "incomplete treatment"}:
            evidence.append(f"Repeat_Visit_Reason={reason}")
        if not evidence:
            exclusions.append("No supported avoidability review indicator")
    return {"eligible_for_review": bool(evidence) and not exclusions,
            "avoidable_evidence": evidence, "exclusion_reasons": exclusions}


def _requirement(value, evidence):
    return {"status": YES if value is True else NO if value is False else UNKNOWN, "evidence": evidence}


def build_value_based_case_for_claim(database, claim_number, *, config=None, _prepared=None):
    """Evaluate a selected anchor or an older reference for a selected outcome.

    Existing UI aliases are retained. Only unique eligible later claims with
    verified payments enter the exposure; no different-member comparisons.
    """
    selected = database.find_claim(claim_number, selectable_only=True)
    if not selected or _historical(selected):
        raise KeyError(f"Selectable claim not found: {claim_number}")
    settings = _settings(database, config)
    prepared, duplicate_issues = _prepared or _prepare(database)
    rows = [r for r in prepared if _member(r) == _member(selected) and not _historical(r)]
    selected = next((r for r in rows if _id(r) == _id(selected)), selected)
    if not _date(selected):
        raise ValueError("Selected claim has a missing or invalid service date")
    selected_fields = selected.get("workbookFields", {})
    outcome_evidence = {
        "claim_id": _id(selected),
        "service_date": _date(selected).isoformat(),
        "procedure": _text(_field(selected, "CPT_Description")),
        "condition_resolved": _text(selected_fields.get("Condition_Resolved")) or "Not recorded",
        "treatment_outcome": _text(selected_fields.get("Treatment_Outcome")) or "Not recorded",
        "follow_up_completed": _text(selected_fields.get("Follow_Up_Completed")) or "Not recorded",
        "outcome_claim_flag": _text(selected_fields.get("Outcome_Claim_Flag")) or "Not recorded",
        "related_claim_flag": _text(selected_fields.get("Related_Claim_Flag")) or "Not recorded",
        "synthetic": _synthetic(database, selected),
        "status": "INSUFFICIENT_EVIDENCE",
        "conclusion": "Claims data does not prove that this service cured a disease or caused an improved outcome.",
    }
    reference, mode, selection_reason, selection_evidence = _select_reference(database, selected, rows, settings)
    base = {"available": False, "status": "No claims-based rectification scenario", "reason": selection_reason,
            "selected_claim": _summary(database, selected), "selection_mode": mode,
            "reference_claim": _summary(database, reference), "reference_selection_reason": selection_reason,
            "reference_evidence": selection_evidence, "prediction_claim": None,
            "reference_evaluation": {}, "outcome_evaluation": {}, "episode": {},
            "earlier_intervention_opportunities": [], "avoidable_repetitive_claims": [], "claims_included": [],
            "calculation": {"available": False, "basis": "Paid_Amount", "eligible_claim_count": 0,
                            "included_claim_count": 0, "potentially_avoidable_repeat_spend_exposure": 0.0,
                            "potential_payer_spend_for_review": 0.0, "predicted_payer_avoidable_spend": None,
                            "confirmed_savings": 0.0, "present_claim_paid": None, "later_related_paid": 0.0,
                            "formula": "Sum unique eligible later claims with verified Paid_Amount",
                            "reason": selection_reason},
            "outcome_evidence": outcome_evidence,
            "requirements_verification": {}, "clinical_review_required": True,
            "data_limitations": ["This is a retrospective review of observed utilization. Earlier interventions remain hypotheses; exposure is not confirmed savings."]}
    names = ("reference_claim_identified", "first_documented_relevant_visit", "reference_service_verified",
             "outcome_claim_identified", "worsening_supported", "earlier_intervention_opportunity",
             "same_episode_supported", "payments_verified", "avoidable_claims_supported")
    if reference is None:
        base["requirements_verification"] = {name: _requirement(False if name in {"reference_claim_identified", "outcome_claim_identified"} else None, [selection_reason]) for name in names}
        return base
    episode_rows = [r for r in rows if _id(r) == _id(reference) or _relationship(reference, r, settings)]
    dated = [r for r in episode_rows if _date(r)]
    later = [r for r in dated if _date(r) > _date(reference)]
    prior = [r for r in rows if _date(r) and _date(r) < _date(reference)]
    scope_families = {_family(r) for r in episode_rows if _family(r)}
    relevant_prior = [r for r in prior if _family(r) in scope_families or _group(reference, r, settings)]
    condition_prior = [r for r in _tables(database, "conditions") if _member(r) == _member(reference)
                       and _date(r) and _date(r) < _date(reference) and
                       (_family(r) in scope_families or _group(reference, r, settings))]
    undated_conditions = [r for r in _tables(database, "conditions") if _member(r) == _member(reference)
                          and not _date(r) and (_family(r) in scope_families or _group(reference, r, settings))]
    first = False if relevant_prior or condition_prior else None if any(not _date(r) for r in rows) or undated_conditions else True
    first_episode = False if any(_date(r) < _date(reference) for r in dated) else None if len(dated) != len(episode_rows) else True
    service_review = _service_review(database, reference, rows)
    outcome_candidates = [r for r in later if not _adjudication_exclusions(r)]

    def outcome_rank(row):
        return (-int(_flag(_field(row, "Outcome_Claim_Flag")) is True),
                -int(_worsening(database, row)["worsening_supported"] is True),
                -int(_episode_id(row) == _episode_id(reference) and bool(_episode_id(reference))),
                -int(_diagnostic(row)), _date(row), _id(row))

    outcome_candidates.sort(key=outcome_rank)
    outcome = outcome_candidates[0] if outcome_candidates else None
    days = (_date(outcome) - _date(reference)).days if outcome else None
    outcome_evaluation = _worsening(database, outcome, prior + condition_prior) if outcome else {
        "worsening_supported": None, "evidence": ["No eligible later outcome claim exists"]}
    outcome_reason = "No eligible later same-member outcome claim was found"
    if outcome:
        outcome_reason = "Explicit outcome flag ranked first" if _flag(_field(outcome, "Outcome_Claim_Flag")) is True else "Best-supported later related utilization, ranked by recorded worsening, episode link, diagnostic service, date and claim ID"
    interventions = _interventions(database, reference, episode_rows, prior, settings, service_review)
    assessed = []
    for row in later:
        item = {**_summary(database, row), **_eligibility(database, row), "episode_link_method": _relationship(reference, row, settings)}
        item["included_in_calculation"] = item["eligible_for_review"] and item["payment_verified"]
        if item["eligible_for_review"] and not item["payment_verified"]:
            item["exclusion_reasons"].append("Payment is not verified; excluded from the financial total")
        item["inclusion_reason"] = "; ".join(item["avoidable_evidence"]) if item["included_in_calculation"] else "; ".join(item["exclusion_reasons"])
        assessed.append(item)
    included = [r for r in assessed if r["included_in_calculation"]]
    eligible = [r for r in assessed if r["eligible_for_review"]]
    total = sum((Decimal(str(r["paid_amount"])) for r in included), Decimal(0))
    outcome_paid = next((Decimal(str(r["paid_amount"])) for r in included if outcome and r["claim_id"] == _id(outcome)), Decimal(0))
    methods = sorted({_relationship(reference, r, settings) for r in episode_rows if _id(r) != _id(reference)})
    payments = [base["reference_claim"], *assessed]
    payments_verified = all(r["payment_verified"] for r in payments) if assessed else None
    first_evidence = ["Prior related claims: " + ", ".join(_id(r) for r in relevant_prior + condition_prior)] if relevant_prior or condition_prior else ["No earlier related record found in the configured member history; lifetime coverage is not established"]
    verification = {
        "reference_claim_identified": _requirement(True, [selection_reason]),
        "first_documented_relevant_visit": _requirement(first, first_evidence),
        "first_relevant_visit_in_episode": _requirement(first_episode, ["Calculated separately within the selected episode scope"]),
        "reference_service_verified": _requirement(True if service_review["services_at_reference"] else None, service_review["evidence"]),
        "office_evaluation_only": _requirement(service_review["office_evaluation_only"], service_review["evidence"]),
        "outcome_claim_identified": _requirement(bool(outcome), [outcome_reason]),
        "worsening_supported": _requirement(outcome_evaluation["worsening_supported"], outcome_evaluation["evidence"]),
        "earlier_intervention_opportunity": _requirement(bool(interventions), ["Source-backed services available for earlier-intervention review"] if interventions else ["No supported service absent from the reference encounter was found"]),
        "same_episode_supported": _requirement(True if later else None, methods or ["No later claim has sufficient episode linkage"]),
        "payments_verified": _requirement(True if payments_verified else None, ["Reference and all related later claims have verified payer payments"] if payments_verified else ["Missing payment evidence or no related later claims"]),
        "avoidable_claims_supported": _requirement(bool(eligible), [f"{len(eligible)} later claims have review indicators; individual preventability is not established"]),
    }
    calculation_reason = f"{len(included)} unique eligible claims with verified payment included."
    if not included:
        calculation_reason = "No later claim has both supported review eligibility and verified Paid_Amount; exposure is zero."
    elif len(included) != len(eligible):
        calculation_reason += " This is a partial total because some eligible payments are unverified."
    limits = base["data_limitations"]
    if _synthetic(database, reference):
        limits.append("Synthetic/illustrative evidence is used only to test the workflow; recorded progression and avoidability flags are not real clinical evidence.")
    if first is True:
        limits.append("First documented means first in the supplied history, not proof of a first-ever condition.")
    if not service_review["service_evidence_complete"]:
        limits.append("Complete reference-encounter service coverage is not established; missing services do not prove absence of care.")
    if methods and "explicit_episode_id" not in methods:
        limits.append("Episode membership uses a configured bounded relationship rule; it is weaker than explicit episode linkage.")
    for item in [base["reference_claim"], *assessed]:
        limits.extend(f"{item['claim_id']}: {message}" for message in item["payment_limitations"])
    base.update({
        "available": bool(outcome), "status": "Data-pattern review candidate" if outcome else "No claims-based rectification scenario",
        "reason": outcome_reason, "prediction_claim": _summary(database, outcome),
        "reference_selection": {"source": "same patient", "relationship": methods[0] if methods else "reference only",
                                "method": mode, "reason": selection_reason, "evidence": selection_evidence,
                                "days_before_prediction": days},
        "reference_evaluation": {**service_review, "first_documented_relevant_visit": first,
                                 "first_relevant_visit_in_episode": first_episode,
                                 "prior_related_claims": [_summary(database, r) for r in relevant_prior],
                                 "prior_related_conditions": condition_prior, "first_visit_evidence": first_evidence},
        "outcome_selection_reason": outcome_reason, "days_after_reference": days,
        "outcome_evaluation": {**outcome_evaluation, "days_after_reference": days,
                               "description": "Recorded worsening" if outcome_evaluation["worsening_supported"] else "Subsequent utilization" if outcome else "No later outcome",
                               "candidate_claim_ids": [_id(r) for r in outcome_candidates]},
        "episode": {"episode_id": _episode_id(reference) or None, "episode_start": min(_date(r) for r in dated).isoformat(),
                    "episode_end": max(_date(r) for r in dated).isoformat(), "link_method": ", ".join(methods) or "reference_only",
                    "claim_count": len(episode_rows), "episode_claims": [_summary(database, r) for r in episode_rows],
                    "fallback_window_days": settings["episode_window_days"]},
        "earlier_intervention_opportunities": interventions,
        "intervention_plan": build_intervention_plan(reference),
        "avoidable_repetitive_claims": assessed, "claims_included": included,
        "calculation": {**base["calculation"], "available": bool(included), "eligible_claim_count": len(eligible),
                        "included_claim_count": len(included), "unverified_eligible_claim_count": len(eligible) - len(included),
                        "complete": len(eligible) == len(included), "potentially_avoidable_repeat_spend_exposure": _money(total),
                        "potential_payer_spend_for_review": _money(total), "present_claim_paid": _money(outcome_paid),
                        "later_related_paid": _money(total - outcome_paid), "reason": calculation_reason},
        "requirements_verification": verification,
        "duplicate_review": [item for item in duplicate_issues if item["member_id"] == _member(reference)],
    })
    return base


def discover_value_based_cases(database, *, limit=20, config=None):
    """Rank candidates internally; no new page/server or different-member sum."""
    settings = _settings(database, config)
    prepared = _prepare(database)
    results, seen = [], set()
    for row in prepared[0]:
        if _historical(row) or not _date(row) or _adjudication_exclusions(row):
            continue
        case = build_value_based_case_for_claim(database, _id(row), config=settings, _prepared=prepared)
        reference = case.get("reference_claim")
        if not reference or not case["available"]:
            continue
        identity = (reference["member_id"], reference["claim_id"])
        if identity in seen:
            continue
        seen.add(identity)
        checks = case["requirements_verification"]
        reasons = [name for name, result in checks.items() if result["status"] == YES]
        score = len(reasons)
        if settings["require_first_documented_visit"] and checks["first_documented_relevant_visit"]["status"] == NO:
            score -= 3
        if case["episode"]["link_method"] == "explicit_episode_id":
            score += 2
            reasons.append("Explicit episode linkage")
        if case["calculation"]["included_claim_count"] > 1:
            score += 1
            reasons.append("Multiple eligible later claims with verified payments")
        if case["duplicate_review"]:
            score -= 1
        results.append({"score": score, "ranking_evidence": reasons, "reference_claim_id": reference["claim_id"],
                        "member_id": reference["member_id"], "case": case})
    return sorted(results, key=lambda item: (-item["score"], item["member_id"], item["reference_claim_id"]))[:max(0, limit)]
