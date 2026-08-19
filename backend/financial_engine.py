"""Deterministic, category-specific provider financial result.

Every amount shown by Member 360, the Provider Prediction workspace, the scenario map,
and claim chat is produced here. Retrieval and Groq may explain these values but
never calculate or modify them.
"""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from statistics import median
from threading import RLock

import numpy as np

try:
    from .claim_patterns import (
        historical_patterns,
        select_peers,
        short_timeframe_patterns,
        similar_historical_claims,
    )
    from .avoidable_prediction import (
        MIN_HIERARCHY_PEERS,
        PRIOR_STRENGTH,
        build_predicted_avoidable_spend,
    )
    from .workbook_enrichment import (
        CALCULATION_VERSION,
        GROQ_PROMPT_VERSION,
        PREDICTION_VERSION,
        RAG_INDEX_VERSION,
        SAVINGS_VERSION,
    )
except ImportError:
    from claim_patterns import (
        historical_patterns,
        select_peers,
        short_timeframe_patterns,
        similar_historical_claims,
    )
    from avoidable_prediction import (
        MIN_HIERARCHY_PEERS,
        PRIOR_STRENGTH,
        build_predicted_avoidable_spend,
    )
    from workbook_enrichment import (
        CALCULATION_VERSION,
        GROQ_PROMPT_VERSION,
        PREDICTION_VERSION,
        RAG_INDEX_VERSION,
        SAVINGS_VERSION,
    )


_RESULT_CACHE = {}
_LOCK = RLock()
MIN_COMPARATOR_EPISODES = 5
PATIENT_BALANCE_DAYS_THRESHOLD = 30
ACTIONABLE_AGING = {"31-60", "61-90", "91+"}
ACTIONABLE_PLAN = {"broken plan", "failed plan", "defaulted", "delinquent"}
ACTIONABLE_COLLECTION = {"in collections", "collections", "collection agency"}
ACTIONABLE_AUTH = {"missing", "denied", "expired", "insufficient units"}
ACTIONABLE_REFERRAL = {"missing", "invalid", "expired"}
ACTIONABLE_APPEAL = {"not filed", "draft", "pending", "appeal pending", "ready to file"}
ACTIONABLE_RESUBMISSION = {"not submitted", "resubmitted - pending", "pending", "ready"}


def clear_financial_cache():
    with _LOCK:
        _RESULT_CACHE.clear()


def _money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value):
    return str(value or "").strip()


def _lower(value):
    return _text(value).lower()


def _yes(value):
    return _text(value).upper() in {"Y", "YES", "TRUE", "1"}


def _available(fields, *names):
    return all(name in fields and fields.get(name) not in (None, "") for name in names)


def _category(
    *,
    status,
    amount,
    reason_code,
    reason,
    formula,
    evidence_fields,
    evidence_claim_ids,
    data_source,
    confidence,
    warnings=None,
    details=None,
):
    return {
        "status": status,
        "amount": _money(amount),
        "reason_code": reason_code,
        "reason": reason,
        "formula": formula,
        "evidence_fields": list(evidence_fields),
        "evidence_claim_ids": list(dict.fromkeys(evidence_claim_ids)),
        "data_source": data_source,
        "confidence": round(float(confidence), 4),
        "validation_warnings": list(warnings or []),
        "details": details or {},
    }


def _insufficient(category, missing):
    return _category(
        status="insufficient_source_fields",
        amount=0,
        reason_code=f"{category.upper()}_SOURCE_FIELDS_MISSING",
        reason="Required workbook field(s) absent: " + ", ".join(missing) + ".",
        formula="",
        evidence_fields=[],
        evidence_claim_ids=[],
        data_source="837_Claims",
        confidence=0,
    )


def _historical_peers(database, claim):
    peers, basis = select_peers(database, claim)
    return peers, basis["peer_label"]


def _safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def _prediction_range(values, fallback):
    clean = [float(value) for value in values if value is not None]
    if not clean:
        value = _money(fallback)
        return {"value": value, "low": value, "high": value}
    return {
        "value": _money(median(clean)),
        "low": _money(np.percentile(clean, 10)),
        "high": _money(np.percentile(clean, 90)),
    }


