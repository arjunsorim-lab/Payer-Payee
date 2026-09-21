"""Central evidence-type taxonomy.

Every number or statement PayerPayee exposes is one of a small, fixed set of
evidence types. Keeping the labels in one module lets the API, the browser UI
and the exports describe the same object with the same words, so a reviewer can
never mistake a model estimate, a recommendation or a reviewer observation for
recorded care or independently verified savings.

The taxonomy is intentionally conservative:

* ``recorded_claim_fact`` is a value read from the claims workbook.
* ``synthetic_demonstration`` is generated demonstration data, never real care.
* ``recommendation`` is proposed for review and is never recorded care.
* ``reviewer_observation`` is a human observation that is not independently verified.
* ``model_estimate`` is produced by the deterministic engines from historical peers.
* ``verified_savings`` requires post-intervention claims evidence; claims
  correlation alone can never produce it.
"""

from __future__ import annotations

RECORDED_CLAIM_FACT = "recorded_claim_fact"
SYNTHETIC_DEMONSTRATION = "synthetic_demonstration"
RECOMMENDATION = "recommendation"
REVIEWER_OBSERVATION = "reviewer_observation"
MODEL_ESTIMATE = "model_estimate"
VERIFIED_SAVINGS = "verified_savings"

EVIDENCE_TYPES = (
    RECORDED_CLAIM_FACT,
    SYNTHETIC_DEMONSTRATION,
    RECOMMENDATION,
    REVIEWER_OBSERVATION,
    MODEL_ESTIMATE,
    VERIFIED_SAVINGS,
)

EVIDENCE_LABELS = {
    RECORDED_CLAIM_FACT: "Recorded claim fact",
    SYNTHETIC_DEMONSTRATION: "Synthetic demonstration data",
    RECOMMENDATION: "Recommendation (not recorded care)",
    REVIEWER_OBSERVATION: "Reviewer observation (not independently verified)",
    MODEL_ESTIMATE: "Model estimate",
    VERIFIED_SAVINGS: "Independently verified savings",
}

EVIDENCE_DESCRIPTIONS = {
    RECORDED_CLAIM_FACT: "A value read directly from the configured claims source.",
    SYNTHETIC_DEMONSTRATION: "Generated demonstration data. It does not describe real care.",
    RECOMMENDATION: "Proposed for clinical review. It is not a recorded service.",
    REVIEWER_OBSERVATION: "A human reviewer's observation. It is not verified savings evidence.",
    MODEL_ESTIMATE: "Calculated by the deterministic engines from historical peers, not observed care.",
    VERIFIED_SAVINGS: "Calculated only from post-intervention claims evidence plus a recorded outcome.",
}

SYNTHETIC_REASON_TOKENS = (
    "SYNTHETIC",
    "SEQW-",
    "SEQP-",
)


def label(evidence_type):
    """Return the human-readable label for an evidence type."""
    return EVIDENCE_LABELS.get(evidence_type, "Unclassified evidence")


def describe(evidence_type):
    """Return the explanation for an evidence type."""
    return EVIDENCE_DESCRIPTIONS.get(evidence_type, "The source of this value was not classified.")


def legend():
    """Return the complete, ordered evidence-type legend for API responses."""
    return [
        {
            "evidence_type": evidence_type,
            "label": EVIDENCE_LABELS[evidence_type],
            "description": EVIDENCE_DESCRIPTIONS[evidence_type],
        }
        for evidence_type in EVIDENCE_TYPES
    ]


def entry(evidence_type, value=None, basis=None, **extra):
    """Build one labeled evidence entry."""
    payload = {
        "evidence_type": evidence_type,
        "evidence_label": label(evidence_type),
        "evidence_description": describe(evidence_type),
    }
    if value is not None:
        payload["value"] = value
    if basis:
        payload["basis"] = basis
    payload.update(extra)
    return payload


def is_synthetic(claim, dataset_synthetic=False):
    """Return True when a claim is demonstration data rather than real care."""
    fields = claim.get("workbookFields", claim) if isinstance(claim, dict) else {}
    if claim.get("isHistoricalReference") and not fields:
        return bool(dataset_synthetic)
    reason = str(fields.get("Reason_Code") or "").strip().upper()
    flag = str(fields.get("Synthetic_Flag") or "").strip().upper()
    synthetic_flag = flag in {"Y", "YES", "TRUE", "1"}
    explicit = claim.get("synthetic") if isinstance(claim, dict) else None
    return bool(
        dataset_synthetic
        or synthetic_flag
        or any(reason.startswith(token) for token in SYNTHETIC_REASON_TOKENS)
        or explicit is True
    )


def source_type(claim, dataset_synthetic=False):
    """Return the evidence type for a claim's own recorded values."""
    return SYNTHETIC_DEMONSTRATION if is_synthetic(claim, dataset_synthetic) else RECORDED_CLAIM_FACT


def claim_source(claim, dataset_synthetic=False):
    """Label a claim's provenance so the UI never presents demo data as care."""
    evidence_type = source_type(claim, dataset_synthetic)
    fields = claim.get("workbookFields", {}) if isinstance(claim, dict) else {}
    if evidence_type == SYNTHETIC_DEMONSTRATION:
        basis = (
            "This row is generated demonstration data. Its values do not describe "
            "recorded care and must never be presented as a claim fact."
        )
    else:
        basis = (
            f"Values read from {claim.get('sourceWorkbook') or 'the configured claims source'} "
            f"row {claim.get('workbookSourceRow') or 'n/a'}."
        )
    return entry(
        evidence_type,
        value=claim.get("claimId") if isinstance(claim, dict) else None,
        basis=basis,
        claim_id=claim.get("claimId") if isinstance(claim, dict) else None,
        member_id=claim.get("memberId") if isinstance(claim, dict) else None,
        source=fields.get("Reason_Code") if fields else None,
    )


def recorded_fact(value, basis=None, **extra):
    """Label a recorded claim fact."""
    return entry(RECORDED_CLAIM_FACT, value=value, basis=basis, **extra)


def model_estimate(value, basis=None, **extra):
    """Label a deterministic model estimate."""
    return entry(MODEL_ESTIMATE, value=value, basis=basis, **extra)


def recommendation(value, basis=None, **extra):
    """Label a proposed intervention that is not recorded care."""
    return entry(RECOMMENDATION, value=value, basis=basis, **extra)


def reviewer_observation(value, basis=None, **extra):
    """Label a reviewer observation."""
    return entry(REVIEWER_OBSERVATION, value=value, basis=basis, **extra)


def verified_savings(value, basis=None, **extra):
    """Label independently verified savings."""
    return entry(VERIFIED_SAVINGS, value=value, basis=basis, **extra)
