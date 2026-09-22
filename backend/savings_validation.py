"""Savings validation and evidence-quality metrics.

This module keeps six different money concepts apart and only calls savings
*verified* when post-intervention claims evidence supports it:

* predicted opportunity  -> model estimate
* billed charge           -> recorded claim fact
* paid amount             -> recorded claim fact
* estimated savings       -> model estimate
* reviewer-reported outcome -> reviewer observation
* independently verified savings -> verified savings (post-intervention claims only)

It also computes the cohort size, matching strength, follow-up completeness,
similarity, confidence and statistical uncertainty the evidence-quality and
validation panels display, replacing placeholder strings such as "not assessed"
with a calculated value whenever the data permits.
"""

from __future__ import annotations

import math
from statistics import mean, median, pstdev

try:
    from . import evidence
except ImportError:  # pragma: no cover - script execution
    import evidence

MIN_COHORT_SIZE = 5
MIN_SIMILARITY = 0.6
MIN_FOLLOW_UP_COMPLETENESS = 0.6
DEFAULT_OBSERVATION_WINDOW_DAYS = 180
RELATED_CLAIM_FLAG = "Related_Claim_Flag"


def _text(value):
    return "" if value is None else str(value).strip()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _money(value):
    return round(_number(value), 2)


def _day(value):
    from datetime import date

    text = _text(value)
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def wilson_interval(successes, total, z=1.96):
    """Wilson score interval for a proportion, safe when the sample is small."""
    if total <= 0:
        return {"low": 0.0, "high": 0.0, "method": "wilson_score"}
    phat = successes / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return {
        "low": round(max((centre - margin) / denominator, 0.0), 4),
        "high": round(min((centre + margin) / denominator, 1.0), 4),
        "method": "wilson_score",
    }


def mean_interval(values, z=1.96):
    """Normal-approximation confidence interval for a sample mean."""
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {
            "sample_size": 0,
            "mean": None,
            "standard_deviation": None,
            "standard_error": None,
            "ci_low": None,
            "ci_high": None,
            "relative_margin": None,
            "method": "normal_approximation",
        }
    sample_mean = mean(clean)
    standard_deviation = pstdev(clean) if len(clean) > 1 else 0.0
    standard_error = standard_deviation / math.sqrt(len(clean)) if clean else 0.0
    margin = z * standard_error
    return {
        "sample_size": len(clean),
        "mean": round(sample_mean, 2),
        "median": round(median(clean), 2),
        "standard_deviation": round(standard_deviation, 2),
        "standard_error": round(standard_error, 2),
        "ci_low": round(max(sample_mean - margin, 0.0), 2),
        "ci_high": round(sample_mean + margin, 2),
        "relative_margin": round(margin / sample_mean, 4) if sample_mean else None,
        "method": "normal_approximation",
    }


def matching_strength(matching_dimensions):
    """Return a 0..1 similarity score from the matched comparison dimensions."""
    if not matching_dimensions:
        return {"score": None, "matched": 0, "assessed": 0, "label": "Not calculated", "dimensions": {}}
    matched = sum(1 for value in matching_dimensions.values() if value)
    assessed = len(matching_dimensions)
    score = round(matched / assessed, 4) if assessed else None
    if score is None:
        label = "Not calculated"
    elif score >= 0.85:
        label = "Strong"
    elif score >= MIN_SIMILARITY:
        label = "Moderate"
    else:
        label = "Weak"
    return {"score": score, "matched": matched, "assessed": assessed, "label": label,
            "dimensions": {name: bool(value) for name, value in matching_dimensions.items()}}


def follow_up_completeness(expected, observed):
    """Return follow-up completeness from expected versus observed contacts."""
    expected = int(_number(expected, 0))
    observed = int(_number(observed, 0))
    if expected <= 0:
        ratio = 1.0 if observed > 0 else None
    else:
        ratio = round(min(observed / expected, 1.0), 4)
    if ratio is None:
        status = "not_assessed"
    elif ratio >= 1.0:
        status = "complete"
    elif ratio >= MIN_FOLLOW_UP_COMPLETENESS:
        status = "partial"
    else:
        status = "insufficient"
    return {
        "expected_contacts": expected,
        "observed_contacts": observed,
        "ratio": ratio,
        "status": status,
        "complete": bool(ratio is not None and ratio >= 1.0),
    }


