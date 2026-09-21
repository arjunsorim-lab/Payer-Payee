"""Evidence-labeled CSV exports.

Every exported value carries its evidence type so a downloaded file can never
present a model estimate, a recommendation or synthetic demonstration data as
recorded care.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

try:
    from . import evidence
except ImportError:  # pragma: no cover - script execution
    import evidence


def _text(value):
    return "" if value is None else str(value)


CLAIM_COLUMNS = (
    "claim_id",
    "member_id",
    "service_date",
    "cpt_code",
    "cpt_description",
    "diagnosis_code",
    "diagnosis_description",
    "payer",
    "provider",
    "charge_amount",
    "allowed_amount",
    "paid_amount",
    "claim_status",
    "episode_id",
)


def claims_csv(claims, dataset_synthetic=False):
    """Return a CSV string with one evidence-typed row per claim."""
    buffer = io.StringIO()
    fieldnames = list(CLAIM_COLUMNS) + [
        "evidence_type",
        "evidence_label",
        "evidence_basis",
        "source_workbook",
        "source_row",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for claim in claims:
        source = evidence.claim_source(claim, dataset_synthetic)
        writer.writerow({
            "claim_id": _text(claim.get("claimId")),
            "member_id": _text(claim.get("memberId")),
            "service_date": _text(claim.get("dos")),
            "cpt_code": _text(claim.get("cptCode")),
            "cpt_description": _text(claim.get("cptDescription")),
            "diagnosis_code": _text(claim.get("diagnosisCode")),
            "diagnosis_description": _text(claim.get("diagnosisDescription")),
            "payer": _text(claim.get("payer")),
            "provider": _text(claim.get("billingProvider")),
            "charge_amount": _text(claim.get("totalCharge")),
            "allowed_amount": _text(claim.get("allowed")),
            "paid_amount": _text(claim.get("paid")),
            "claim_status": _text(claim.get("status")),
            "episode_id": _text(claim.get("episodeId")),
            "evidence_type": source["evidence_type"],
            "evidence_label": source["evidence_label"],
            "evidence_basis": source.get("basis", ""),
            "source_workbook": _text(claim.get("sourceWorkbook")),
            "source_row": _text(claim.get("workbookSourceRow")),
        })
    return buffer.getvalue()


def recommendations_csv(plans):
    """Return a CSV string for intervention recommendations."""
    buffer = io.StringIO()
    fieldnames = (
        "review_id",
        "member_id",
        "anchor_claim_id",
        "scenario",
        "title",
        "evidence_type",
        "evidence_label",
        "recommendation",
        "follow_up_days",
        "timing_basis",
        "status",
        "evidence_used",
        "missing_evidence",
        "source_data_type",
        "verified_savings",
        "verification_status",
    )
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for plan in plans:
        quality = plan.get("evidence_quality") or {}
        validation = plan.get("savings_validation") or {}
        verified = (validation.get("amounts") or {}).get("verified_savings") or {}
        writer.writerow({
            "review_id": _text(plan.get("review_id")),
            "member_id": _text(plan.get("member_id")),
            "anchor_claim_id": _text(plan.get("anchor_claim_id")),
            "scenario": _text(plan.get("scenario")),
            "title": _text(plan.get("title")),
            # A recommendation is never recorded care.
            "evidence_type": evidence.RECOMMENDATION,
            "evidence_label": evidence.label(evidence.RECOMMENDATION),
            "recommendation": _text(plan.get("action")),
            "follow_up_days": _text(plan.get("follow_up_days")),
            "timing_basis": _text(plan.get("timing_basis")),
            "status": _text(plan.get("status")),
            "evidence_used": "; ".join(
                f"{item.get('input')}={item.get('value')}" for item in plan.get("evidence_used", [])
            ),
            "missing_evidence": "; ".join(plan.get("missing_evidence", [])),
            "source_data_type": _text(plan.get("source_data_type")),
            "verified_savings": _text(verified.get("value")),
            "verification_status": _text(validation.get("verification_status")),
        })
    return buffer.getvalue()


def export_filename(prefix):
    stamp = datetime.now().date().isoformat()
    return f"payerpayee-{prefix}-{stamp}.csv"
