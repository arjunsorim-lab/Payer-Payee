"""Data-driven linking of preventive visits to later outcomes."""
from datetime import date
import re

from .evidence import (
    entry,
    RECORDED_CLAIM_FACT,
    SYNTHETIC_DEMONSTRATION,
    RECOMMENDATION,
    REVIEWER_OBSERVATION,
    MODEL_ESTIMATE,
    VERIFIED_SAVINGS,
)
from .intervention_plans import build_intervention_plan


POSITIVE_OUTCOME_TERMS = (
    "resolved",
    "improved",
    "recovered",
    "positive",
    "documented outcome assessment",
)

def _text(value):
    return str(value or "").strip()


def _diagnosis_family(claim):
    code = "".join(character for character in _text(claim.get("diagnosisCode")).upper() if character.isalnum())
    return code[:3]


def _positive_outcome(claim):
    fields = claim.get("workbookFields", {})
    values = " | ".join(
        _text(fields.get(key))
        for key in ("Condition_Resolved", "Treatment_Outcome", "Follow_Up_Completed")
    ).lower()
    explicit_flag = _text(fields.get("Outcome_Claim_Flag")).upper() == "Y"
    return explicit_flag or any(term in values for term in POSITIVE_OUTCOME_TERMS)


def _preventive_visit(claim):
    fields = claim.get("workbookFields", {})
    description = f"{claim.get('cptDescription', '')} {fields.get('Procedure_Description', '')}".lower()
    cpt = _text(claim.get("cptCode"))
    reason = _text(fields.get("Reason_Code")).upper()
    return (
        "preventive" in description
        or cpt in {str(code) for code in range(99381, 99398)}
        or (_text(fields.get("Intervention_Performed")).upper() == "Y" and "visit" in description)
        or (
            _text(fields.get("Intervention_Performed")).upper() == "Y"
            and reason == "SYNTHETIC_SEQUENCE_PREVENTIVE"
        )
    )


def _date(value):
    text = _text(value)
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _integer(value):
    try:
        return int(float(_text(value)))
    except (TypeError, ValueError):
        return None


def _without_fixed_day_claim(value):
    text = _text(value)
    return re.sub(r"\s*(?:for|during)\s+\d+\s*(?:-day|day|days)\s*(?:follow-up)?", "", text, flags=re.IGNORECASE).strip(" ;")


def _observed_no_readmission_days(intervention_claim, recorded_days, today=None):
    service_date = _date(
        intervention_claim.get("dos")
        or intervention_claim.get("workbookFields", {}).get("Service_Date_From")
    ) if intervention_claim else None
    if not service_date:
        return _integer(recorded_days)
    today = today or date.today()
    elapsed_days = max(0, (today - service_date).days)
    recorded = _integer(recorded_days)
    return min(recorded, elapsed_days) if recorded is not None else elapsed_days


def _claim_by_id(database, claim_id):
    normalized = _text(claim_id).upper()
    return next(
        (candidate for candidate in database.claims if _text(candidate.get("claimId")).upper() == normalized),
        None,
    )


def _patient_name(claim):
    if not claim:
        return None
    return (
        _text(claim.get("patient"))
        or " ".join(
            part for part in (
                _text(claim.get("patientFirstName")),
                _text(claim.get("patientLastName")),
            )
            if part
        )
        or None
    )


def _same_service_day(left, right):
    return _text(left.get("dos"))[:10] == _text(right.get("dos"))[:10]


def _office_or_preventive_visit(claim):
    description = f"{claim.get('cptDescription', '')} {claim.get('workbookFields', {}).get('Procedure_Description', '')}".lower()
    cpt = _text(claim.get("cptCode"))
    return (
        cpt in {str(code) for code in range(99381, 99398)}
        or cpt in {"99213", "99214", "99215"}
        or "office visit" in description
        or "preventive visit" in description
    )


def _historical_intervention_line(database, historical_reference, family):
    if not historical_reference:
        return None
    candidates = [
        candidate
        for candidate in database.claims
        if candidate.get("claimId") != historical_reference.get("claimId")
        and candidate.get("memberId") == historical_reference.get("memberId")
        and _diagnosis_family(candidate) == family
        and _same_service_day(candidate, historical_reference)
        and _text(candidate.get("workbookFields", {}).get("Reference_Claim_Flag")).upper() == "Y"
        and _text(candidate.get("workbookFields", {}).get("Intervention_Performed")).upper() == "Y"
        and _positive_outcome(candidate)
        and not _office_or_preventive_visit(candidate)
    ]
    return sorted(candidates, key=lambda candidate: _text(candidate.get("claimId")))[0] if len(candidates) == 1 else None


