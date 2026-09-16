"""Data-driven linking of preventive visits to later recorded outcomes."""


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
    return (
        "preventive" in description
        or cpt in {str(code) for code in range(99381, 99398)}
        or (_text(fields.get("Intervention_Performed")).upper() == "Y" and "visit" in description)
    )


def build_outcome_evidence(database, claim):
    """Return linked outcome evidence using only workbook fields and claim relationships."""
    source = claim
    claim_date = _text(claim.get("dos"))
    episode_id = claim.get("episodeId")
    family = _diagnosis_family(claim)
    if not _positive_outcome(claim) and episode_id and _preventive_visit(claim):
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
        and _text(candidate.get("workbookFields", {}).get("Related_Claim_Flag")).upper() == "Y"
        and not _positive_outcome(candidate)
    ] if positive else []
    # An explicit positive-outcome row can reuse a preventive CPT code, but it
    # must not serve as both the intervention and its own outcome evidence.
    preventive_source = claim if _preventive_visit(claim) and not positive else None
    if positive and preventive_source is None and episode_id:
        prior_preventive_visits = [
            candidate
            for candidate in database.claims
            if candidate.get("memberId") == claim.get("memberId")
            and candidate.get("episodeId") == episode_id
            and _diagnosis_family(candidate) == family
            and _text(candidate.get("dos")) < source_date
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
    if positive and preventive_source:
        conclusion = (
            f"Preventive claim {preventive_source.get('claimId')} dated {preventive_source.get('dos')} "
            f"is linked to outcome claim {source.get('claimId')} dated {source_date}. "
            f"That outcome claim records Condition resolved = {condition or 'Not recorded'}, "
            f"Treatment outcome = {treatment or 'Not recorded'}, and "
            f"Follow-up completed = {follow_up or 'Not recorded'}. "
            "The records match on member, episode, and diagnosis family. "
            + (
                "No later related claim is recorded for this diagnosis family."
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
        "treatment_outcome": treatment or "Not recorded",
        "follow_up_completed": follow_up or "Not recorded",
        "outcome_claim_flag": _text(fields.get("Outcome_Claim_Flag")) or ("Y" if positive else "N"),
        "reference_claim_flag": _text(fields.get("Reference_Claim_Flag")) or "N",
        "related_claim_flag": _text(fields.get("Related_Claim_Flag")) or "Not recorded",
        "synthetic": _text(fields.get("Reason_Code")).upper() in {"SYNTHETIC_PRESENTATION_CASE", "SYNTHETIC_OUTCOME_REFERENCE"},
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
        "status": "Recorded outcome evidence" if positive else "Not established",
        "conclusion": conclusion,
    }
