"""Deterministic, claims-only value-based rectification scenarios.

This module deliberately separates an observable billing pattern from a
clinical conclusion.  Claims can establish chronology, billed services, and
Payer Paid_Amount.  They cannot establish that care was missed, that health
worsened because of it, or that a later claim would have been avoided.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import os


VBC_REFERENCE_LOOKBACK_DAYS = int(os.getenv("VBC_REFERENCE_LOOKBACK_DAYS", "120"))
VBC_REPETITIVE_CLAIM_DAYS = int(os.getenv("VBC_REPETITIVE_CLAIM_DAYS", "120"))


def _field(claim, name, canonical=None, default=""):
    value = claim.get("workbookFields", {}).get(name)
    if value not in (None, ""):
        return value
    return claim.get(canonical or name, default)


def _text(value):
    return str(value or "").strip()


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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


def _claim_id(claim):
    return _text(_field(claim, "Claim_ID", "claimId"))


def _member_id(claim):
    return _text(_field(claim, "Member_ID", "memberId"))


def _family(claim):
    explicit = _text(_field(claim, "ICD10_Family"))
    diagnosis = _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode"))
    return explicit or diagnosis.split(".")[0][:3]


def _is_historical_reference(claim):
    return _text(_field(claim, "Is_Historical_Reference_Record")).upper() in {
        "Y", "YES", "TRUE", "1",
    }


def _claim_summary(claim):
    service_date = _day(_field(claim, "Service_Date_From", "dos"))
    return {
        "claim_id": _claim_id(claim),
        "member_id": _member_id(claim),
        "service_date": service_date.isoformat() if service_date else "",
        "icd10": _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "icd10_family": _family(claim),
        "diagnosis_description": _text(_field(
            claim, "ICD10_Diagnosis_Description", "diagnosisDescription"
        )),
        "cpt": _text(_field(claim, "CPT_Code", "cptCode")),
        "procedure_description": _text(_field(claim, "CPT_Description", "cptDescription")),
        "units": _number(_field(claim, "Units", "units")),
        "paid_amount": _money(_number(_field(claim, "Paid_Amount", "paid"))),
        "claim_status": _text(_field(claim, "Claim_Status_Description", "status")),
        "is_historical_reference": _is_historical_reference(claim),
    }


def _is_office_or_evaluation_claim(claim):
    code = _text(_field(claim, "CPT_Code", "cptCode"))
    description = _text(_field(claim, "CPT_Description", "cptDescription")).lower()
    return code.startswith("992") or "office" in description or "evaluation" in description


def _reference_candidates(database, prediction_claim):
    """Return real claims that pre-date the selected later/prediction claim.

    Same-member references are preferred. The reference must share the same
    ICD-10 family as the selected claim. A broad ICD-10 chapter (for example,
    every diagnosis beginning with "Z") is not enough evidence to calculate
    a claim-level amount.
    """
    prediction_date = _day(_field(prediction_claim, "Service_Date_From", "dos"))
    prediction_member = _member_id(prediction_claim)
    prediction_family = _family(prediction_claim)
    cutoff = prediction_date - timedelta(days=VBC_REFERENCE_LOOKBACK_DAYS)
    candidates = []

    for row in database.claims:
        row_date = _day(_field(row, "Service_Date_From", "dos"))
        if (
            not row_date
            or not cutoff <= row_date < prediction_date
            or _claim_id(row) == _claim_id(prediction_claim)
        ):
            continue
        same_member = _member_id(row) == prediction_member
        if same_member and _is_historical_reference(row):
            # Synthetic/archival peer rows must not become the patient's own
            # documented reference care.
            continue
        same_family = bool(prediction_family) and _family(row) == prediction_family
        if not same_family:
            continue

        candidates.append({
            "claim": row,
            "same_member": same_member,
            "same_family": same_family,
            "relationship": "same ICD-10 family",
        })

    def rank(candidate):
        row = candidate["claim"]
        row_date = _day(_field(row, "Service_Date_From", "dos"))
        days_before_prediction = (prediction_date - row_date).days
        return (
            int(candidate["same_member"] and candidate["same_family"]),
            int(not candidate["same_member"] and candidate["same_family"]),
            int(_is_office_or_evaluation_claim(row)),
            -days_before_prediction,
            _claim_id(row),
        )

    return sorted(candidates, key=rank, reverse=True)


def _later_related_claims(database, prediction_claim, relationship_scope):
    """Find later same-member claims in the selected claims relationship scope."""
    prediction_date = _day(_field(prediction_claim, "Service_Date_From", "dos"))
    prediction_member = _member_id(prediction_claim)
    prediction_family = _family(prediction_claim)
    end_date = prediction_date + timedelta(days=VBC_REPETITIVE_CLAIM_DAYS)
    rows = [
        row for row in database.selectable_claims
        if _member_id(row) == prediction_member
        and _family(row) == prediction_family
        and (row_date := _day(_field(row, "Service_Date_From", "dos")))
        and prediction_date < row_date <= end_date
    ]
    return sorted(rows, key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)))


def _workbook_has_synthetic_outcome_fields(database):
    notes = " ".join(
        " ".join(_text(value) for value in row.values())
        for row in getattr(database, "data_notes_rows", ())
    ).lower()
    return "synthetic/illustrative placeholder" in notes or "synthetic historical reference" in notes


def build_value_based_case_for_claim(database, claim_number):
    """Build an auditable, claims-only rectification scenario for one claim.

    A returned potential amount is an amount for payer review.  It is never a
    confirmed saving and is not allowed to change a clinical or adjudication
    status.
    """
    prediction_claim = database.find_claim(claim_number, selectable_only=True)
    if not prediction_claim or _is_historical_reference(prediction_claim):
        raise KeyError(f"Selectable claim not found: {claim_number}")
    prediction_date = _day(_field(prediction_claim, "Service_Date_From", "dos"))
    prediction = _claim_summary(prediction_claim)
    candidates = _reference_candidates(database, prediction_claim)
    synthetic_outcomes = _workbook_has_synthetic_outcome_fields(database)

    if not candidates:
        return {
            "available": False,
            "status": "No claims-based rectification scenario",
            "reason": (
                "We could not find an older bill for the same kind of problem in the "
                f"previous {VBC_REFERENCE_LOOKBACK_DAYS} days."
            ),
            "prediction_claim": prediction,
            "avoidable_repetitive_claims": [],
            "claims_included": [],
            "calculation": {
                "available": False,
                "present_claim_paid": prediction["paid_amount"],
                "later_related_paid": 0.0,
                "potential_payer_spend_for_review": 0.0,
                "formula": "No matching older bill was found",
                "reason": "The possible amount is $0.00 because we do not have a fair older bill to compare.",
            },
            "clinical_review_required": True,
            "data_limitations": [
                "Claims do not identify a missed clinical intervention or prove that a later claim would have been avoided.",
            ],
        }

    reference_detail = candidates[0]
    reference_claim = reference_detail["claim"]
    reference = _claim_summary(reference_claim)
    days_between = (prediction_date - _day(_field(reference_claim, "Service_Date_From", "dos"))).days
    relationship_scope = "same ICD-10 family"
    repeats = _later_related_claims(database, prediction_claim, relationship_scope)
    repetitive_claims = []
    for row in repeats:
        item = _claim_summary(row)
        item["inclusion_reason"] = (
            "This later bill is for the same person and the same kind of problem. "
            "It is included once in the possible amount."
        )
        repetitive_claims.append(item)
    repetitive_paid = _money(sum(item["paid_amount"] for item in repetitive_claims))
    present_claim_paid = prediction["paid_amount"]
    gross_spend_for_review = _money(present_claim_paid + repetitive_paid)
    source = "same patient" if reference_detail["same_member"] else "different patient"
    status = "Rectification candidate"
    reference_reason = (
        f"This is an older bill for the {source}, from {days_between} day(s) before this bill. "
        "Both bills use the same kind of problem code."
    )
    limitations = [
        "Claims establish billed services, dates, and payer spend; they do not establish that care was missed or caused a condition to worsen.",
        "A clinician or validated care-pathway rule must confirm whether the later service belongs in the earlier episode and whether any later claim was avoidable.",
    ]
    if synthetic_outcomes:
        limitations.append(
            "This workbook labels its outcome and intervention fields as synthetic/illustrative, so they are not used to confirm rectification or savings."
        )

    claims_included = [{
        **prediction,
        "inclusion_reason": (
            f"This bill happened {days_between} day(s) after the older bill and uses the same kind of problem code. "
            "It is included once in the possible amount."
        ),
    }, *repetitive_claims]
    return {
        "available": True,
        "status": status,
        "clinical_review_required": True,
        "reference_claim": reference,
        "prediction_claim": prediction,
        "reference_selection": {
            "source": source,
            "relationship": reference_detail["relationship"],
            "days_before_prediction": days_between,
            "reason": reference_reason,
        },
        "improved_claim": {
            **prediction,
            "label": "Proposed earlier-service review",
            "reason": (
                f"The selected service ({prediction['cpt'] or 'billing code not recorded'}) was billed after the reference claim. "
                "A clinical reviewer must decide whether it was appropriate to provide, schedule, or monitor earlier."
            ),
        },
        "avoidable_repetitive_claims": repetitive_claims,
        "claims_included": claims_included,
        "calculation": {
            "available": True,
            "present_claim_paid": present_claim_paid,
            "later_related_paid": repetitive_paid,
            "potential_payer_spend_for_review": gross_spend_for_review,
            "formula": "We add the bills named below, one time each.",
            "reason": "This is a possible amount to check. It is not money we have definitely saved.",
        },
        "data_limitations": limitations,
    }