def build_outcome_evidence(database, claim):
    """Return linked outcome evidence using only workbook fields and claim relationships."""
    source = claim
    claim_date = _text(claim.get("dos"))
    episode_id = claim.get("episodeId")
    family = _diagnosis_family(claim)
    claim_fields = claim.get("workbookFields", {})
    explicit_reference_id = _text(
        claim_fields.get("Reference_Claim_ID") or claim_fields.get("Reference claim")
    )
    linked_sequence_reference = next(
        (
            candidate for candidate in database.claims
            if candidate.get("memberId") == claim.get("memberId")
            and _diagnosis_family(candidate) == family
            and _text(candidate.get("workbookFields", {}).get("Reference_Claim_ID")) == claim.get("claimId")
            and _text(candidate.get("workbookFields", {}).get("Reason_Code")).upper() == "SYNTHETIC_SEQUENCE_PREVENTIVE"
        ),
        None,
    )
    historical_reference = linked_sequence_reference or (
        _claim_by_id(database, explicit_reference_id) if explicit_reference_id else None
    )
    historical_fields = historical_reference.get("workbookFields", {}) if historical_reference else {}
    historical_match = bool(
        historical_reference
        and _diagnosis_family(historical_reference) == family
        and _positive_outcome(historical_reference)
        and _preventive_visit(historical_reference)
        and _text(historical_fields.get("Reference_Claim_Flag")).upper() == "Y"
        and _text(historical_fields.get("Intervention_Performed")).upper() == "Y"
    )
    historical_intervention = _historical_intervention_line(database, historical_reference, family) if historical_match else None
    intervention_reference = historical_intervention or historical_reference
    if linked_sequence_reference and historical_match:
        source = linked_sequence_reference

    readmission_candidates = [
        candidate
        for candidate in database.claims
        if candidate.get("memberId") == claim.get("memberId")
        and candidate.get("claimId") != claim.get("claimId")
        and _diagnosis_family(candidate) == family
        and _text(candidate.get("dos")) > claim_date
        and (
            (
                linked_sequence_reference
                and _text(candidate.get("workbookFields", {}).get("Reference_Claim_ID")) == claim.get("claimId")
            )
            or (
                not linked_sequence_reference
                and (
                    candidate.get("episodeId") == episode_id
                    or _text(candidate.get("workbookFields", {}).get("Reference_Claim_ID")) == explicit_reference_id
                )
            )
        )
        and _text(candidate.get("workbookFields", {}).get("Related_Claim_Flag")).upper() == "Y"
        and (
            "READMISSION" in _text(candidate.get("workbookFields", {}).get("Reason_Code")).upper()
            or "WORSENING" in _text(candidate.get("workbookFields", {}).get("Reason_Code")).upper()
            or "hospital" in _text(candidate.get("placeOfService")).lower()
            or "hospital" in _text(candidate.get("cptDescription")).lower()
        )
    ] if historical_match else []
    predicted_readmission = min(
        readmission_candidates,
        key=lambda candidate: (_text(candidate.get("dos")), candidate.get("claimId", "")),
        default=None,
    )
    claim_is_later_hospitalization = bool(
        historical_match
        and not predicted_readmission
        and (
            "READMISSION" in _text(claim_fields.get("Reason_Code")).upper()
            or "hospital" in _text(claim.get("placeOfService")).lower()
            or "hospital" in _text(claim.get("cptDescription")).lower()
        )
    )
    linked_prediction_claim_id = None
    if claim_is_later_hospitalization:
        earlier_episode_claims = [
            candidate
            for candidate in database.claims
            if candidate.get("memberId") == claim.get("memberId")
            and candidate.get("claimId") != claim.get("claimId")
            and candidate.get("episodeId") == episode_id
            and _text(candidate.get("dos")) < claim_date
        ]
        earlier_prediction = min(
            earlier_episode_claims,
            key=lambda candidate: (_text(candidate.get("dos")), candidate.get("claimId", "")),
            default=None,
        )
        linked_prediction_claim_id = earlier_prediction.get("claimId") if earlier_prediction else None
        predicted_readmission = claim
    prediction_date = _date(claim.get("dos"))
    readmission_date = _date(predicted_readmission.get("dos")) if predicted_readmission else None
    predicted_gap_days = (
        (readmission_date - prediction_date).days
        if prediction_date and readmission_date and not claim_is_later_hospitalization
        else None
    )
    if not linked_sequence_reference and not _positive_outcome(claim) and episode_id and _preventive_visit(claim):
        candidates = [
            candidate
            for candidate in database.claims
            if candidate.get("memberId") == claim.get("memberId")
            and candidate.get("episodeId") == episode_id
            and _diagnosis_family(candidate) == family
            and _text(candidate.get("dos")) > claim_date
            and _positive_outcome(candidate)
        ]
        if candidates:
            source = min(candidates, key=lambda candidate: (_text(candidate.get("dos")), candidate.get("claimId", "")))

    fields = source.get("workbookFields", {})
    observed_no_readmission_days = _observed_no_readmission_days(
        intervention_reference,
        historical_fields.get("Episode_Duration_Days") if historical_match else fields.get("Episode_Duration_Days"),
    )
    condition = _text(fields.get("Condition_Resolved"))
    treatment = _text(fields.get("Treatment_Outcome"))
    follow_up = _text(fields.get("Follow_Up_Completed"))
    positive = _positive_outcome(source)
    source_date = _text(source.get("dos"))
    later_related = [
        candidate.get("claimId")
        for candidate in database.claims
        if candidate.get("memberId") == claim.get("memberId")
        and _diagnosis_family(candidate) == family
        and _text(candidate.get("dos")) > source_date
        and (
            not linked_sequence_reference
            or _text(candidate.get("workbookFields", {}).get("Reference_Claim_ID")) == claim.get("claimId")
        )
        and _text(candidate.get("workbookFields", {}).get("Related_Claim_Flag")).upper() == "Y"
        and not _positive_outcome(candidate)
    ] if positive else []
    # An explicit positive-outcome row can reuse a preventive CPT code, but it
    # must not serve as both the intervention and its own outcome evidence.
    # A claim can be both the preventive intervention and the recorded outcome evidence. Keep that claim as the source instead of searching backward and
    # incorrectly presenting an older preventive claim as the intervention.
    preventive_source = linked_sequence_reference or (claim if _preventive_visit(claim) else None)
    if positive and preventive_source is None and episode_id:
        prior_preventive_visits = [
            candidate
            for candidate in database.claims
            if candidate.get("memberId") == claim.get("memberId")
            and candidate.get("episodeId") == episode_id
            and _diagnosis_family(candidate) == family
            and _preventive_visit(candidate)
        ]
        if prior_preventive_visits:
            preventive_source = max(
                prior_preventive_visits,
                key=lambda candidate: (_text(candidate.get("dos")), candidate.get("claimId", "")),
            )

    outcome_summary = (
        f"Outcome claim {source.get('claimId')} dated {source_date} records "
        f"Condition resolved = {condition or 'Not recorded'}, "
        f"Treatment outcome = {treatment or 'Not recorded'}, and "
        f"Follow-up completed = {follow_up or 'Not recorded'}."
    )
    if historical_match:
        conclusion = (
            f"Historical claim {historical_reference.get('claimId')} records "
            f"{intervention_reference.get('cptDescription')} and an improved outcome. "
            "This historical outcome belongs to the reference patient, not to this prediction patient. "
            "This is comparison evidence for review; it does not establish that earlier care would prevent admission. "
        )
        if claim_is_later_hospitalization:
            conclusion += f"This is the linked later hospitalization after initial claim {linked_prediction_claim_id or 'not identified'}. Its billed charge is a review exposure, not verified savings."
        elif predicted_readmission:
            conclusion += (
                f"Related hospitalization claim {predicted_readmission.get('claimId')} occurred "
                f"{predicted_gap_days} days after the initial visit. Its billed charge is a review exposure, "
                "not verified savings."
            )
    elif positive and preventive_source:
        conclusion = (
            f"Preventive claim {preventive_source.get('claimId')} dated {preventive_source.get('dos')} "
            f"is linked to outcome claim {source.get('claimId')} dated {source_date}. "
            f"That outcome claim records Condition resolved = {condition or 'Not recorded'}, "
            f"Treatment outcome = {treatment or 'Not recorded'}, and "
            f"Follow-up completed = {follow_up or 'Not recorded'}. "
            "The records match on member, episode, and diagnosis family. "
            + (
                ((f"The source documents a {fields.get('Episode_Duration_Days')}-day follow-up period immediately after the first episode. " if fields.get("Episode_Duration_Days") else "") + "No later related claim is recorded for this diagnosis family; continuous observation is not established by absence alone.")
                if not later_related
                else f"Later related claims are recorded: {', '.join(later_related)}."
            )
        )
    elif positive:
        conclusion = outcome_summary
    else:
        conclusion = "No resolved or improved outcome is recorded for this claim or its linked episode."

    return {
        "condition_resolved": condition or "Not recorded",
        "intervention_plan": build_intervention_plan(claim),
        "treatment_outcome": treatment or "Not recorded",
        "follow_up_completed": follow_up or "Not recorded",
        "outcome_claim_flag": _text(fields.get("Outcome_Claim_Flag")) or ("Y" if positive else "N"),
        "reference_claim_flag": _text(fields.get("Reference_Claim_Flag")) or "N",
        "reference_claim_id": explicit_reference_id or (historical_reference.get("claimId") if linked_sequence_reference else None),
        "reference_outcome_supported": historical_match,
        "reference_member_id": historical_reference.get("memberId") if historical_reference else None,
        "reference_member_name": _patient_name(historical_reference),
        "reference_diagnosis": historical_reference.get("diagnosisDescription") if historical_reference else None,
        "reference_intervention": intervention_reference.get("cptDescription") if intervention_reference else None,
        "reference_intervention_claim_id": intervention_reference.get("claimId") if intervention_reference else None,
        "reference_intervention_member_id": intervention_reference.get("memberId") if intervention_reference else None,
        "reference_intervention_member_name": _patient_name(intervention_reference),
        "reference_intervention_service_date": intervention_reference.get("dos") if intervention_reference else None,
        "reference_treatment_outcome": (
            (
                f"{_without_fixed_day_claim(historical_fields.get('Treatment_Outcome'))}; "
                f"{observed_no_readmission_days} observed day(s) without a related readmission"
            )
            if historical_match and observed_no_readmission_days is not None
            else _text(historical_fields.get("Treatment_Outcome")) or None
        ),
        "historical_no_readmission_days": observed_no_readmission_days if (historical_match or (positive and not later_related)) else None,
        "recorded_follow_up_days": _integer(historical_fields.get("Episode_Duration_Days") if historical_match else fields.get("Episode_Duration_Days")),
        "recommended_intervention": intervention_reference.get("cptDescription") if historical_match else None,
        "prediction_claim_id": claim.get("claimId") if historical_match else None,
        "prediction_member_id": claim.get("memberId") if historical_match else None,
        "prediction_member_name": _patient_name(claim) if historical_match else None,
        "prediction_intervention_performed": _text(claim_fields.get("Intervention_Performed")) or None,
        "prediction_readmission_claim_id": predicted_readmission.get("claimId") if predicted_readmission else None,
        "prediction_readmission_member_name": _patient_name(predicted_readmission) if predicted_readmission else None,
        "prediction_readmission_gap_days": predicted_gap_days,
        "prediction_readmission_billed_amount": float(predicted_readmission.get("totalCharge") or 0) if predicted_readmission else None,
        "claim_is_later_hospitalization": claim_is_later_hospitalization,
        "linked_prediction_claim_id": linked_prediction_claim_id,
        "member_id": claim.get("memberId"),
        "member_name": _patient_name(claim),
        "episode_id": episode_id,
        "calculation_basis": "billed_charge_amount" if historical_match else None,
        "reference_source_row": {
            "claim_id": historical_reference.get("claimId"),
            "service_date": historical_reference.get("dos"),
            "billed_amount": float(historical_reference.get("totalCharge") or 0),
            "calculation_basis": "billed_charge_amount",
            "why_included": (
                "Used as the historical reference because this claim points to it via Reference_Claim_ID, "
                "it shares the same diagnosis family, and it records a preventive intervention with a positive outcome."
            ),
        } if historical_match else None,
        "prediction_readmission_source": {
            "claim_id": predicted_readmission.get("claimId"),
            "service_date": predicted_readmission.get("dos"),
            "billed_amount": float(predicted_readmission.get("totalCharge") or 0),
            "calculation_basis": "billed_charge_amount",
            "why_included": (
                "This claim is the later related hospitalization, so its own billed (charge) amount is the potentially avoided amount."
                if claim_is_later_hospitalization
                else (
                    "Included because it is the same member's later related hospitalization for this diagnosis family "
                    "(Related_Claim_Flag = Y), and its billed (charge) amount is the potentially avoided amount."
                )
            ),
        } if predicted_readmission else None,
        "related_claim_flag": _text(fields.get("Related_Claim_Flag")) or "Not recorded",
        "synthetic": (
            _text(fields.get("Reason_Code")).upper() in {"SYNTHETIC_PRESENTATION_CASE", "SYNTHETIC_OUTCOME_REFERENCE"}
            or linked_sequence_reference is not None
        ),
        "source_claim_id": source.get("claimId"),
        "source_service_date": source.get("dos"),
        "source_procedure": source.get("cptDescription"),
        "journey_claim_id": preventive_source.get("claimId") if preventive_source else claim.get("claimId"),
        "preventive_claim_id": preventive_source.get("claimId") if preventive_source else None,
        "preventive_service_date": preventive_source.get("dos") if preventive_source else None,
        "preventive_procedure": preventive_source.get("cptDescription") if preventive_source else None,
        "preventive_visit_identified": preventive_source is not None,
        "no_later_related_claims": positive and not later_related,
        "later_related_claim_ids": later_related,
        "status": "Historical reference evidence" if historical_match else "Recorded outcome evidence" if positive else "Not established",
        "conclusion": conclusion,
    }
