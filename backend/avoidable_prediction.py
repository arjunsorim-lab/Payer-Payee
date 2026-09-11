"""Deterministic predicted avoidable-spend model.

This module forecasts the expected incremental cost of a potentially avoidable
related repeat within 90 days. It never uses the selected claim's adjudicated
outcome as a feature and it remains separate from retrospective validated
avoidable spend.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from datetime import date
from statistics import median
from threading import RLock

import numpy as np


PRIOR_STRENGTH = float(
    os.getenv(
        "PREDICTION_PRIOR_STRENGTH",
        os.getenv("REPEAT_PRIOR_STRENGTH", "8"),
    )
)
REPEAT_PRIOR_STRENGTH = PRIOR_STRENGTH
AVOIDABLE_PRIOR_STRENGTH = float(
    os.getenv("AVOIDABLE_PRIOR_STRENGTH", str(REPEAT_PRIOR_STRENGTH))
)
MIN_HIERARCHY_PEERS = int(os.getenv("AVOIDABLE_MIN_HIERARCHY_PEERS", "3"))
PREDICTION_HORIZON_DAYS = 90

FIELD_ALIASES = {
    "member_id": ("Member_ID", "MemberID"),
    "claim_id": ("Claim_ID", "ClaimID"),
    "service_date": ("Service_Date_From", "Service_Date", "Date_of_Service"),
    "payer_id": ("Payer_ID", "PayerID"),
    "provider_id": (
        "Rendering_Provider_NPI",
        "Billing_Provider_NPI",
        "Provider_NPI",
    ),
    "cpt": ("CPT_Code", "Procedure_Code", "HCPCS_Code"),
    "diagnosis": ("ICD10_Diagnosis_Code", "Diagnosis_Code", "ICD10_Code"),
    "diagnosis_family": ("ICD10_Family", "Diagnosis_Family"),
    "units": ("Units", "Service_Units"),
    "pos": ("Place_of_Service_Code", "POS_Code"),
    "episode_id": ("Episode_ID", "EpisodeID"),
    "related": ("Related_Claim_Flag", "Related_Claim_Indicator"),
    "repeat_reason": ("Repeat_Visit_Reason", "Repeat_Reason"),
    "condition_resolved": ("Condition_Resolved",),
    "treatment_outcome": ("Treatment_Outcome",),
    "follow_up_completed": ("Follow_Up_Completed",),
    "intervention_performed": ("Intervention_Performed",),
    "allowed": ("Allowed_Amount", "AllowedAmount"),
    "paid": ("Paid_Amount", "PaidAmount"),
}

_CACHE = {}
_LOCK = RLock()


def clear_avoidable_prediction_cache():
    with _LOCK:
        _CACHE.clear()


def _field(claim, logical_name, default=""):
    fields = claim.get("workbookFields", {})
    for alias in FIELD_ALIASES[logical_name]:
        if alias in fields and fields[alias] not in (None, ""):
            return fields[alias]
    canonical = {
        "member_id": "memberId",
        "claim_id": "claimId",
        "service_date": "dos",
        "payer_id": "payerId",
        "provider_id": "billingProviderNpi",
        "cpt": "cptCode",
        "diagnosis": "diagnosisCode",
        "units": "units",
        "pos": "placeOfServiceCode",
        "episode_id": "episodeId",
        "allowed": "allowed",
        "paid": "paid",
    }.get(logical_name)
    return claim.get(canonical, default) if canonical else default


def _text(value):
    return str(value or "").strip()


def _lower(value):
    return _text(value).lower()


def _yes(value):
    return _text(value).upper() in {"Y", "YES", "TRUE", "1"}


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value):
    return round(max(_number(value), 0.0), 2)


def _date(value):
    try:
        return date.fromisoformat(_text(value)[:10])
    except ValueError:
        return None


def _diagnosis_family(claim):
    explicit = _text(_field(claim, "diagnosis_family"))
    diagnosis = _text(_field(claim, "diagnosis"))
    return explicit or diagnosis.split(".")[0][:3]


def _procedure_family(claim):
    code = "".join(character for character in _text(_field(claim, "cpt")) if character.isalnum())
    return code[:3]


def _identity(claim):
    return {
        "member": _text(_field(claim, "member_id")),
        "payer": _text(_field(claim, "payer_id")),
        "provider": _text(_field(claim, "provider_id")),
        "cpt": _text(_field(claim, "cpt")),
        "procedure_family": _procedure_family(claim),
        "diagnosis_family": _diagnosis_family(claim),
        "pos": _text(_field(claim, "pos")),
        "units": _text(_field(claim, "units")),
    }


def _is_planned_follow_up(claim):
    reason = _lower(_field(claim, "repeat_reason"))
    return any(token in reason for token in ("planned follow-up", "scheduled follow-up", "routine follow-up"))


def _avoidable_evidence(claim):
    if _is_planned_follow_up(claim):
        return False
    reason = _lower(_field(claim, "repeat_reason"))
    resolved = _lower(_field(claim, "condition_resolved"))
    outcome = _lower(_field(claim, "treatment_outcome"))
    follow_up = _lower(_field(claim, "follow_up_completed"))
    supported_reason = any(
        token in reason
        for token in (
            "persistent",
            "symptom recurrence",
            "incomplete prior treatment",
            "unplanned",
        )
    )
    unresolved = resolved in {"n", "no", "ongoing", "not resolved", "unresolved"}
    poor_outcome = outcome in {"no change", "worsened", "not resolved", "unresolved"}
    missed_follow_up = follow_up in {"n", "no", "missed", "incomplete"}
    return supported_reason or unresolved or poor_outcome or missed_follow_up


def _historical_episode_observations(database, cutoff):
    cutoff_date = _date(cutoff)
    prior = [
        row
        for row in database.historical_claims
        if _date(_field(row, "service_date"))
        and cutoff_date
        and _date(_field(row, "service_date")) < cutoff_date
    ]
    grouped = defaultdict(list)
    for row in prior:
        episode_id = _text(_field(row, "episode_id")) or _text(_field(row, "claim_id"))
        grouped[episode_id].append(row)

    observations = []
    for episode_id, rows in grouped.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                _text(_field(row, "service_date")),
                _text(_field(row, "claim_id")),
            ),
        )
        anchor = ordered[0]
        anchor_date = _date(_field(anchor, "service_date"))
        repeats = [
            row
            for row in ordered[1:]
            if _yes(_field(row, "related"))
            and _date(_field(row, "service_date"))
            and 0
            < (_date(_field(row, "service_date")) - anchor_date).days
            <= PREDICTION_HORIZON_DAYS
        ]
        repeat_days = [
            (_date(_field(row, "service_date")) - anchor_date).days
            for row in repeats
        ]
        total_allowed = sum(_number(_field(row, "allowed")) for row in [anchor, *repeats])
        total_paid = sum(_number(_field(row, "paid")) for row in [anchor, *repeats])
        extra_allowed = max(total_allowed - _number(_field(anchor, "allowed")), 0.0)
        extra_paid = max(total_paid - _number(_field(anchor, "paid")), 0.0)
        observations.append(
            {
                "episode_id": episode_id,
                "anchor": anchor,
                "identity": _identity(anchor),
                "repeat_30": bool(repeat_days and min(repeat_days) <= 30),
                "repeat_60": bool(repeat_days and min(repeat_days) <= 60),
                "repeat_90": bool(repeats),
                "avoidable": bool(repeats) and any(_avoidable_evidence(row) for row in repeats),
                "extra_allowed": extra_allowed,
                "extra_paid": extra_paid,
                "repeat_claim_ids": [_text(_field(row, "claim_id")) for row in repeats],
            }
        )
    return observations


def _rules(selected):
    def equal(observation, *keys):
        identity = observation["identity"]
        return all(selected[key] and identity[key] == selected[key] for key in keys)

    return [
        ("same member + CPT + diagnosis family", lambda row: equal(row, "member", "cpt", "diagnosis_family")),
        ("payer + provider + CPT + diagnosis family + POS + units", lambda row: equal(row, "payer", "provider", "cpt", "diagnosis_family", "pos", "units")),
        ("payer + provider + CPT + diagnosis family + POS", lambda row: equal(row, "payer", "provider", "cpt", "diagnosis_family", "pos")),
        ("payer + CPT + diagnosis family + POS", lambda row: equal(row, "payer", "cpt", "diagnosis_family", "pos")),
        ("payer + CPT + diagnosis family", lambda row: equal(row, "payer", "cpt", "diagnosis_family")),
        ("CPT + diagnosis family", lambda row: equal(row, "cpt", "diagnosis_family")),
        ("diagnosis family + POS", lambda row: equal(row, "diagnosis_family", "pos")),
        ("diagnosis family", lambda row: equal(row, "diagnosis_family")),
        ("global historical baseline", lambda row: True),
    ]


def _local_and_external(observations, selected, *, repeats_only=False):
    source = [row for row in observations if row["repeat_90"]] if repeats_only else observations
    rules = _rules(selected)
    local = []
    local_level = "no member-level observations"
    for label, matcher in rules[:1]:
        matches = [row for row in source if matcher(row)]
        if matches:
            local = matches
            local_level = label
            break
    external = []
    external_level = rules[-1][0]
    for label, matcher in rules[1:]:
        matches = [row for row in source if matcher(row)]
        if len(matches) >= MIN_HIERARCHY_PEERS or label == rules[-1][0]:
            external = matches
            external_level = label
            break
    return local, local_level, external, external_level


def _cost_peers(observations, selected):
    repeats = [row for row in observations if row["repeat_90"]]
    for depth, (label, matcher) in enumerate(_rules(selected)[1:], start=2):
        matches = [row for row in repeats if matcher(row)]
        if len(matches) >= MIN_HIERARCHY_PEERS or label == "global historical baseline":
            return matches, label, depth
    return [], "no historical repeat-cost observations", len(_rules(selected))


def _smoothed_probability(local, external, flag, prior_strength):
    local_count = sum(bool(row[flag]) for row in local)
    external_count = sum(bool(row[flag]) for row in external)
    external_rate = external_count / len(external) if external else 0.0
    denominator = len(local) + prior_strength
    probability = (
        (local_count + prior_strength * external_rate) / denominator
        if denominator
        else 0.0
    )
    effective_n = max(denominator, 1.0)
    spread = math.sqrt(max(probability * (1 - probability), 0.0) / effective_n)
    return {
        "value": min(max(probability, 0.0), 1.0),
        "low": min(max(probability - spread, 0.0), 1.0),
        "high": min(max(probability + spread, 0.0), 1.0),
        "local_numerator": local_count,
        "local_denominator": len(local),
        "local_rate": local_count / len(local) if local else 0.0,
        "prior_strength": prior_strength,
        "external_numerator": external_count,
        "external_denominator": len(external),
        "external_rate": external_rate,
        "blend_weights": {
            "local": len(local) / denominator if denominator else 0.0,
            "external_prior": prior_strength / denominator if denominator else 0.0,
        },
        "final_probability": min(max(probability, 0.0), 1.0),
    }


def _confidence(repeat_evidence, avoidable_evidence, cost_count, peer_level, interval_width, member_history):
    levels = [label for label, _ in _rules({key: "x" for key in _identity({})})]
    depth = levels.index(peer_level) if peer_level in levels else len(levels) - 1
    observation_score = min(math.log1p(repeat_evidence) / math.log(51), 1.0)
    avoidable_score = min(math.log1p(avoidable_evidence) / math.log(31), 1.0)
    cost_score = min(math.log1p(cost_count) / math.log(31), 1.0)
    specificity = max(0.15, 1 - depth / max(len(levels) - 1, 1))
    member_score = min(member_history / 5, 1.0)
    width_penalty = min(max(interval_width, 0.0), 1.0)
    raw = (
        0.25 * observation_score
        + 0.20 * avoidable_score
        + 0.25 * cost_score
        + 0.20 * specificity
        + 0.10 * member_score
        - 0.10 * width_penalty
    )
    score = int(round(min(max(raw, 0.05), 0.95) * 100))
    return {
        "score": score,
        "level": "High" if score >= 80 else "Medium" if score >= 55 else "Low",
        "drivers": [
            f"{repeat_evidence} recurrence observation(s)",
            f"{avoidable_evidence} avoidability observation(s)",
            f"{cost_count} repeat-cost episode(s)",
        ],
        "penalties": (
            [f"Repeat-cost estimate required {peer_level} fallback"]
            if depth >= 4
            else []
        ),
    }


def build_predicted_avoidable_spend(database, claim, predicted_allowed, predicted_paid):
    cutoff = _text(_field(claim, "service_date"))
    cache_key = (
        database.workbook_hash,
        _text(_field(claim, "claim_id")),
        cutoff,
        round(float(predicted_allowed or 0), 4),
        round(float(predicted_paid or 0), 4),
        REPEAT_PRIOR_STRENGTH,
        AVOIDABLE_PRIOR_STRENGTH,
        MIN_HIERARCHY_PEERS,
    )
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            return cached

    observations = _historical_episode_observations(database, cutoff)
    selected = _identity(claim)
    local, local_level, external, external_level = _local_and_external(
        observations, selected
    )
    repeat = {
        horizon: _smoothed_probability(
            local,
            external,
            f"repeat_{horizon}",
            REPEAT_PRIOR_STRENGTH,
        )
        for horizon in (30, 60, 90)
    }
    # Cumulative probabilities must remain monotonic after smoothing.
    repeat[60]["value"] = max(repeat[60]["value"], repeat[30]["value"])
    repeat[90]["value"] = max(repeat[90]["value"], repeat[60]["value"])
    repeat[60]["low"] = max(repeat[60]["low"], repeat[30]["low"])
    repeat[90]["low"] = max(repeat[90]["low"], repeat[60]["low"])
    repeat[60]["high"] = max(repeat[60]["high"], repeat[30]["high"])
    repeat[90]["high"] = max(repeat[90]["high"], repeat[60]["high"])

    local_repeats, local_avoid_level, external_repeats, external_avoid_level = (
        _local_and_external(observations, selected, repeats_only=True)
    )
    avoidable = _smoothed_probability(
        local_repeats,
        external_repeats,
        "avoidable",
        AVOIDABLE_PRIOR_STRENGTH,
    )
    cost_peers, cost_level, _ = _cost_peers(observations, selected)
    allowed_costs = [row["extra_allowed"] for row in cost_peers]
    paid_costs = [row["extra_paid"] for row in cost_peers]

    raw_allowed = float(median(allowed_costs)) if allowed_costs else 0.0
    raw_paid = float(median(paid_costs)) if paid_costs else 0.0
    # Incremental cost cannot exceed the corresponding predicted repeat cost.
    expected_allowed = min(raw_allowed, max(float(predicted_allowed or 0), 0.0))
    expected_paid = min(raw_paid, max(float(predicted_paid or 0), 0.0))
    allowed_q25 = min(
        float(np.percentile(allowed_costs, 25)) if allowed_costs else 0.0,
        max(float(predicted_allowed or 0), 0.0),
    )
    allowed_q75 = min(
        float(np.percentile(allowed_costs, 75)) if allowed_costs else 0.0,
        max(float(predicted_allowed or 0), 0.0),
    )
    paid_q25 = min(
        float(np.percentile(paid_costs, 25)) if paid_costs else 0.0,
        max(float(predicted_paid or 0), 0.0),
    )
    paid_q75 = min(
        float(np.percentile(paid_costs, 75)) if paid_costs else 0.0,
        max(float(predicted_paid or 0), 0.0),
    )

    repeat90 = round(repeat[90]["value"], 6)
    avoid_probability = round(avoidable["value"], 6)
    expected_allowed = _money(expected_allowed)
    expected_paid = _money(expected_paid)
    predicted_value = repeat90 * avoid_probability * expected_allowed
    predicted_paid_value = repeat90 * avoid_probability * expected_paid
    low = repeat[90]["low"] * avoidable["low"] * allowed_q25
    high = repeat[90]["high"] * avoidable["high"] * allowed_q75
    paid_low = repeat[90]["low"] * avoidable["low"] * paid_q25
    paid_high = repeat[90]["high"] * avoidable["high"] * paid_q75
    value = _money(predicted_value)
    provider_value = _money(predicted_paid_value)
    low = min(_money(low), value)
    high = max(_money(high), value)
    paid_low = min(_money(paid_low), provider_value)
    paid_high = max(_money(paid_high), provider_value)
    interval_width = (
        (high - low) / predicted_value if predicted_value > 0 else 1.0
    )
    confidence = _confidence(
        len(local) + len(external),
        len(local_repeats) + len(external_repeats),
        len(cost_peers),
        cost_level,
        interval_width,
        len(local),
    )
    zero_reasons = []
    if repeat90 == 0:
        zero_reasons.append("Blended 90-day repeat probability mathematically evaluated to zero.")
    if avoid_probability == 0:
        zero_reasons.append("Blended avoidable-if-repeat probability mathematically evaluated to zero.")
    if expected_allowed == 0:
        zero_reasons.append("Historical incremental repeat allowed cost mathematically evaluated to zero.")

    formula_trace = {
        "formula": "repeat_probability_90d × avoidable_given_repeat_probability × expected_extra_repeat_allowed_cost",
        "repeat_probability_90d": round(repeat90, 6),
        "avoidable_given_repeat_probability": round(avoid_probability, 6),
        "expected_extra_repeat_allowed_cost": _money(expected_allowed),
        "predicted_avoidable_spend": value,
    }
    result = {
        "predicted_avoidable_spend": {
            "value": value,
            "low": low,
            "high": high,
            "confidence": round(confidence["score"] / 100, 4),
            "confidence_detail": confidence,
            "method": "Bayesian-smoothed recurrence × avoidability × hierarchical historical incremental repeat cost",
            "repeat_probability_90d": round(repeat90, 6),
            "avoidable_given_repeat_probability": round(avoid_probability, 6),
            "expected_extra_repeat_allowed_cost": _money(expected_allowed),
            "expected_extra_repeat_provider_payment": _money(expected_paid),
            "peer_count": len(cost_peers),
            "peer_level": cost_level,
            "zero_reasons": zero_reasons,
            "formula_trace": formula_trace,
        },
        "predicted_avoidable_provider_payment": {
            "value": provider_value,
            "low": paid_low,
            "high": paid_high,
            "confidence": round(confidence["score"] / 100, 4),
            "method": "Bayesian-smoothed recurrence × avoidability × hierarchical historical incremental repeat provider payment",
            "repeat_probability_90d": round(repeat90, 6),
            "avoidable_given_repeat_probability": round(avoid_probability, 6),
            "expected_extra_repeat_paid_cost": _money(expected_paid),
            "peer_count": len(cost_peers),
            "peer_level": cost_level,
            "zero_reasons": zero_reasons,
        },
        "repeat_probability": {
            "probability_30d": round(repeat[30]["value"], 6),
            "probability_60d": round(repeat[60]["value"], 6),
            "probability_90d": round(repeat90, 6),
            "low_90d": round(repeat[90]["low"], 6),
            "high_90d": round(repeat[90]["high"], 6),
            "local_level": local_level,
            "external_level": external_level,
            "evidence": repeat[90],
            "horizons": {
                f"{horizon}d": repeat[horizon] for horizon in (30, 60, 90)
            },
        },
        "avoidable_probability": {
            "value": round(avoid_probability, 6),
            "low": round(avoidable["low"], 6),
            "high": round(avoidable["high"], 6),
            "local_level": local_avoid_level,
            "external_level": external_avoid_level,
            "evidence": avoidable,
        },
        "repeat_cost": {
            "peer_count": len(cost_peers),
            "peer_level": cost_level,
            "median_extra_allowed_cost": _money(raw_allowed),
            "extra_allowed_cost_q25": _money(allowed_q25),
            "extra_allowed_cost_q75": _money(allowed_q75),
            "median_extra_paid_cost": _money(raw_paid),
            "extra_paid_cost_q25": _money(paid_q25),
            "extra_paid_cost_q75": _money(paid_q75),
            "evidence_episode_ids": [row["episode_id"] for row in cost_peers[:25]],
            "evidence_claim_ids": list(
                dict.fromkeys(
                    claim_id
                    for row in cost_peers[:25]
                    for claim_id in row["repeat_claim_ids"]
                )
            ),
        },
        "formula_trace": formula_trace,
    }
    with _LOCK:
        _CACHE[cache_key] = result
    return result