def _date_value(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _repeat_probabilities(database, peers, cutoff):
    prior_rows = [
        row for row in database.historical_claims
        if row.get("dos", "") < cutoff
    ]
    outcomes = {30: [], 60: [], 90: []}
    for peer in peers:
        peer_date = _date_value(peer.get("dos"))
        if not peer_date:
            continue
        episode_id = peer.get("episodeId")
        member_id = peer.get("memberId")
        later_days = []
        for candidate in prior_rows:
            candidate_date = _date_value(candidate.get("dos"))
            if not candidate_date or candidate_date <= peer_date:
                continue
            related_identity = (
                candidate.get("memberId") == member_id
                or (episode_id and candidate.get("episodeId") == episode_id)
            )
            if not related_identity or not _yes(candidate["workbookFields"].get("Related_Claim_Flag")):
                continue
            later_days.append((candidate_date - peer_date).days)
        first_repeat_days = min(later_days) if later_days else None
        for horizon in outcomes:
            outcomes[horizon].append(
                1.0 if first_repeat_days is not None and first_repeat_days <= horizon else 0.0
            )
    return {
        horizon: round(sum(flags) / len(flags), 4) if flags else 0.0
        for horizon, flags in outcomes.items()
    }


def _denial_prediction(database, claim):
    cutoff = claim.get("dos") or ""
    fields = claim["workbookFields"]
    family = _text(fields.get("ICD10_Family"))
    source = getattr(database, "claims", database.historical_claims)
    prior = [row for row in source if row.get("dos", "") < cutoff]

    def denied(row):
        row_fields = row.get("workbookFields", {})
        denial_flag = _text(
            row_fields.get("Denial_Correctable_Flag")
        ).upper()
        appeal_status = _lower(row_fields.get("Appeal_Status"))
        resubmission_status = _lower(
            row_fields.get("Resubmission_Status")
        )
        denial_resolution = _lower(
            row_fields.get("Denial_Resolution")
        )
        return (
            "denied" in _lower(row.get("status"))
            or "reject" in _lower(row.get("status"))
            or denial_flag in {"Y", "N"}
            or appeal_status not in {"", "n/a", "not applicable"}
            or resubmission_status
            not in {"", "n/a", "not applicable"}
            or denial_resolution not in {"", "n/a", "not applicable"}
        )

    local = [
        row
        for row in prior
        if row.get("memberId") == claim.get("memberId")
        and row.get("cptCode") == claim.get("cptCode")
    ]
    peers, peer_basis = select_peers(database, claim, MIN_HIERARCHY_PEERS)
    peer_level = peer_basis["peer_label"]
    local_denials = sum(denied(row) for row in local)
    peer_denials = sum(denied(row) for row in peers)
    peer_rate = _safe_ratio(peer_denials, len(peers))
    probability = _safe_ratio(
        local_denials + PRIOR_STRENGTH * peer_rate,
        len(local) + PRIOR_STRENGTH,
    )
    observation_count = len(local) + len(peers)
    specificity = 1 - (peer_basis["peer_level"] - 1) / 9
    confidence = min(
        0.95,
        max(
            0.1,
            0.35
            + min(observation_count, 100) / 250
            + specificity * 0.2,
        ),
    )
    return {
        "probability": round(probability, 6),
        "local_denials": local_denials,
        "local_claim_count": len(local),
        "local_rate": round(_safe_ratio(local_denials, len(local)), 6),
        "peer_denials": peer_denials,
        "peer_count": len(peers),
        "peer_rate": round(peer_rate, 6),
        "peer_level": peer_level,
        "peer_hierarchy": peer_basis,
        "evidence_claims": list(dict.fromkeys(row["claimId"] for row in local + peers)),
        "local_numerator": local_denials,
        "local_denominator": len(local),
        "external_numerator": peer_denials,
        "external_denominator": len(peers),
        "external_rate": round(peer_rate, 6),
        "final_blended_probability": round(probability, 6),
        "prior_strength": PRIOR_STRENGTH,
        "confidence": round(confidence, 4),
    }


def _prediction(database, claim):
    peers, peer_basis = select_peers(database, claim)
    match_level = peer_basis["peer_label"]
    charge = _money(claim.get("totalCharge"))
    allowed_rates = [
        _safe_ratio(_money(row.get("allowed")), _money(row.get("totalCharge")))
        for row in peers if _money(row.get("totalCharge")) > 0
    ]
    paid_rates = [
        _safe_ratio(_money(row.get("paid")), _money(row.get("allowed")))
        for row in peers if _money(row.get("allowed")) > 0
    ]
    patient_rates = [
        _safe_ratio(_money(row.get("patientResp")), _money(row.get("allowed")))
        for row in peers if _money(row.get("allowed")) > 0
    ]
    # Never use the selected claim's adjudicated result as a prediction feature.
    allowed_rate = median(allowed_rates) if allowed_rates else 0.0
    paid_rate = median(paid_rates) if paid_rates else 0.0
    patient_rate = median(patient_rates) if patient_rates else 0.0
    allowed_candidates = [_money(charge * rate) for rate in allowed_rates]
    paid_candidates = [
        _money(
            charge
            * _safe_ratio(_money(row.get("allowed")), _money(row.get("totalCharge")))
            * _safe_ratio(_money(row.get("paid")), _money(row.get("allowed")))
        )
        for row in peers
        if _money(row.get("totalCharge")) > 0
        and _money(row.get("allowed")) > 0
    ]
    patient_candidates = [
        _money(
            charge
            * _safe_ratio(_money(row.get("allowed")), _money(row.get("totalCharge")))
            * _safe_ratio(_money(row.get("patientResp")), _money(row.get("allowed")))
        )
        for row in peers
        if _money(row.get("totalCharge")) > 0
        and _money(row.get("allowed")) > 0
    ]
    predicted_allowed_range = _prediction_range(allowed_candidates, charge * allowed_rate)
    predicted_paid_range = _prediction_range(
        paid_candidates,
        predicted_allowed_range["value"] * paid_rate,
    )
    predicted_patient_range = _prediction_range(
        patient_candidates,
        predicted_allowed_range["value"] * patient_rate,
    )
    adjustment_candidates = [_money(max(charge - value, 0)) for value in allowed_candidates]
    predicted_adjustment_range = _prediction_range(
        adjustment_candidates,
        max(charge - predicted_allowed_range["value"], 0),
    )
    common_evidence = {
        "peer_count": len(peers),
        "peer_level": match_level,
        "peer_hierarchy_level": peer_basis["peer_level"],
        "matching_dimensions": peer_basis["matching_dimensions"],
        "claim_ids_used": peer_basis["claim_ids_used"],
    }
    for prediction_range, historical_rate in (
        (predicted_allowed_range, allowed_rate),
        (predicted_paid_range, paid_rate),
        (predicted_patient_range, patient_rate),
        (predicted_adjustment_range, 1 - allowed_rate),
    ):
        prediction_range.update(common_evidence)
        prediction_range["historical_rate"] = round(historical_rate, 6)
    predicted_allowed = predicted_allowed_range["value"]
    predicted_paid = predicted_paid_range["value"]
    predicted_patient = predicted_patient_range["value"]
    denial_prediction = _denial_prediction(database, claim)
    denial_probability = denial_prediction["probability"]
    avoidable_prediction = build_predicted_avoidable_spend(
        database,
        claim,
        predicted_allowed,
        predicted_paid,
    )
    repeat = avoidable_prediction["repeat_probability"]
    sample_size = len(peers)
    confidence = min(0.95, round(0.45 + min(sample_size, 100) / 200, 4))
    return {
        "predicted_allowed": predicted_allowed,
        "predicted_paid": predicted_paid,
        "predicted_patient_responsibility": predicted_patient,
        "predicted_adjustment": predicted_adjustment_range["value"],
        "predicted_allowed_range": predicted_allowed_range,
        "predicted_paid_range": predicted_paid_range,
        "predicted_patient_responsibility_range": predicted_patient_range,
        "predicted_adjustment_range": predicted_adjustment_range,
        "denial_probability": denial_probability,
        "denial_prediction_basis": denial_prediction,
        "repeat_probability_30d": repeat["probability_30d"],
        "repeat_probability_60d": repeat["probability_60d"],
        "repeat_probability_90d": repeat["probability_90d"],
        "predicted_avoidable_spend": avoidable_prediction[
            "predicted_avoidable_spend"
        ],
        "predicted_avoidable_provider_payment": avoidable_prediction[
            "predicted_avoidable_provider_payment"
        ],
        "avoidable_prediction_basis": {
            "repeat_probability": avoidable_prediction["repeat_probability"],
            "avoidable_probability": avoidable_prediction[
                "avoidable_probability"
            ],
            "repeat_cost": avoidable_prediction["repeat_cost"],
        },
        "avoidable_formula_trace": avoidable_prediction["formula_trace"],
        "peer_sample_size": sample_size,
        "matching_level": match_level,
        "peer_hierarchy": peer_basis,
        "cutoff_date": claim.get("dos"),
        "version": PREDICTION_VERSION,
        "method": "Historical-reference peer-rate median plus Bayesian hierarchical 90-day avoidable-repeat forecast",
        "formula_trace": [
            f"Predicted allowed = charge {_money(charge):.2f} × historical median allowed rate {allowed_rate:.4f} = {predicted_allowed:.2f}.",
            f"Predicted paid = predicted allowed {predicted_allowed:.2f} × historical median paid-to-allowed rate {paid_rate:.4f} = {predicted_paid:.2f}.",
        ],
        "confidence": {
            "score": confidence,
            "level": "high" if confidence >= 0.8 else "medium" if confidence >= 0.6 else "low",
            "sample_size": sample_size,
            "reason": f"{sample_size} prior historical-reference record(s) at match level: {match_level}.",
        },
    }


def _underpayment(claim):
    fields = claim["workbookFields"]
    required = ["Expected_Reimbursement", "Paid_Amount", "Recovered_Amount", "Underpayment_Amount", "Underpayment_Flag", "Payment_Tolerance"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("underpayment", missing)
    expected = _money(fields["Expected_Reimbursement"])
    paid = _money(fields["Paid_Amount"])
    recovered = _money(fields["Recovered_Amount"])
    workbook_amount = _money(fields["Underpayment_Amount"])
    tolerance = _money(fields["Payment_Tolerance"])
    calculated = _money(expected - paid - recovered)
    validated = _money(max(calculated, 0))
    supported = _yes(fields["Underpayment_Flag"]) and workbook_amount > 0 and workbook_amount >= tolerance and validated > 0
    warnings = []
    if abs(workbook_amount - calculated) > 0.01:
        warnings.append(
            f"Workbook Underpayment_Amount {workbook_amount:.2f} differs from backend calculation {calculated:.2f}."
        )
    reason = (
        f"Expected reimbursement {expected:.2f} minus paid {paid:.2f} minus recovered {recovered:.2f} supports {validated:.2f}."
        if supported else
        f"Paid and recovered amounts do not support an underpayment above the {tolerance:.2f} tolerance."
    )
    return _category(
        status="supported" if supported else "supported_zero",
        amount=validated if supported else 0,
        reason_code="VALIDATED_UNDERPAYMENT" if supported else "UNDERPAYMENT_NOT_SUPPORTED_BY_WORKBOOK",
        reason=reason,
        formula=f"{expected:.2f} - {paid:.2f} - {recovered:.2f} = {calculated:.2f}",
        evidence_fields=required + ["Chk_Proven_Underpayment"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.98 if supported else 0.94,
        warnings=warnings,
        details={
            "workbook_underpayment_amount": workbook_amount,
            "backend_calculated_underpayment": calculated,
            "validated_underpayment": validated,
            "payment_tolerance": tolerance,
        },
    )


def _patient_balance(claim):
    fields = claim["workbookFields"]
    required = ["Patient_Responsibility", "Patient_Payment_Received", "Outstanding_Patient_Balance", "Balance_Status"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("patient_balance", missing)
    responsibility = _money(fields["Patient_Responsibility"])
    received = _money(fields["Patient_Payment_Received"])
    outstanding = _money(fields["Outstanding_Patient_Balance"])
    status = _lower(fields["Balance_Status"])
    days = int(_number(fields.get("Days_Outstanding"), 0))
    aging = _text(fields.get("Aging_Bucket"))
    plan = _lower(fields.get("Payment_Plan_Status"))
    collection = _lower(fields.get("Collection_Status"))
    actionable_signals = []
    if days > PATIENT_BALANCE_DAYS_THRESHOLD:
        actionable_signals.append(f"{days} days outstanding")
    if aging in ACTIONABLE_AGING:
        actionable_signals.append(f"aging bucket {aging}")
    if plan in ACTIONABLE_PLAN:
        actionable_signals.append(_text(fields.get("Payment_Plan_Status")))
    if collection in ACTIONABLE_COLLECTION:
        actionable_signals.append(_text(fields.get("Collection_Status")))
    if _yes(fields.get("Chk_Confirmed_Unpaid_Balance")):
        actionable_signals.append("confirmed unpaid-balance check")
    supported = outstanding > 0 and status != "paid in full" and bool(actionable_signals)
    calculation = _money(responsibility - received)
    warnings = []
    if abs(outstanding - calculation) > 0.01:
        warnings.append(
            f"Workbook Outstanding_Patient_Balance {outstanding:.2f} differs from responsibility-minus-payment {calculation:.2f}; workbook balance remains authoritative."
        )
    reason = (
        "Outstanding balance is actionable because " + ", ".join(actionable_signals) + "."
        if supported else
        "Patient balance is paid in full or no actionable workbook signal is present."
    )
    return _category(
        status="supported" if supported else "supported_zero",
        amount=outstanding if supported else 0,
        reason_code="ACTIONABLE_PATIENT_BALANCE" if supported else "PATIENT_BALANCE_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=reason,
        formula=f"{responsibility:.2f} - {received:.2f} = {outstanding:.2f}",
        evidence_fields=required + ["Days_Outstanding", "Aging_Bucket", "Payment_Plan_Status", "Collection_Status", "Chk_Confirmed_Unpaid_Balance"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.99 if supported else 0.95,
        warnings=warnings,
        details={
            "patient_responsibility": responsibility,
            "patient_payment_received": received,
            "outstanding_patient_balance": outstanding,
            "actionable_signals": actionable_signals,
        },
    )


def _correctable_denial(claim):
    fields = claim["workbookFields"]
    required = ["Claim_Status_Description", "Denial_Correctable_Flag", "Expected_Reimbursement", "Recovered_Amount"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("correctable_denial", missing)
    denied = "denied" in _lower(fields["Claim_Status_Description"]) or "reject" in _lower(fields["Claim_Status_Description"])
    appeal = _lower(fields.get("Appeal_Status"))
    resubmission = _lower(fields.get("Resubmission_Status"))
    actionable = appeal in ACTIONABLE_APPEAL or resubmission in ACTIONABLE_RESUBMISSION
    expected = _money(fields["Expected_Reimbursement"])
    recovered = _money(fields["Recovered_Amount"])
    amount = _money(max(expected - recovered, 0))
    supported = denied and _yes(fields["Denial_Correctable_Flag"]) and actionable and amount > 0
    reason = (
        f"Correctable unresolved denial with actionable appeal/resubmission status supports {amount:.2f}."
        if supported else
        "Claim status or workbook appeal/resubmission evidence does not support a current denial recovery."
    )
    return _category(
        status="supported" if supported else "supported_zero",
        amount=amount if supported else 0,
        reason_code="CORRECTABLE_DENIAL_RECOVERY" if supported else "DENIAL_RECOVERY_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=reason,
        formula=f"{expected:.2f} - {recovered:.2f} = {amount:.2f}",
        evidence_fields=required + ["Appeal_Status", "Resubmission_Status", "Denial_Resolution", "CARC_Code", "RARC_Code"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.96 if supported else 0.9,
    )


def _excessive_adjustment(claim):
    fields = claim["workbookFields"]
    required = ["Charge_Amount", "Contract_Allowed_Amount", "Adjustment_Amount", "Payment_Tolerance"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("excessive_adjustment", missing)
    charge = _money(fields["Charge_Amount"])
    contract_allowed = _money(fields["Contract_Allowed_Amount"])
    adjustment = _money(fields["Adjustment_Amount"])
    tolerance = _money(fields["Payment_Tolerance"])
    expected_adjustment = _money(max(charge - contract_allowed, 0))
    excessive = _money(max(adjustment - expected_adjustment, 0))
    calculated_supported = excessive >= tolerance and excessive > 0
    workbook_supported = _yes(fields.get("Chk_Excessive_Adjustment"))
    warnings = []
    if calculated_supported != workbook_supported:
        warnings.append("Workbook excessive-adjustment check conflicts with the deterministic calculation.")
    supported = calculated_supported and workbook_supported
    return _category(
        status="supported" if supported else "supported_zero",
        amount=excessive if supported else 0,
        reason_code="EXCESSIVE_ADJUSTMENT" if supported else "ADJUSTMENT_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=(
            f"Adjustment exceeds expected contract adjustment by {excessive:.2f} and meets tolerance."
            if supported else
            "Adjustment does not have consistent workbook and calculation support above tolerance."
        ),
        formula=f"max({adjustment:.2f} - max({charge:.2f} - {contract_allowed:.2f}, 0), 0) = {excessive:.2f}",
        evidence_fields=required + ["Chk_Excessive_Adjustment", "Reason_Code", "Reason_Description"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.96 if supported else 0.9,
        warnings=warnings,
        details={"expected_contract_adjustment": expected_adjustment},
    )


def _authorization(claim):
    fields = claim["workbookFields"]
    required = ["Prior_Authorization_Required", "Authorization_Status"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("authorization", missing)
    required_flag = _yes(fields["Prior_Authorization_Required"])
    status = _lower(fields["Authorization_Status"])
    supported = required_flag and status in ACTIONABLE_AUTH
    return _category(
        status="supported" if supported else "supported_zero",
        amount=0,
        reason_code="AUTHORIZATION_ACTION_REQUIRED" if supported else "AUTHORIZATION_ACTION_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=(
            f"Authorization is required and status is {_text(fields['Authorization_Status'])}."
            if supported else
            "Authorization is authorized, not required, or has no actionable workbook status."
        ),
        formula="Administrative action; no recoverable amount is calculated.",
        evidence_fields=required + ["Authorization_Valid_From", "Authorization_Valid_To", "Authorized_Units"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.98,
    )


def _referral(claim):
    fields = claim["workbookFields"]
    required = ["Referral_Required", "Referral_Status"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("referral", missing)
    required_flag = _yes(fields["Referral_Required"])
    status = _lower(fields["Referral_Status"])
    supported = required_flag and status in ACTIONABLE_REFERRAL
    return _category(
        status="supported" if supported else "supported_zero",
        amount=0,
        reason_code="REFERRAL_ACTION_REQUIRED" if supported else "REFERRAL_ACTION_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=(
            f"Referral is required and status is {_text(fields['Referral_Status'])}."
            if supported else
            "Referral is valid, not required, or has no actionable workbook status."
        ),
        formula="Administrative action; no recoverable amount is calculated.",
        evidence_fields=required,
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.98,
    )


def _duplicate_or_correction(claim):
    fields = claim["workbookFields"]
    required = ["Corrected_Claim_Flag", "Duplicate_Claim_Flag"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("duplicate_or_correction", missing)
    supported = _yes(fields["Corrected_Claim_Flag"]) or _yes(fields["Duplicate_Claim_Flag"])
    amount = _money(fields.get("Paid_Amount")) if supported else 0
    return _category(
        status="supported" if supported else "supported_zero",
        amount=amount,
        reason_code="DUPLICATE_OR_CORRECTION_REVIEW" if supported else "DUPLICATE_CORRECTION_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=(
            "Workbook duplicate/corrected-claim flag requires payment reconciliation."
            if supported else
            "Workbook flags do not indicate a duplicate or corrected claim."
        ),
        formula=f"Payment reconciliation amount = {amount:.2f}" if supported else "No flagged payment reconciliation amount.",
        evidence_fields=required + ["Original_Claim_ID", "Replacement_Claim_ID", "Claim_Frequency_Code"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="837_Claims",
        confidence=0.9,
    )


def _avoidable_spend(database, claim):
    fields = claim["workbookFields"]
    required = ["Episode_ID", "Comparable_Episodes_Count", "Repeat_Visit_Reason", "Allowed_Amount"]
    missing = [name for name in required if not _available(fields, name)]
    if missing:
        return _insufficient("potentially_avoidable_episode_spend", missing)
    comparator_count = int(_number(fields.get("Comparable_Episodes_Count"), 0))
    if comparator_count < MIN_COMPARATOR_EPISODES:
        return _category(
            status="supported_zero",
            amount=0,
            reason_code="INSUFFICIENT_COMPARABLE_EPISODES",
            reason=f"Only {comparator_count} comparable episodes were available; minimum {MIN_COMPARATOR_EPISODES} required.",
            formula="Current episode allowed cost - median qualified lower-repeat comparator episode cost.",
            evidence_fields=required + ["Too_Few_Repeated_Related_Episodes", "Reason_Code"],
            evidence_claim_ids=[claim["claimId"]],
            data_source="historical_reference",
            confidence=0.95,
            details={"comparable_episode_count": comparator_count, "minimum_comparator_count": MIN_COMPARATOR_EPISODES},
        )
    episode_id = _text(fields["Episode_ID"])
    cutoff = claim.get("dos") or ""
    current_episode = [
        row for row in database.selectable_claims
        if row.get("episodeId") == episode_id and row.get("dos", "") <= cutoff
    ]
    repeat_reason = _lower(fields.get("Repeat_Visit_Reason"))
    qualifying_repeat = len(current_episode) >= 2 and _yes(fields.get("Related_Claim_Flag")) and repeat_reason not in {"planned follow-up", "routine follow-up", "not applicable", "n/a"}
    peer_episodes = {}
    for row in database.historical_claims:
        if row.get("dos", "") >= cutoff:
            continue
        row_fields = row["workbookFields"]
        if _text(row_fields.get("ICD10_Family")) != _text(fields.get("ICD10_Family")):
            continue
        peer_episodes.setdefault(_text(row_fields.get("Episode_ID")), []).append(row)
    comparator_costs = [
        _money(sum(_money(item.get("allowed")) for item in rows))
        for rows in peer_episodes.values()
        if rows and sum(_yes(item["workbookFields"].get("Related_Claim_Flag")) for item in rows) < len(current_episode)
    ]
    if not qualifying_repeat or len(comparator_costs) < MIN_COMPARATOR_EPISODES:
        count = len(comparator_costs)
        return _category(
            status="supported_zero",
            amount=0,
            reason_code="INSUFFICIENT_COMPARABLE_EPISODES",
            reason=f"Only {count} qualified lower-repeat comparator episodes were available; minimum {MIN_COMPARATOR_EPISODES} required.",
            formula="Current episode allowed cost - median qualified lower-repeat comparator episode cost.",
            evidence_fields=required + ["Related_Claim_Flag", "Treatment_Outcome", "Condition_Resolved"],
            evidence_claim_ids=[row["claimId"] for row in current_episode],
            data_source="historical_reference",
            confidence=0.85,
            details={
                "comparable_episode_count": count,
                "minimum_comparator_count": MIN_COMPARATOR_EPISODES,
            },
        )
    current_cost = _money(sum(_money(row.get("allowed")) for row in current_episode))
    comparator_median = _money(median(comparator_costs))
    amount = _money(max(current_cost - comparator_median, 0))
    return _category(
        status="supported" if amount > 0 else "supported_zero",
        amount=amount,
        reason_code="SUPPORTED_AVOIDABLE_EPISODE_SPEND" if amount > 0 else "EPISODE_SPEND_CURRENTLY_SUPPORTED_AT_ZERO",
        reason=f"Current episode allowed cost {current_cost:.2f} compared with qualified lower-repeat median {comparator_median:.2f}.",
        formula=f"{current_cost:.2f} - {comparator_median:.2f} = {amount:.2f}",
        evidence_fields=required + ["Related_Claim_Flag", "Treatment_Outcome", "Condition_Resolved"],
        evidence_claim_ids=[row["claimId"] for row in current_episode],
        data_source="historical_reference",
        confidence=0.88,
        details={"current_episode_allowed_cost": current_cost, "comparator_median_allowed_cost": comparator_median},
    )


def _future_exposure(claim, prediction):
    denial = _money(prediction["denial_probability"] * prediction["predicted_paid"])
    repeat_allowed = _money(prediction["repeat_probability_90d"] * prediction["predicted_allowed"])
    repeat_paid = _money(prediction["repeat_probability_90d"] * prediction["predicted_paid"])
    return _category(
        status="supported",
        amount=0,
        reason_code="FORECAST_EXPOSURE",
        reason="Forecast exposure is separate from current recoverable money.",
        formula="Probability × deterministic predicted amount.",
        evidence_fields=["denial_probability", "repeat_probability_90d", "predicted_allowed", "predicted_paid"],
        evidence_claim_ids=[claim["claimId"]],
        data_source="prediction",
        confidence=prediction["confidence"]["score"],
        details={
            "denial_revenue_exposure": denial,
            "repeat_allowed_exposure": repeat_allowed,
            "repeat_provider_payment_exposure": repeat_paid,
        },
    )


def _financial_prediction_snapshot(prediction):
    predicted_paid = prediction["predicted_paid_range"]
    predicted_allowed = prediction["predicted_allowed_range"]
    predicted_patient = prediction["predicted_patient_responsibility_range"]
    predicted_adjustment = prediction["predicted_adjustment_range"]
    denial_exposure = _money(prediction["denial_probability"] * predicted_paid["value"])
    repeat_allowed_exposure = _money(
        prediction["repeat_probability_90d"] * predicted_allowed["value"]
    )
    repeat_payment_exposure = _money(
        prediction["repeat_probability_90d"] * predicted_paid["value"]
    )
    denial_basis = prediction["denial_prediction_basis"]
    future_denial_exposure = {
        "value": denial_exposure,
        "denial_probability": prediction["denial_probability"],
        "denial_prediction_basis": prediction["denial_prediction_basis"],
        "predicted_paid": predicted_paid["value"],
        "peer_count": denial_basis["peer_count"],
        "peer_level": denial_basis["peer_level"],
        "confidence": denial_basis["confidence"],
    }
    return {
        "predicted_allowed": predicted_allowed,
        "predicted_provider_payment": predicted_paid,
        "predicted_patient_responsibility": predicted_patient,
        "predicted_contractual_adjustment": predicted_adjustment,
        "denial_probability": prediction["denial_probability"],
        "denial_prediction_basis": denial_basis,
        "repeat_probability_30d": prediction["repeat_probability_30d"],
        "repeat_probability_60d": prediction["repeat_probability_60d"],
        "repeat_probability_90d": prediction["repeat_probability_90d"],
        "repeat_probability_evidence": prediction["avoidable_prediction_basis"]["repeat_probability"].get("horizons", {}),
        "predicted_denial_revenue_exposure": denial_exposure,
        "future_denial_exposure": future_denial_exposure,
        "predicted_repeat_allowed_exposure": repeat_allowed_exposure,
        "predicted_repeat_payment_exposure": repeat_payment_exposure,
        "predicted_avoidable_spend": prediction["predicted_avoidable_spend"],
        "predicted_avoidable_provider_payment": prediction[
            "predicted_avoidable_provider_payment"
        ],
        "avoidable_prediction_basis": prediction[
            "avoidable_prediction_basis"
        ],
        "avoidable_formula_trace": prediction["avoidable_formula_trace"],
        "confidence": prediction["confidence"],
        "prediction_method": prediction["method"],
        "model_version": PREDICTION_VERSION,
        "calculation_version": CALCULATION_VERSION,
    }


OPPORTUNITY_LABELS = {
    "underpayment": "Payment underpayment",
    "correctable_denial": "Correctable denial recovery",
    "excessive_adjustment": "Excessive adjustment",
    "patient_balance": "Actionable patient balance",
    "authorization": "Authorization",
    "referral": "Referral",
    "duplicate_or_correction": "Duplicate or correction",
    "potentially_avoidable_episode_spend": "Potentially avoidable episode spend",
}


def _evidence_items(fields, names):
    money_tokens = (
        "Amount",
        "Balance",
        "Responsibility",
        "Payment_Received",
        "Tolerance",
        "Reimbursement",
        "Cost",
    )
    items = []
    for field in names:
        value = fields.get(field)
        if field not in fields or value in (None, ""):
            continue
        is_money = isinstance(value, (int, float)) and any(
            token in field for token in money_tokens
        )
        items.append({
            "field": field,
            "value": value,
            "display_value": f"${_money(value):,.2f}" if is_money else str(value),
        })
    return items


def _supported_and_non_actionable(claim, categories):
    fields = claim["workbookFields"]
    supported = []
    non_actionable = []
    for category_name, category in categories.items():
        if category_name == "future_exposure":
            continue
        entry = {
            "type": category_name,
            "label": OPPORTUNITY_LABELS[category_name],
            "evidence": _evidence_items(fields, category["evidence_fields"]),
            **category,
        }
        if category["status"] == "supported" and category["amount"] > 0:
            supported.append(entry)
            continue
        non_actionable.append({
            **entry,
            "action_selected": False,
        })
    supported.sort(key=lambda item: item["amount"], reverse=True)
    return supported, non_actionable


def _validate_prediction_consistency(snapshot, categories, summary):
    expected_denial = _money(
        snapshot["denial_probability"]
        * snapshot["predicted_provider_payment"]["value"]
    )
    expected_repeat_payment = _money(
        snapshot["repeat_probability_90d"]
        * snapshot["predicted_provider_payment"]["value"]
    )
    future = categories["future_exposure"]["details"]
    predicted_avoidable = snapshot["predicted_avoidable_spend"]["value"]
    predicted_avoidable_paid = snapshot[
        "predicted_avoidable_provider_payment"
    ]["value"]
    checks = (
        snapshot["predicted_denial_revenue_exposure"] == expected_denial,
        snapshot["predicted_repeat_payment_exposure"] == expected_repeat_payment,
        future["denial_revenue_exposure"] == expected_denial,
        future["repeat_provider_payment_exposure"] == expected_repeat_payment,
        summary["future_denial_exposure"] == expected_denial,
        summary["future_repeat_payment_exposure"] == expected_repeat_payment,
        predicted_avoidable
        <= snapshot["predicted_repeat_allowed_exposure"] + 0.01,
        predicted_avoidable_paid
        <= snapshot["predicted_repeat_payment_exposure"] + 0.01,
        summary["predicted_avoidable_spend"]
        == predicted_avoidable,
        summary["predicted_avoidable_provider_payment"]
        == predicted_avoidable_paid,
    )
    if not all(checks):
        raise RuntimeError("PREDICTION_CONSISTENCY_ERROR")
    return {
        "passed": True,
        "fields_checked": [
            "predicted_paid",
            "predicted_adjustment",
            "denial_probability",
            "repeat_probability_90d",
            "denial_exposure",
            "repeat_payment_exposure",
            "predicted_avoidable_spend",
            "predicted_avoidable_provider_payment",
            "confidence",
        ],
    }


def _best_action(categories):
    candidates = []
    action_map = {
        "underpayment": ("Payment underpayment review", "Validate and pursue the supported payment variance.", "provider revenue integrity"),
        "correctable_denial": ("Correctable denial follow-up", "File or continue the supported appeal/resubmission workflow.", "denials management"),
        "excessive_adjustment": ("Contract adjustment review", "Reconcile the adjustment against the contract-allowed amount.", "contract management"),
        "patient_balance": ("Patient-balance follow-up / broken payment-plan review", "Follow up the workbook-confirmed outstanding patient balance and payment-plan signal.", "patient financial services"),
        "duplicate_or_correction": ("Duplicate or corrected-claim review", "Reconcile the flagged claim and related payment.", "claims operations"),
        "authorization": ("Authorization correction", "Resolve the actionable authorization status before the next billing step.", "authorization team"),
        "referral": ("Referral correction", "Resolve the actionable referral status before the next billing step.", "referral team"),
        "potentially_avoidable_episode_spend": ("Episode payment review", "Review the evidence-supported avoidable episode-spend difference.", "provider performance"),
    }
    for name, (stage, action, owner) in action_map.items():
        category = categories[name]
        if category["status"] == "supported":
            candidates.append((category["amount"], name, stage, action, owner, category))
    if not candidates:
        return {
            "type": "monitoring",
            "stage": "Evidence-based monitoring",
            "action": "Continue monitoring the workbook-supported financial categories.",
            "owner": "provider operations",
            "amount_addressed": 0.0,
            "evidence_claim_ids": [],
            "evidence_fields": [],
            "confidence": 0.9,
            "reason_code": "CURRENTLY_SUPPORTED_AT_ZERO",
        }
    amount, name, stage, action, owner, category = max(candidates, key=lambda item: (item[0], item[1] == "patient_balance"))
    return {
        "type": name,
        "stage": stage,
        "action": action,
        "owner": owner,
        "amount_addressed": _money(amount),
        "evidence_claim_ids": category["evidence_claim_ids"],
        "evidence_fields": category["evidence_fields"],
        "confidence": category["confidence"],
        "reason_code": category["reason_code"],
        "reason": category["reason"],
    }


def _summary(categories, best_action, prediction_snapshot):
    recoverable_names = ["underpayment", "correctable_denial", "excessive_adjustment", "patient_balance", "duplicate_or_correction"]
    recoverable_supported = [
        (name, categories[name]["amount"])
        for name in recoverable_names
        if categories[name]["status"] == "supported" and categories[name]["amount"] > 0
    ]
    recoverable = _money(sum(amount for _, amount in recoverable_supported))
    avoidable = categories["potentially_avoidable_episode_spend"]
    future = categories["future_exposure"]["details"]
    all_supported = list(recoverable_supported)
    if avoidable["status"] == "supported" and avoidable["amount"] > 0:
        all_supported.append(("potentially_avoidable_episode_spend", avoidable["amount"]))
    top = max(all_supported, key=lambda item: item[1]) if all_supported else ("", 0.0)
    top_opportunity = {
        "type": top[0],
        "label": OPPORTUNITY_LABELS.get(top[0], ""),
        "amount": _money(top[1]),
    }
    return {
        "recoverable_now": recoverable,
        "predicted_avoidable_spend": prediction_snapshot[
            "predicted_avoidable_spend"
        ]["value"],
        "predicted_avoidable_provider_payment": prediction_snapshot[
            "predicted_avoidable_provider_payment"
        ]["value"],
        "potentially_avoidable_spend": avoidable["amount"] if avoidable["status"] == "supported" else 0.0,
        "potentially_avoidable_spend_supported": avoidable["amount"] if avoidable["status"] == "supported" else 0.0,
        "future_denial_exposure": future["denial_revenue_exposure"],
        "future_repeat_payment_exposure": future["repeat_provider_payment_exposure"],
        "top_supported_opportunity": top_opportunity,
        "top_supported_opportunity_type": top[0],
        "top_supported_opportunity_amount": _money(top[1]),
        "best_action": best_action,
        "calculation_trace": [
            {
                "category": name,
                "amount": amount,
                "formula": categories[name]["formula"],
                "reason_code": categories[name]["reason_code"],
            }
            for name, amount in all_supported
        ],
        "version": SAVINGS_VERSION,
    }


def _historical_comparison(database, claim):
    peers, basis = select_peers(database, claim)
    patterns = historical_patterns(database, claim)
    return {
        "match_level": basis["peer_label"],
        "peer_level": basis["peer_level"],
        "matching_dimensions": basis["matching_dimensions"],
        "readable_basis": basis["readable_basis"],
        "sample_size": len(peers),
        "cutoff_date": claim.get("dos"),
        "earlier_same_member_claims": patterns["earlier_member_claims"],
        "earlier_same_cpt_claims": patterns["same_cpt_claims"],
        "previous_denials": patterns["previous_denials"],
        "peer_claim_ids": basis["claim_ids_used"],
    }


def _scenario_map(claim, snapshot, categories, supported, summary, comparison, database, patterns, similar, short_patterns):
    fields = claim["workbookFields"]
    best = summary["best_action"]
    visible_categories = {name: category for name, category in categories.items() if name != "future_exposure"}
    return {
        "sections": [
            {
                "step": 1,
                "title": "Member History",
                "items": {
                    "earlier_claims": patterns["earlier_member_claims"],
                    "same_cpt_icd_claims": patterns["same_cpt_icd_claims"],
                    "previous_denials": comparison["previous_denials"],
                    "episode_id": claim.get("episodeId"),
                    "peer_evidence_count": comparison["sample_size"],
                },
            },
            {
                "step": 2,
                "title": "Current Claim",
                "items": {
                    "cpt": f"{claim.get('cptCode')} — {claim.get('cptDescription')}",
                    "diagnosis": f"{claim.get('diagnosisCode')} — {claim.get('diagnosisDescription')}",
                    "place_of_service": f"{claim.get('placeOfServiceCode')} — {claim.get('placeOfService')}",
                    "payer": claim.get("payer"),
                    "provider": claim.get("billingProvider"),
                    "authorization": _text(fields.get("Authorization_Status")),
                    "referral": _text(fields.get("Referral_Status")),
                },
            },
            {
                "step": 3,
                "title": "ICD + CPT Relationship",
                "items": {
                    "cpt": claim.get("cptCode"), "icd_family": fields.get("ICD10_Family"),
                    "matching_basis": comparison["readable_basis"],
                },
            },
            {
                "step": 4,
                "title": "Similar Historical Claims",
                "items": {"matches": len(similar), "top_claim_ids": [item["claim_id"] for item in similar[:5]]},
            },
            {
                "step": 5,
                "title": "Short-Timeframe / Repeat Pattern",
                "items": {"related_pairs": len(short_patterns), "within_90_days": patterns["within_90_days"]},
            },
            {
                "step": 6,
                "title": "Financial Prediction",
                "items": {
                    "predicted_allowed": snapshot["predicted_allowed"]["value"],
                    "predicted_provider_payment": snapshot["predicted_provider_payment"]["value"],
                    "predicted_patient_responsibility": snapshot["predicted_patient_responsibility"]["value"],
                    "predicted_contractual_adjustment": snapshot["predicted_contractual_adjustment"]["value"],
                    "denial_probability": snapshot["denial_probability"],
                    "future_denial_exposure": snapshot[
                        "future_denial_exposure"
                    ]["value"],
                    "repeat_probability_90d": snapshot["repeat_probability_90d"],
                    "predicted_avoidable_spend": snapshot[
                        "predicted_avoidable_spend"
                    ]["value"],
                    "predicted_avoidable_provider_payment": snapshot[
                        "predicted_avoidable_provider_payment"
                    ]["value"],
                },
            },
            {
                "step": 7,
                "title": "Financial Opportunity",
                "items": supported,
                "calculations": [
                    {
                        "category": name,
                        "status": category["status"],
                        "amount": category["amount"],
                        "formula": category["formula"],
                        "reason": category["reason"],
                    }
                    for name, category in visible_categories.items()
                    if category["status"] == "supported" and category["amount"] > 0
                ],
            },
            {
                "step": 8,
                "title": "Supporting Evidence",
                "items": {"workbook_sheet": "837_Claims", "workbook_row": claim["workbookSourceRow"], "peer_claim_ids": comparison["peer_claim_ids"][:10]},
            },
            {
                "step": 9,
                "title": "Best Provider Action",
                "items": best,
            },
        ]
    }


def build_financial_result(database, claim_id):
    claim = database.find_claim(claim_id, selectable_only=True)
    if not claim:
        raise KeyError("Selectable claim was not found in 837_Claims.")
    cache_key = (
        database.workbook_hash,
        claim["claimId"],
        claim["sourceRowHash"],
        CALCULATION_VERSION,
        PREDICTION_VERSION,
        SAVINGS_VERSION,
    )
    with _LOCK:
        cached = _RESULT_CACHE.get(cache_key)
        if cached:
            return cached
    prediction = _prediction(database, claim)
    prediction_snapshot = _financial_prediction_snapshot(prediction)
    categories = {
        "underpayment": _underpayment(claim),
        "correctable_denial": _correctable_denial(claim),
        "excessive_adjustment": _excessive_adjustment(claim),
        "patient_balance": _patient_balance(claim),
        "authorization": _authorization(claim),
        "referral": _referral(claim),
        "duplicate_or_correction": _duplicate_or_correction(claim),
        "potentially_avoidable_episode_spend": _avoidable_spend(database, claim),
    }
    categories["future_exposure"] = _future_exposure(claim, prediction)
    best_action = _best_action(categories)
    summary = _summary(categories, best_action, prediction_snapshot)
    supported_opportunities, non_actionable_evidence = _supported_and_non_actionable(
        claim, categories
    )
    comparison = _historical_comparison(database, claim)
    patterns = historical_patterns(database, claim)
    similar = similar_historical_claims(database, claim)
    short_patterns = short_timeframe_patterns(database, claim)
    consistency_check = _validate_prediction_consistency(
        prediction_snapshot, categories, summary
    )
    fields = claim["workbookFields"]
    validated_category = categories[
        "potentially_avoidable_episode_spend"
    ]
    validated_avoidable_spend = {
        "available": validated_category["reason_code"]
        != "INSUFFICIENT_COMPARABLE_EPISODES"
        and validated_category["status"] != "insufficient_source_fields",
        "value": validated_category["amount"],
        "reason": validated_category["reason"],
        "reason_code": validated_category["reason_code"],
        "comparator_episode_count": int(
            _number(
                validated_category.get("details", {}).get(
                    "comparable_episode_count",
                    fields.get("Comparable_Episodes_Count"),
                ),
                0,
            )
        ),
    }
    result = {
        "claim_id": claim["claimId"],
        "member_id": claim["memberId"],
        "episode_id": claim.get("episodeId"),
        "source": {
            **database.source_banner(),
            "source_sheet": "837_Claims",
            "source_row": claim["workbookSourceRow"],
            "claim_source_row_hash": claim["sourceRowHash"],
        },
        "actual_claim_facts": {
            "claim_id": claim["claimId"],
            "service_date": claim.get("dos"),
            "cpt_code": claim.get("cptCode"),
            "cpt_description": claim.get("cptDescription"),
            "diagnosis_code": claim.get("diagnosisCode"),
            "diagnosis_description": claim.get("diagnosisDescription"),
            "units": claim.get("units"),
            "place_of_service_code": claim.get("placeOfServiceCode"),
            "place_of_service_description": claim.get("placeOfService"),
            "payer": claim.get("payer"),
            "provider": claim.get("billingProvider"),
            "billing_provider": claim.get("billingProvider"),
            "rendering_provider": _text(fields.get("Rendering_Provider_Name") or fields.get("Rendering_Provider_NPI")),
            "charge": _money(fields.get("Charge_Amount")),
            "allowed": _money(fields.get("Allowed_Amount")),
            "paid": _money(fields.get("Paid_Amount")),
            "patient_responsibility": _money(fields.get("Patient_Responsibility")),
            "adjustment": _money(fields.get("Adjustment_Amount")),
            "expected_reimbursement": _money(fields.get("Expected_Reimbursement")),
            "contract_allowed": _money(fields.get("Contract_Allowed_Amount")),
            "patient_payment_received": _money(fields.get("Patient_Payment_Received")),
            "outstanding_patient_balance": _money(fields.get("Outstanding_Patient_Balance")),
            "recovered_amount": _money(fields.get("Recovered_Amount")),
            "claim_status": _text(fields.get("Claim_Status_Description")),
            "authorization_required": _yes(fields.get("Prior_Authorization_Required")),
            "authorization_status": _text(fields.get("Authorization_Status")),
            "referral_required": _yes(fields.get("Referral_Required")),
            "referral_status": _text(fields.get("Referral_Status")),
        },
        "prediction": prediction,
        "financial_prediction": prediction_snapshot,
        "financial_prediction_snapshot": prediction_snapshot,
        "predicted_avoidable_spend": prediction_snapshot[
            "predicted_avoidable_spend"
        ],
        "predicted_avoidable_provider_payment": prediction_snapshot[
            "predicted_avoidable_provider_payment"
        ],
        "validated_avoidable_spend": validated_avoidable_spend,
        "financial_opportunities": categories,
        "supported_financial_opportunities": supported_opportunities,
        "non_actionable_evidence": non_actionable_evidence,
        "supported_money_summary": summary,
        "best_action": best_action,
        "historical_comparison": comparison,
        "historical_patterns": patterns,
        "similar_historical_claims": similar,
        "short_timeframe_patterns": short_patterns,
        "historical_prediction_basis": comparison,
        "scenario_map": {},
        "rag_evidence": [],
        "prediction_explanation": {},
        "rag": {
            "index_version": RAG_INDEX_VERSION,
            "workbook_hash": database.workbook_hash,
            "query": "",
            "retrieved_chunks": [],
        },
        "confidence": prediction["confidence"],
        "consistency_check": consistency_check,
        "limitations": [
            category["reason"]
            for category in categories.values()
            if category["status"] == "insufficient_source_fields"
        ],
        "versions": {
            "calculation_version": CALCULATION_VERSION,
            "prediction_version": PREDICTION_VERSION,
            "savings_version": SAVINGS_VERSION,
            "rag_index_version": RAG_INDEX_VERSION,
            "groq_prompt_version": GROQ_PROMPT_VERSION,
        },
    }
    result["claim_facts"] = result["actual_claim_facts"]
    result["scenario_map"] = _scenario_map(
        claim,
        prediction_snapshot,
        categories,
        supported_opportunities,
        summary,
        comparison,
        database,
        patterns,
        similar,
        short_patterns,
    )
    try:
        from .prediction_validation import (
            claim_backtest,
            validate_prediction_result,
        )
    except ImportError:
        from prediction_validation import claim_backtest, validate_prediction_result
    result["retrospective_validation"] = claim_backtest(result)
    result["consistency_check"] = validate_prediction_result(result)
    result["financial_result_hash"] = sha256(
        json.dumps(
            {
                "claim_id": result["claim_id"],
                "financial_prediction_snapshot": prediction_snapshot,
                "supported_financial_opportunities": supported_opportunities,
                "non_actionable_evidence": non_actionable_evidence,
                "supported_money_summary": summary,
                "consistency_check": consistency_check,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with _LOCK:
        _RESULT_CACHE[cache_key] = result
    return result


def member_supported_summary(database, member_id):
    claims = database.member_claims(member_id)
    results = [build_financial_result(database, claim["claimId"]) for claim in claims]
    recoverable = _money(sum(item["supported_money_summary"]["recoverable_now"] for item in results))
    future_denial = _money(sum(item["supported_money_summary"]["future_denial_exposure"] for item in results))
    future_repeat = _money(sum(item["supported_money_summary"]["future_repeat_payment_exposure"] for item in results))
    episode_values = {}
    latest_episode_predictions = {}
    supported_actions = []
    for item in results:
        episode_values[item["episode_id"]] = max(
            episode_values.get(item["episode_id"], 0),
            item["supported_money_summary"]["potentially_avoidable_spend_supported"],
        )
        episode_key = item["episode_id"] or item["claim_id"]
        current = latest_episode_predictions.get(episode_key)
        if (
            current is None
            or (
                item["actual_claim_facts"]["service_date"],
                item["claim_id"],
            )
            > (
                current["actual_claim_facts"]["service_date"],
                current["claim_id"],
            )
        ):
            latest_episode_predictions[episode_key] = item
        for opportunity in item["supported_financial_opportunities"]:
            supported_actions.append({
                "claim_id": item["claim_id"],
                "episode_id": item["episode_id"],
                "type": opportunity["type"],
                "label": opportunity["label"],
                "amount": opportunity["amount"],
                "best_action": item["best_action"],
            })
    top = max(supported_actions, key=lambda item: item["amount"]) if supported_actions else {}
    member_predicted_avoidable = _money(
        sum(
            item["predicted_avoidable_spend"]["value"]
            for item in latest_episode_predictions.values()
        )
    )
    member_predicted_avoidable_paid = _money(
        sum(
            item["predicted_avoidable_provider_payment"]["value"]
            for item in latest_episode_predictions.values()
        )
    )
    validated_total = _money(sum(episode_values.values()))
    return {
        "member_id": member_id,
        "active_episode_count": len(latest_episode_predictions),
        "predicted_avoidable_spend_90d": member_predicted_avoidable,
        "predicted_avoidable_provider_payment_90d": member_predicted_avoidable_paid,
        "validated_avoidable_spend": validated_total,
        "recoverable_now_total": recoverable,
        "supported_avoidable_spend_total": validated_total,
        "future_denial_exposure_total": future_denial,
        "future_repeat_payment_exposure_total": future_repeat,
        "supported_action_count": len(supported_actions),
        "top_opportunity": top,
        "top_action": top.get("best_action", {}),
        "recoverable_now": recoverable,
        "potentially_avoidable_spend_supported": validated_total,
        "predicted_avoidable_spend": member_predicted_avoidable,
        "predicted_avoidable_provider_payment": member_predicted_avoidable_paid,
        "future_denial_exposure": future_denial,
        "future_repeat_payment_exposure": future_repeat,
        "claims_with_supported_actions": len({
            item["claim_id"] for item in supported_actions
        }),
        "top_supported_opportunity": top,
        "highest_priority_action": top.get("best_action", {}),
        "claim_count": len(results),
        "episode_count": len(latest_episode_predictions),
        "workbook_hash": database.workbook_hash,
        "calculation_version": CALCULATION_VERSION,
    }