def _window(anchor_service_date, days, today=None):
    from datetime import date, timedelta

    start = _day(anchor_service_date)
    days = int(_number(days, DEFAULT_OBSERVATION_WINDOW_DAYS))
    if not start:
        return {
            "start": None,
            "end": None,
            "days": days,
            "elapsed_days": None,
            "complete": False,
            "status": "not_assessed",
        }
    end = start + timedelta(days=days)
    reference = today or date.today()
    elapsed = (reference - start).days
    complete = reference >= end
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "elapsed_days": max(elapsed, 0),
        "complete": complete,
        "status": "complete" if complete else "in_progress",
    }


def _amount_block(predicted_opportunity, billed_charge, paid_amount, estimated_savings,
                  reviewer_reported_outcome, verified_amount, verified_basis, source_data_type=evidence.RECORDED_CLAIM_FACT):
    return {
        "predicted_opportunity": evidence.model_estimate(
            _money(predicted_opportunity) if predicted_opportunity is not None else None,
            "Deterministic cohort engine. Not observed care and not verified savings.",
        ),
        "billed_charge": evidence.entry(source_data_type,
            _money(billed_charge) if billed_charge is not None else None, "Charge_Amount read from the claims source."
        ),
        "paid_amount": evidence.entry(source_data_type,
            _money(paid_amount) if paid_amount is not None else None, "Paid_Amount read from the claims source."
        ),
        "estimated_savings": evidence.model_estimate(
            _money(estimated_savings) if estimated_savings is not None else None,
            "Model estimate of the opportunity. It is not a confirmed saving.",
        ),
        "reviewer_reported_outcome": evidence.reviewer_observation(
            reviewer_reported_outcome,
            "Recorded by a human reviewer. It is not independently verified.",
        ),
        "verified_savings": evidence.verified_savings(
            verified_amount,
            verified_basis,
        ),
    }


def build_evidence_quality(
    *,
    history_start=None,
    history_end=None,
    history_claim_count=0,
    claims_reviewed=None,
    missing_evidence=(),
    recorded_inputs=(),
    cohort_claim_count=0,
    cohort_member_count=0,
    matching_dimensions=None,
    expected_follow_up_contacts=0,
    observed_follow_up_contacts=0,
    data_source_type="recorded_claim",
):
    """Calculated evidence-quality block. No placeholder strings remain."""
    follow_up = follow_up_completeness(expected_follow_up_contacts, observed_follow_up_contacts)
    similarity = matching_strength(matching_dimensions)
    inputs = list(recorded_inputs)
    needed = list(missing_evidence)
    if inputs or needed:
        present = round(len(inputs) / (len(inputs) + len(needed)), 4)
    else:
        present = None
    if cohort_claim_count >= MIN_COHORT_SIZE and (similarity["score"] or 0) >= MIN_SIMILARITY:
        comparison_strength = "Supported by a comparable cohort"
    elif cohort_claim_count > 0:
        comparison_strength = f"Limited: {cohort_claim_count} comparable episode(s)"
    else:
        comparison_strength = "No comparable cohort available"
    return {
        "history_start": history_start,
        "history_end": history_end,
        "history_claim_count": int(_number(history_claim_count, 0)),
        "claims_reviewed": None if claims_reviewed is None else int(_number(claims_reviewed, 0)),
        "recorded_inputs": inputs,
        "missing_information": needed,
        "inputs_present_ratio": present,
        "cohort_size": int(_number(cohort_claim_count, 0)),
        "cohort_member_count": int(_number(cohort_member_count, 0)),
        "matching_strength": similarity["score"],
        "matching_strength_label": similarity["label"],
        "matching_dimensions": similarity["dimensions"],
        "follow_up_completeness": follow_up["ratio"],
        "follow_up_status": follow_up["status"],
        "follow_up_expected_contacts": follow_up["expected_contacts"],
        "follow_up_observed_contacts": follow_up["observed_contacts"],
        "data_source_type": data_source_type,
        # Claims correlation can support review; it never establishes causation.
        "causal_effectiveness": "not_established_from_claims_alone",
        "causal_evidence_status": "not_established",
        "comparison_strength": comparison_strength,
    }


