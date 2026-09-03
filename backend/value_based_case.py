"""Deterministic, partially data-driven value-based review scenarios.

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
VBC_RECENT_OFFICE_TO_TEST_DAYS = int(os.getenv("VBC_RECENT_OFFICE_TO_TEST_DAYS", "14"))

# This is deliberately small. It supports the urinary sequence used in the
# demonstration dataset without treating every diagnosis in the same ICD-10
# chapter as related. A clinician must approve or replace this list before
# production use.
VBC_REVIEW_CODE_GROUPS = (
    {
        "id": "urinary_infection_review_group",
        "label": "urinary-infection review group",
        "code_prefixes": ("N30", "N39.0", "N11"),
    },
)


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


def _diagnosis_code(claim):
    return _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")).upper()


def _review_group_for_pair(left_claim, right_claim):
    """Return the configured review group shared by two claims, if any."""
    left_code = _diagnosis_code(left_claim)
    right_code = _diagnosis_code(right_claim)
    for group in VBC_REVIEW_CODE_GROUPS:
        prefixes = group["code_prefixes"]
        if any(left_code.startswith(prefix) for prefix in prefixes) and any(
            right_code.startswith(prefix) for prefix in prefixes
        ):
            return group
    return None


def _is_in_review_group(claim, group):
    return bool(group) and any(
        _diagnosis_code(claim).startswith(prefix)
        for prefix in group["code_prefixes"]
    )


def _payer_id(claim):
    return _text(_field(claim, "Payer_ID", "payerId"))


def _provider_npi(claim):
    return _text(_field(claim, "Billing_Provider_NPI", "providerNpi"))


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


def _is_diagnostic_claim(claim):
    """Identify a billed test without making a clinical conclusion from it."""
    code = _text(_field(claim, "CPT_Code", "cptCode"))
    description = _text(_field(claim, "CPT_Description", "cptDescription")).lower()
    try:
        numeric_code = int(float(code))
    except (TypeError, ValueError):
        numeric_code = 0
    return (
        80000 <= numeric_code <= 89999
        or any(word in description for word in ("test", "culture", "analysis", "imaging", "laboratory"))
    )


def _reference_candidates(database, prediction_claim):
    """Return real claims that pre-date the selected later/prediction claim.

    Same-member references are preferred. Exact ICD-10-family matches are the
    normal rule. A limited fallback identifies a *recent office-to-test
    sequence* for the same person, payer, provider, and a configured review
    group. It is deliberately labelled as a data pattern, not a clinical
    pathway.
    """
    prediction_date = _day(_field(prediction_claim, "Service_Date_From", "dos"))
    prediction_member = _member_id(prediction_claim)
    prediction_family = _family(prediction_claim)
    prediction_payer = _payer_id(prediction_claim)
    prediction_provider = _provider_npi(prediction_claim)
    cutoff = prediction_date - timedelta(days=VBC_REFERENCE_LOOKBACK_DAYS)
    candidates = []

    # Historical-reference rows in this workbook are synthetic comparators and
    # must not become the basis for a member-level care review.
    for row in database.selectable_claims:
        row_date = _day(_field(row, "Service_Date_From", "dos"))
        if (
            not row_date
            or not cutoff <= row_date < prediction_date
            or _claim_id(row) == _claim_id(prediction_claim)
        ):
            continue
        same_member = _member_id(row) == prediction_member
        same_family = bool(prediction_family) and _family(row) == prediction_family
        days_before_prediction = (prediction_date - row_date).days
        review_group = _review_group_for_pair(row, prediction_claim)
        same_payer = bool(prediction_payer) and _payer_id(row) == prediction_payer
        same_provider = bool(prediction_provider) and _provider_npi(row) == prediction_provider
        recent_office_to_test_pattern = (
            same_member
            and 1 <= days_before_prediction <= VBC_RECENT_OFFICE_TO_TEST_DAYS
            and review_group is not None
            and same_payer
            and same_provider
            and _is_office_or_evaluation_claim(row)
            and _is_diagnostic_claim(prediction_claim)
        )
        if not same_family and not recent_office_to_test_pattern:
            continue

        if recent_office_to_test_pattern:
            relationship = (
                f"recent office visit followed by a test in the same {review_group['label']}"
            )
            method = "recent_same_member_office_to_test_pattern"
            evidence = [
                "same member",
                "same payer",
                "same billing provider",
                "same configured review group",
                "older office visit followed by a billed test",
            ]
        else:
            relationship = "same ICD-10 family"
            method = "same_icd10_family"
            evidence = ["same ICD-10 family"]
        candidates.append({
            "claim": row,
            "same_member": same_member,
            "same_family": same_family,
            "relationship": relationship,
            "method": method,
            "evidence": evidence,
            "review_group": review_group,
        })

    def rank(candidate):
        row = candidate["claim"]
        row_date = _day(_field(row, "Service_Date_From", "dos"))
        recent_pattern = candidate["method"] == "recent_same_member_office_to_test_pattern"
        return (
            int(recent_pattern),
            int(candidate["same_member"] and candidate["same_family"]),
            int(not candidate["same_member"] and candidate["same_family"]),
            int(_is_office_or_evaluation_claim(row)),
            -(prediction_date - row_date).days,
            _claim_id(row),
        )

    return sorted(candidates, key=rank, reverse=True)


def _later_related_claims(database, prediction_claim, relationship_scope, review_group=None):
    """Find later bills in the same transparent, non-clinical pattern scope."""
    prediction_date = _day(_field(prediction_claim, "Service_Date_From", "dos"))
    prediction_member = _member_id(prediction_claim)
    prediction_family = _family(prediction_claim)
    prediction_payer = _payer_id(prediction_claim)
    prediction_provider = _provider_npi(prediction_claim)
    end_date = prediction_date + timedelta(days=VBC_REPETITIVE_CLAIM_DAYS)
    rows = []
    for row in database.selectable_claims:
        row_date = _day(_field(row, "Service_Date_From", "dos"))
        if not row_date or not prediction_date < row_date <= end_date:
            continue
        if _member_id(row) != prediction_member:
            continue
        if relationship_scope == "same_icd10_family":
            related = _family(row) == prediction_family
        else:
            related = (
                _is_in_review_group(row, review_group)
                and _payer_id(row) == prediction_payer
                and _provider_npi(row) == prediction_provider
            )
        if related:
            rows.append(row)
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
    relationship_scope = reference_detail["method"]
    review_group = reference_detail.get("review_group")
    repeats = _later_related_claims(
        database,
        prediction_claim,
        relationship_scope,
        review_group,
    )
    repetitive_claims = []
    for row in repeats:
        item = _claim_summary(row)
        if relationship_scope == "same_icd10_family":
            inclusion_reason = (
                "This later bill is for the same person and the same kind of problem. "
                "It is included once in the possible amount."
            )
        else:
            inclusion_reason = (
                "This later bill is for the same person, payer, provider, and configured review group "
                "as the short office-to-test pattern. It is included once for review."
            )
        item["inclusion_reason"] = (
            inclusion_reason
        )
        repetitive_claims.append(item)
    repetitive_paid = _money(sum(item["paid_amount"] for item in repetitive_claims))
    present_claim_paid = prediction["paid_amount"]
    gross_spend_for_review = _money(present_claim_paid + repetitive_paid)
    source = "same patient" if reference_detail["same_member"] else "different patient"
    status = "Data-pattern review candidate"
    if relationship_scope == "recent_same_member_office_to_test_pattern":
        reference_reason = (
            f"This is an office visit for the same person, payer, and provider, {days_between} day(s) before the billed test. "
            f"Both codes are in the {review_group['label']}. This is a timing pattern to review, not proof of a clinical connection."
        )
    else:
        reference_reason = (
            f"This is an older bill for the {source}, from {days_between} day(s) before this bill. "
            "Both bills use the same kind of problem code."
        )
    limitations = [
        "Claims establish billed services, dates, and payer spend; they do not establish that care was missed or caused a condition to worsen.",
        "A clinician or validated care-pathway rule must confirm whether the later service belongs in the earlier episode and whether any later claim was avoidable.",
    ]
    if relationship_scope == "recent_same_member_office_to_test_pattern":
        limitations.append(
            f"The {review_group['label']} is a provisional rules list for this demo and needs clinician approval. It is weaker than an exact diagnosis-family match."
        )
    if synthetic_outcomes:
        limitations.append(
            "This workbook labels its outcome and intervention fields as synthetic/illustrative, so they are not used to confirm rectification or savings."
        )

    prediction_inclusion_reason = (
        f"This bill happened {days_between} day(s) after the older bill and uses the same kind of problem code. "
        "It is included once in the possible amount."
        if relationship_scope == "same_icd10_family"
        else (
            f"This test happened {days_between} day(s) after the older office visit and is in the {review_group['label']}. "
            "It is included once for review."
        )
    )
    claims_included = [{
        **prediction,
        "inclusion_reason": prediction_inclusion_reason,
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
            "method": relationship_scope,
            "evidence": reference_detail["evidence"],
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