def build_savings_validation(
    *,
    anchor_service_date=None,
    intervention_date=None,
    observation_window_days=DEFAULT_OBSERVATION_WINDOW_DAYS,
    predicted_opportunity=0.0,
    billed_charge=0.0,
    paid_amount=0.0,
    estimated_savings=0.0,
    cohort_paid_values=(),
    cohort_member_count=0,
    cohort_claim_ids=(),
    matching_dimensions=None,
    expected_follow_up_contacts=0,
    observed_follow_up_contacts=0,
    verification_claims=(),
    review=None,
    source_data_type="recorded_claim",
    today=None,
):
    """Validate a savings claim and explain why it may be unreliable.

    ``verification_claims`` must contain post-intervention related claims with a
    recorded service date and paid amount. Without them (and a recorded reviewer
    outcome) the result stays unverified and ``verified_savings.value`` is None.
    """
    review = review or {}
    window = _window(intervention_date or anchor_service_date, observation_window_days, today=today)
    cohort_values = [_number(value) for value in (cohort_paid_values or [])]
    cohort_stats = mean_interval(cohort_values)
    similarity = matching_strength(matching_dimensions)
    follow_up = follow_up_completeness(expected_follow_up_contacts, observed_follow_up_contacts)
    cohort_size = len(cohort_values) or int(_number(cohort_member_count, 0))

    intervention_day = _day(intervention_date)
    post_claims = []
    for claim in verification_claims or []:
        service_date = _day(claim.get("service_date") or claim.get("dos"))
        if not service_date or not intervention_day or service_date <= intervention_day:
            continue
        if service_date > _day(window["end"]) or claim.get("related") is not True:
            continue
        paid = claim.get("paid", claim.get("paid_amount"))
        if paid is None or not math.isfinite(_number(paid, float("nan"))) or _number(paid) < 0:
            continue
        if evidence.is_synthetic(claim):
            continue
        post_claims.append({"claim_id": claim.get("claim_id") or claim.get("claimId"),
                            "service_date": service_date.isoformat(),
                            "paid": _money(claim.get("paid") or claim.get("paid_amount"))})
    post_claims.sort(key=lambda item: item["service_date"])
    observed_post_paid = _money(sum(item["paid"] for item in post_claims))

    outcome_recorded = str(review.get("status") or "") == "outcome_recorded" or bool(
        review.get("outcome") and review.get("outcome_notes")
    )
    reasons = []
    if cohort_size < MIN_COHORT_SIZE:
        reasons.append(
            f"Comparison cohort has {cohort_size} episode(s); at least {MIN_COHORT_SIZE} are "
            "needed before an observed difference is treated as evidence."
        )
    if similarity["score"] is not None and similarity["score"] < MIN_SIMILARITY:
        reasons.append(
            f"Matching strength is {similarity['score']:.2f} ({similarity['label']}); the cohort "
            "differs on the comparison dimensions."
        )
    if follow_up["ratio"] is not None and follow_up["ratio"] < MIN_FOLLOW_UP_COMPLETENESS:
        reasons.append(
            f"Follow-up completeness is {follow_up['ratio']:.2f}; too few post-intervention "
            "contacts are recorded to observe the outcome."
        )
    if not window["complete"]:
        reasons.append(
            f"The {window['days']}-day observation window is not complete "
            f"({window['elapsed_days']} days elapsed)."
        )
    if source_data_type == evidence.SYNTHETIC_DEMONSTRATION:
        reasons.append("The underlying records are synthetic demonstration data, not recorded care.")
    if not post_claims:
        reasons.append("No post-intervention claims evidence is available in the observation window.")

    # An observed cohort difference plus a reviewer note does not independently
    # validate an intervention's savings. Keep it explicitly unverified.
    verified_amount = None
    observed_difference = (
        _money(cohort_stats["mean"] - observed_post_paid)
        if post_claims and cohort_stats["mean"] is not None else None
    )
    reasons.append("Independent savings validation is unavailable; claims comparisons and reviewer observations do not establish attributable savings.")
    if not intervention_day:
        reasons.append("No recorded intervention date is available.")
    if not outcome_recorded:
        reasons.append("No supported reviewer outcome has been recorded.")
    verification_status = "unverified" if post_claims else "insufficient_evidence"
    verification_basis = (
        f"Post-intervention claims {', '.join(item['claim_id'] or 'claim' for item in post_claims[:5])} "
        f"show {observed_post_paid:.2f} paid against a cohort baseline of "
        f"{cohort_stats['mean'] if cohort_stats['mean'] is not None else 'unknown'}."
        if post_claims
        else "No post-intervention claims evidence; verified savings cannot be calculated."
    )

    components = []
    if similarity["score"] is not None:
        components.append(similarity["score"])
    if follow_up["ratio"] is not None:
        components.append(follow_up["ratio"])
    components.append(min(cohort_size / 20, 1.0))
    components.append(1.0 if window["complete"] else 0.35)
    pieces = components + ([0.0] if source_data_type == evidence.SYNTHETIC_DEMONSTRATION else [1.0])
    confidence_score = round(sum(pieces) / len(pieces), 4)
    confidence_level = (
        "High" if confidence_score >= 0.8 else "Medium" if confidence_score >= 0.6 else "Low"
    )

    reliable = (
        cohort_size >= MIN_COHORT_SIZE
        and (similarity["score"] is None or similarity["score"] >= MIN_SIMILARITY)
        and (follow_up["ratio"] is None or follow_up["ratio"] >= MIN_FOLLOW_UP_COMPLETENESS)
        and window["complete"]
        and source_data_type != evidence.SYNTHETIC_DEMONSTRATION
    )
    if reliable and not reasons:
        reasons.append(
            "Statistically limited: the estimate remains a modelled opportunity, not an observed saving."
        )

    return {
        "observed_cohort_difference": evidence.model_estimate(observed_difference, "Unadjusted comparison; not attributable or verified savings."),
        "amounts": _amount_block(
            predicted_opportunity,
            billed_charge,
            paid_amount,
            estimated_savings,
            {"status": review.get("status"), "outcome": review.get("outcome"),
             "outcome_date": review.get("outcome_date"), "notes": review.get("outcome_notes")},
            verified_amount,
            verification_basis,
            source_data_type,
        ),
        "observation_window": window,
        "comparison_cohort": {
            "size": cohort_size,
            "member_count": int(_number(cohort_member_count, 0)),
            "claim_ids": list(cohort_claim_ids)[:20],
            "paid_statistics": cohort_stats,
            "similarity": similarity["score"],
            "matching_strength_label": similarity["label"],
            "matching_dimensions": similarity["dimensions"],
            "baseline": cohort_stats["mean"],
        },
        "follow_up_completeness": follow_up,
        "post_intervention_evidence": {
            "claim_count": len(post_claims),
            "claims": post_claims[:20],
            "observed_paid_amount": observed_post_paid,
        },
        "confidence": {
            "score": confidence_score,
            "level": confidence_level,
            "method": "cohort_size + similarity + follow-up completeness + window completeness",
        },
        "statistical_uncertainty": {
            **cohort_stats,
            "interpretation": (
                "Wide interval: too few comparable episodes to separate signal from noise."
                if cohort_stats["sample_size"] < MIN_COHORT_SIZE
                else "Interval around the cohort baseline paid amount."
            ),
        },
        "verification_status": verification_status,
        "verification_basis": verification_basis,
        "reliable": reliable,
        "reliability_reasons": reasons,
        "causal_effectiveness": "not_established_from_claims_alone",
        "causal_evidence_status": "not_established",
        "source_data_type": source_data_type,
    }
