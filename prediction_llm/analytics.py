"""Deterministic, evidence-first analytics for the local 837 claims collection."""

from __future__ import annotations

import math
import os
import statistics
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from pymongo import ASCENDING, DESCENDING


EVIDENCE_FIELDS = [
    "Claim_ID",
    "Member_ID",
    "Patient_ID",
    "Service_Date_From",
    "Service_Date_To",
    "ICD10_Diagnosis_Code",
    "ICD10_Diagnosis_Description",
    "CPT_Code",
    "CPT_Description",
    "Billing_Provider_NPI",
    "Billing_Provider_Name",
    "Payer_Name",
    "Place_of_Service_Description",
    "Charge_Amount",
    "Allowed_Amount",
    "Paid_Amount",
    "Patient_Responsibility",
    "Claim_Status_Description",
    "Is_Historical_Reference_Record",
]

FINANCIAL_FIELDS = [
    "Charge_Amount",
    "Allowed_Amount",
    "Paid_Amount",
    "Patient_Responsibility",
]


def parse_claim_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(int(value)) if isinstance(value, (int, float)) else str(value).strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def iso_claim_date(value: Any) -> str | None:
    parsed = parse_claim_date(value)
    return parsed.isoformat() if parsed else None


def as_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value: Any) -> float:
    return round(as_number(value), 2)


def _payer_money(value: Any) -> float:
    """Financial rounding for the payer prediction (two decimals, half up)."""
    try:
        return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, ArithmeticError):
        return 0.0


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    denominator_value = as_number(denominator)
    if denominator_value == 0:
        return None
    return round(as_number(numerator) / denominator_value, 4)


def amount_similarity(left: Any, right: Any) -> float:
    a, b = as_number(left), as_number(right)
    scale = max(abs(a), abs(b), 1.0)
    return max(0.0, 1.0 - abs(a - b) / scale)


def similarity_score(target: dict[str, Any], candidate: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if target.get("CPT_Code") == candidate.get("CPT_Code"):
        score += 0.25
        reasons.append("same CPT code")
    if target.get("ICD10_Diagnosis_Code") == candidate.get("ICD10_Diagnosis_Code"):
        score += 0.25
        reasons.append("same ICD-10 code")
    elif target.get("ICD10_Family") and target.get("ICD10_Family") == candidate.get("ICD10_Family"):
        score += 0.08
        reasons.append("same ICD-10 family")
    if target.get("Billing_Provider_NPI") == candidate.get("Billing_Provider_NPI"):
        score += 0.15
        reasons.append("same billing provider")
    if target.get("Payer_ID") == candidate.get("Payer_ID"):
        score += 0.05
        reasons.append("same payer")
    if target.get("Place_of_Service_Code") == candidate.get("Place_of_Service_Code"):
        score += 0.05
        reasons.append("same place of service")
    if target.get("Member_ID") == candidate.get("Member_ID"):
        score += 0.05
        reasons.append("same member")

    amount_score = statistics.mean(
        amount_similarity(target.get(field), candidate.get(field))
        for field in ("Charge_Amount", "Allowed_Amount", "Paid_Amount")
    )
    score += 0.20 * amount_score
    if amount_score >= 0.85:
        reasons.append("closely comparable claim amounts")
    elif amount_score >= 0.65:
        reasons.append("moderately comparable claim amounts")

    return round(min(score, 1.0), 4), reasons


def compact_claim(claim: dict[str, Any]) -> dict[str, Any]:
    result = {field: claim.get(field) for field in EVIDENCE_FIELDS}
    result["Service_Date_From"] = iso_claim_date(result.get("Service_Date_From"))
    result["Service_Date_To"] = iso_claim_date(result.get("Service_Date_To"))
    for field in FINANCIAL_FIELDS:
        result[field] = money(result.get(field))
    return result


def top_counts(values: Iterable[Any], limit: int = 5) -> list[dict[str, Any]]:
    counts = Counter(str(value) for value in values if value not in (None, "", "N/A"))
    return [{"value": value, "count": count} for value, count in counts.most_common(limit)]


# These settings deliberately live beside the deterministic rules.  They are
# not model inputs and are never calculated by the optional Ollama narrator.
PAYER_COHORT_EPISODE_DAYS = int(os.environ.get("PAYER_COHORT_EPISODE_DAYS", "90"))
PAYER_SCENARIO1_UNIT_TOLERANCE = float(os.environ.get("PAYER_SCENARIO1_UNIT_TOLERANCE", "1"))


def _payer_text(value: Any) -> str:
    """Return a stable comparison value for Mongo strings, numbers, or dates."""
    if value in (None, ""):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _payer_same(left: Any, right: Any) -> bool:
    left_text, right_text = _payer_text(left), _payer_text(right)
    return bool(left_text) and bool(right_text) and left_text == right_text


def _payer_historical(claim: dict[str, Any]) -> bool:
    return _payer_text(claim.get("Is_Historical_Reference_Record")).upper() == "Y"


def _payer_family(claim: dict[str, Any]) -> str:
    explicit = _payer_text(claim.get("ICD10_Family"))
    if explicit:
        return explicit
    return _payer_text(claim.get("ICD10_Diagnosis_Code")).split(".")[0][:3]


def _payer_units(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _payer_units_match(left: Any, right: Any, tolerance: float) -> bool:
    left_value, right_value = _payer_units(left), _payer_units(right)
    return left_value is not None and right_value is not None and abs(left_value - right_value) <= tolerance


def _payer_claim_date(claim: dict[str, Any]) -> date | None:
    return parse_claim_date(claim.get("Service_Date_From"))


def _payer_claim_id(claim: dict[str, Any]) -> str:
    return _payer_text(claim.get("Claim_ID"))


def _payer_member_id(claim: dict[str, Any]) -> str:
    return _payer_text(claim.get("Member_ID"))


def _payer_identity(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": _payer_member_id(claim),
        "icd10_family": _payer_family(claim),
        "exact_icd": _payer_text(claim.get("ICD10_Diagnosis_Code")),
        "payer_id": _payer_text(claim.get("Payer_ID")),
        "provider_npi": _payer_text(claim.get("Billing_Provider_NPI")),
        "pos": _payer_text(claim.get("Place_of_Service_Code")),
        "cpt": _payer_text(claim.get("CPT_Code")),
        "units": _payer_units(claim.get("Units")),
    }


def _payer_episode_record(
    rows: list[dict[str, Any]],
    historical_rows: list[dict[str, Any]],
    member_id: str,
    family: str,
    start: date,
    sequence: int,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (_payer_claim_date(row) or date.min, _payer_claim_id(row)))
    dates = [value for value in (_payer_claim_date(row) for row in ordered) if value]
    end = max(dates) if dates else start
    paid = [as_number(row.get("Paid_Amount")) for row in ordered]
    episode_id = f"PAYER-{member_id}-{family}-{start.strftime('%Y%m%d')}-{sequence}"
    return {
        "peer_episode_id": episode_id,
        "episode_id": episode_id,
        "member_id": member_id,
        "diagnosis_family": family,
        "episode_start": start.isoformat(),
        "episode_end": end.isoformat(),
        "episode_duration_days": (end - start).days + 1 if dates else 0,
        "claim_count": len(ordered),
        "total_paid": _payer_money(sum(paid)),
        "median_paid_per_claim": _payer_money(statistics.median(paid)) if paid else 0.0,
        "claim_ids": [_payer_claim_id(row) for row in ordered if _payer_claim_id(row)],
        "rows": ordered,
        # Historical reference rows are retained for evidence only.  They do
        # not contribute to claim counts, Paid_Amount totals, or benchmarks.
        "historical_reference_rows": sorted(
            historical_rows,
            key=lambda row: (_payer_claim_date(row) or date.min, _payer_claim_id(row)),
        ),
    }


def _payer_rolling_episodes(
    claims: Iterable[dict[str, Any]],
    window_days: int,
    include_historical_metrics: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    historical_grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for claim in claims:
        if not _payer_member_id(claim) or not _payer_family(claim) or not _payer_claim_date(claim):
            continue
        key = (_payer_member_id(claim), _payer_family(claim))
        if _payer_historical(claim):
            historical_grouped.setdefault(key, []).append(claim)
            if not include_historical_metrics:
                continue
        grouped.setdefault(key, []).append(claim)

    episodes: list[dict[str, Any]] = []
    for (member_id, family), rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (_payer_claim_date(row) or date.min, _payer_claim_id(row)))
        index = 0
        sequence = 1
        while index < len(ordered):
            start = _payer_claim_date(ordered[index])
            assert start is not None
            end_limit = start.fromordinal(start.toordinal() + window_days)
            end_index = index
            while end_index + 1 < len(ordered):
                next_date = _payer_claim_date(ordered[end_index + 1])
                if next_date is None or next_date > end_limit:
                    break
                end_index += 1
            window_rows = ordered[index : end_index + 1]
            metric_rows = window_rows
            episode_end = _payer_claim_date(window_rows[-1]) or start
            history_rows = [
                row
                for row in historical_grouped.get((member_id, family), [])
                if start <= (_payer_claim_date(row) or date.min) <= episode_end
            ]
            if metric_rows:
                episodes.append(_payer_episode_record(metric_rows, history_rows, member_id, family, start, sequence))
            sequence += 1
            index = end_index + 1
    return episodes


def build_comparison_episode(
    claims: Iterable[dict[str, Any]],
    selected_claim: dict[str, Any],
    window_days: int = PAYER_COHORT_EPISODE_DAYS,
) -> dict[str, Any]:
    """Build the bounded target episode containing the selected claim."""
    claim_id = _payer_claim_id(selected_claim)
    episodes = _payer_rolling_episodes(claims, window_days, include_historical_metrics=False)
    for episode in episodes:
        if claim_id in episode["claim_ids"]:
            return episode
    raise LookupError(f"Claim {claim_id} could not be placed in a {window_days}-day disease episode")


def _payer_match_metrics(target_claim: dict[str, Any], episode: dict[str, Any], tolerance: float) -> dict[str, bool]:
    target = _payer_identity(target_claim)
    rows = episode.get("rows", [])
    return {
        "exact_icd_match": any(_payer_same(target["exact_icd"], row.get("ICD10_Diagnosis_Code")) for row in rows),
        "payer_match": any(_payer_same(target["payer_id"], row.get("Payer_ID")) for row in rows),
        "provider_match": any(_payer_same(target["provider_npi"], row.get("Billing_Provider_NPI")) for row in rows),
        "cpt_match": any(_payer_same(target["cpt"], row.get("CPT_Code")) for row in rows),
        "pos_match": any(_payer_same(target["pos"], row.get("Place_of_Service_Code")) for row in rows),
        "units_match": any(_payer_units_match(target["units"], row.get("Units"), tolerance) for row in rows),
    }


def _payer_episode_matches(target_claim: dict[str, Any], episode: dict[str, Any], number: int, tolerance: float) -> bool:
    target = _payer_identity(target_claim)
    if not episode.get("rows") or episode.get("member_id") == target["member_id"]:
        return False
    if episode.get("diagnosis_family") != target["icd10_family"]:
        return False
    for row in episode["rows"]:
        if number == 1 and not (
            _payer_same(target["payer_id"], row.get("Payer_ID"))
            and _payer_same(target["provider_npi"], row.get("Billing_Provider_NPI"))
            and _payer_same(target["pos"], row.get("Place_of_Service_Code"))
            and _payer_same(target["cpt"], row.get("CPT_Code"))
            and _payer_units_match(target["units"], row.get("Units"), tolerance)
        ):
            continue
        if number == 2 and not _payer_same(target["payer_id"], row.get("Payer_ID")):
            continue
        return True
    return number == 3


def build_peer_episodes(
    claims: Iterable[dict[str, Any]],
    target_claim: dict[str, Any],
    scenario_number: int | None = None,
    window_days: int = PAYER_COHORT_EPISODE_DAYS,
    unit_tolerance: float = PAYER_SCENARIO1_UNIT_TOLERANCE,
) -> list[dict[str, Any]]:
    """Build bounded external-member episodes, optionally for one scenario."""
    episodes = _payer_rolling_episodes(claims, window_days, include_historical_metrics=True)
    if scenario_number is None:
        return episodes
    return [
        episode
        for episode in episodes
        if _payer_episode_matches(target_claim, episode, scenario_number, unit_tolerance)
    ]


def select_payer_scenario(
    target_claim: dict[str, Any],
    peer_episodes: Iterable[dict[str, Any]],
    unit_tolerance: float = PAYER_SCENARIO1_UNIT_TOLERANCE,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply strict deterministic priority and return exactly one peer cohort."""
    episodes = list(peer_episodes)
    names = {
        1: "Scenario 1 — Strict Match",
        2: "Scenario 2 — Same ICD-10 Family + Same Payer",
        3: "Scenario 3 — Same ICD-10 Family Only",
    }
    rules = {
        1: "different-member peers matched ICD-10 family, payer, billing provider NPI, place of service, CPT, and units within tolerance",
        2: "different-member peers matched ICD-10 family and payer; provider, place of service, CPT, and units may differ",
        3: "different-member peers matched ICD-10 family; payer, provider, place of service, CPT, and units may differ",
    }
    scenario_selection: dict[str, Any] = {}
    cohorts: dict[int, list[dict[str, Any]]] = {}
    for number in (1, 2, 3):
        matches = [episode for episode in episodes if _payer_episode_matches(target_claim, episode, number, unit_tolerance)]
        cohorts[number] = matches
        member_count = len({episode["member_id"] for episode in matches})
        scenario_selection[f"scenario_{number}"] = {
            "available": bool(matches),
            "reason": (
                f"Available: {member_count} different-member peer member(s) supplied {len(matches)} qualifying episode(s); {rules[number]}."
                if matches
                else f"Unavailable: no different-member peer episode met the rule ({rules[number]})."
            ),
            "peer_member_count": member_count,
            "peer_episode_count": len(matches),
            "peer_claim_count": sum(item["claim_count"] for item in matches),
        }
    selected_number = next((number for number in (1, 2, 3) if cohorts[number]), 0)
    if selected_number:
        earlier = " ".join(
            scenario_selection[f"scenario_{number}"]["reason"] for number in range(1, selected_number)
        )
        selected = cohorts[selected_number]
        scenario_selection["selected"] = {
            "number": selected_number,
            "name": names[selected_number],
            "reason": (
                f"{earlier} Selected because {len({item['member_id'] for item in selected})} different-member peer member(s) "
                f"supplied {len(selected)} qualifying episode(s) under the rule: {rules[selected_number]}."
            ).strip(),
            "peer_member_count": len({item["member_id"] for item in selected}),
            "peer_episode_count": len(selected),
            "peer_claim_count": sum(item["claim_count"] for item in selected),
        }
        return scenario_selection, selected
    scenario_selection["selected"] = {"number": 0, "name": "", "reason": "No cross-member prediction: none of the three rules had a qualifying peer episode."}
    return scenario_selection, []


def _payer_benchmark_group(episodes: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], str]:
    ordered = sorted(episodes, key=lambda item: (item[key], item["total_paid"], item["member_id"], item["episode_id"]))
    count = len(ordered)
    if count >= 5:
        return ordered[: max(1, math.ceil(count * 0.40))], f"Median {key.replace('_', ' ')} of the lowest 40% of qualifying peer episodes"
    if count >= 3:
        return ordered[:2], f"Median {key.replace('_', ' ')} of the two lowest qualifying peer episodes"
    if count == 2:
        return ordered[:1], f"{key.replace('_', ' ').capitalize()} of the lower qualifying peer episode"
    if count == 1:
        return ordered, f"Single qualifying external peer episode ({key.replace('_', ' ')})"
    return [], "No qualifying external peer episodes"


def build_utilisation_benchmark(
    peer_episodes: Iterable[dict[str, Any]],
    target_claim_count: int,
) -> dict[str, Any]:
    episodes = list(peer_episodes)
    group, method = _payer_benchmark_group(episodes, "claim_count")
    claim_counts = [item["claim_count"] for item in group]
    benchmark = round(float(statistics.median(claim_counts)), 1) if claim_counts else 0.0
    peer_paid = [as_number(row.get("Paid_Amount")) for episode in episodes for row in episode.get("rows", [])]
    median_peer_paid = _payer_money(statistics.median(peer_paid)) if peer_paid else 0.0
    excess = max(target_claim_count - benchmark, 0)
    opportunity = _payer_money(excess * median_peer_paid)
    return {
        "claim_count": benchmark,
        "value": benchmark,
        "method": method,
        "peer_episode_ids": [item["episode_id"] for item in group],
        "member_ids": sorted({item["member_id"] for item in group}),
        "claim_ids": [claim_id for item in group for claim_id in item["claim_ids"]],
        "median_peer_paid_per_claim": median_peer_paid,
        "excess_claim_count": excess,
        "opportunity": opportunity,
    }


def build_lower_spend_benchmark(
    peer_episodes: Iterable[dict[str, Any]],
    target_total_paid: float,
) -> dict[str, Any]:
    episodes = list(peer_episodes)
    ordered = sorted(episodes, key=lambda item: (item["total_paid"], item["claim_count"], item["member_id"], item["episode_id"]))
    if len(ordered) >= 5:
        group = ordered[: max(1, math.ceil(len(ordered) * 0.40))]
        method = "Median payer spend of the lowest 40% of qualifying peer episodes"
    elif len(ordered) >= 3:
        group = ordered[:2]
        method = "Median payer spend of the two lowest-spend qualifying peer episodes"
    elif len(ordered) == 2:
        group = ordered[:1]
        method = "Payer spend of the lower-spend qualifying peer episode"
    elif len(ordered) == 1 and ordered[0]["total_paid"] < target_total_paid:
        group = ordered
        method = "Single lower-spend external peer episode (confidence reduced)"
    else:
        group = []
        method = "No qualifying lower-spend external peer episode"
    value = _payer_money(statistics.median(item["total_paid"] for item in group)) if group else 0.0
    return {
        "value": value,
        "method": method,
        "peer_episode_ids": [item["episode_id"] for item in group],
        "member_ids": sorted({item["member_id"] for item in group}),
        "claim_ids": [claim_id for item in group for claim_id in item["claim_ids"]],
        "available": bool(group),
    }


def _payer_percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calculate_payer_savings(
    target_episode: dict[str, Any],
    peer_episodes: Iterable[dict[str, Any]],
    selected_claim: dict[str, Any],
) -> dict[str, Any]:
    """Calculate non-duplicated payer opportunities from Paid_Amount only."""
    peers = list(peer_episodes)
    target_total = _payer_money(target_episode.get("total_paid"))
    utilisation = build_utilisation_benchmark(peers, int(target_episode.get("claim_count", 0)))
    lower_spend = build_lower_spend_benchmark(peers, target_total)
    payer_spend_opportunity = _payer_money(max(target_total - lower_spend["value"], 0)) if lower_spend["available"] else 0.0
    predicted = _payer_money(min(target_total, max(utilisation["opportunity"], payer_spend_opportunity)))
    peer_totals = [_payer_money(item["total_paid"]) for item in peers]
    q25 = _payer_money(_payer_percentile(peer_totals, 0.25)) if peer_totals else 0.0
    median_peer = _payer_money(_payer_percentile(peer_totals, 0.50)) if peer_totals else 0.0
    q75 = _payer_money(_payer_percentile(peer_totals, 0.75)) if peer_totals else 0.0
    range_low = _payer_money(max(target_total - q75, 0))
    range_mid = _payer_money(max(target_total - median_peer, 0))
    range_high = _payer_money(max(target_total - q25, 0))
    # A component can be stronger than the distribution's midpoint.  Keep the
    # displayed interval honest about the selected rule result.
    range_low = min(range_low, predicted)
    range_high = max(range_high, predicted)
    selected_paid = _payer_money(selected_claim.get("Paid_Amount"))
    share = selected_paid / target_total if target_total else 0.0
    attributed = predicted if target_episode.get("claim_count") == 1 else _payer_money(predicted * share)
    return {
        "utilisation_reduction_opportunity": utilisation["opportunity"],
        "payer_spend_reduction_opportunity": payer_spend_opportunity,
        "episode_predicted_payer_avoidable_spend": predicted,
        "claim_attributed_payer_avoidable_spend": _payer_money(attributed),
        "selected_claim": {
            "claim_id": _payer_claim_id(selected_claim),
            "paid_amount": selected_paid,
            "episode_spend_share": round(share, 4),
            "attributed_payer_avoidable_spend": _payer_money(attributed),
        },
        "range": {
            "low": range_low,
            "mid": range_mid,
            "high": range_high,
            "label": "Benchmark-Based Estimate Range" if len({item["member_id"] for item in peers}) <= 1 or len(peers) <= 2 else "Peer Episode Spend Range",
        },
        "utilisation_benchmark": utilisation,
        "lower_spend_benchmark": lower_spend,
        "peer_spend_distribution": {"q25": q25, "median": median_peer, "q75": q75},
    }


def calculate_rule_confidence(
    scenario_number: int,
    target_claim: dict[str, Any],
    peer_episodes: Iterable[dict[str, Any]],
    target_episode_duration_days: float | None = None,
) -> dict[str, Any]:
    peers = list(peer_episodes)
    if not peers:
        return {"score": 0, "level": "Low", "drivers": [], "penalties": ["No external peer evidence"]}
    base = {1: 78, 2: 65, 3: 52}.get(scenario_number, 0)
    members = len({item["member_id"] for item in peers})
    score = base
    drivers = [f"Scenario {scenario_number} rule strength", f"{members} different-member peer member(s)", f"{len(peers)} qualifying peer episode(s)"]
    penalties: list[str] = []
    if len(peers) >= 5:
        score += 8
        drivers.append("At least five qualifying peer episodes")
    elif len(peers) >= 3:
        score += 4
    else:
        score -= 8
        penalties.append("Few qualifying peer episodes")
    if members == 1:
        score -= 20
        penalties.append("Only one external peer member was available")
    if len(peers) == 1:
        score = min(score, 49)
        penalties.append("Only one qualifying peer episode was available; confidence is Low")
    metrics = [_payer_match_metrics(target_claim, peer, PAYER_SCENARIO1_UNIT_TOLERANCE) for peer in peers]
    labels = {
        "exact_icd_match": "exact ICD-10 diagnosis",
        "payer_match": "payer",
        "provider_match": "billing provider NPI",
        "cpt_match": "CPT",
        "pos_match": "place of service",
        "units_match": "units",
    }
    for key, label in labels.items():
        rate = sum(bool(metric[key]) for metric in metrics) / len(metrics)
        if rate == 1:
            score += 2
            drivers.append(f"All peer episodes matched {label}")
        elif rate == 0:
            score -= 3
            penalties.append(f"Peer episodes differed on {label}")
        else:
            penalties.append(f"Only {round(rate * 100)}% of peer episodes matched {label}")
    peer_durations = [item.get("episode_duration_days", 0) for item in peers]
    if peer_durations:
        target_duration = float(target_episode_duration_days if target_episode_duration_days is not None else 1)
        if abs(float(statistics.median(peer_durations)) - target_duration) <= 30:
            drivers.append("Peer episode durations are within the configured comparison window")
        else:
            penalties.append("Peer episode durations vary from the target")
    totals = [as_number(item.get("total_paid")) for item in peers]
    mean = statistics.mean(totals) if totals else 0
    dispersion = (statistics.pstdev(totals) / mean) if mean else 0
    if dispersion > 1:
        score -= 10
        penalties.append("High payer-spend dispersion across peer episodes")
    elif dispersion > 0.5:
        score -= 5
        penalties.append("Moderate payer-spend dispersion across peer episodes")
    else:
        drivers.append("Payer-spend dispersion is limited")
    score = max(0, min(100, int(round(score))))
    return {"score": score, "level": "High" if score >= 80 else "Medium" if score >= 55 else "Low", "drivers": drivers, "penalties": penalties}


def _payer_evidence_row(claim: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "Claim_ID": _payer_claim_id(claim),
        "Member_ID": claim.get("Member_ID"),
        "Service_Date_From": iso_claim_date(claim.get("Service_Date_From")),
        "ICD10_Diagnosis_Code": claim.get("ICD10_Diagnosis_Code"),
        "ICD10_Family": _payer_family(claim),
        "CPT_Code": claim.get("CPT_Code"),
        "Payer_Name": claim.get("Payer_Name"),
        "Payer_ID": claim.get("Payer_ID"),
        "Billing_Provider_Name": claim.get("Billing_Provider_Name"),
        "Billing_Provider_NPI": claim.get("Billing_Provider_NPI"),
        "Place_of_Service_Code": claim.get("Place_of_Service_Code"),
        "Paid_Amount": _payer_money(claim.get("Paid_Amount")),
        "Evidence_Role": role,
    }


class ClaimsAnalytics:
    def __init__(self, database, collection_name: str = "837_claims") -> None:
        self.database = database
        self.claims = database[collection_name]
        self._payer_prediction_rows: list[dict[str, Any]] | None = None
        self._payer_prediction_episodes: list[dict[str, Any]] | None = None
        self._payer_prediction_peer_episodes: list[dict[str, Any]] | None = None

    def list_members(self) -> list[dict[str, Any]]:
        pipeline = [
            {
                "$group": {
                    "_id": "$Member_ID",
                    "claim_count": {"$sum": 1},
                    "latest_service_date": {"$max": "$Service_Date_From"},
                }
            },
            {"$sort": {"_id": ASCENDING}},
        ]
        return [
            {
                "Member_ID": row["_id"],
                "claim_count": row["claim_count"],
                "latest_service_date": iso_claim_date(row["latest_service_date"]),
            }
            for row in self.claims.aggregate(pipeline)
            if row.get("_id")
        ]

    def list_member_claims(self, member_id: str) -> list[dict[str, Any]]:
        projection = {
            "_id": 0,
            "Claim_ID": 1,
            "Service_Date_From": 1,
            "CPT_Code": 1,
            "CPT_Description": 1,
            "ICD10_Diagnosis_Code": 1,
            "Paid_Amount": 1,
            "Is_Historical_Reference_Record": 1,
        }
        rows = self.claims.find({"Member_ID": member_id}, projection).sort("Service_Date_From", DESCENDING)
        return [
            {
                **row,
                "Service_Date_From": iso_claim_date(row.get("Service_Date_From")),
                "Paid_Amount": money(row.get("Paid_Amount")),
            }
            for row in rows
        ]

    def member_summary(self, member_id: str) -> dict[str, Any]:
        claims = list(self.claims.find({"Member_ID": member_id}, {"_id": 0}).sort("Service_Date_From", ASCENDING))
        if not claims:
            raise LookupError(f"No claims found for member {member_id}")

        dates = [parse_claim_date(claim.get("Service_Date_From")) for claim in claims]
        valid_dates = [value for value in dates if value]
        financial_totals = {
            field: round(sum(as_number(claim.get(field)) for claim in claims), 2)
            for field in FINANCIAL_FIELDS
        }
        ratios = {
            "allowed_to_charge": safe_ratio(financial_totals["Allowed_Amount"], financial_totals["Charge_Amount"]),
            "paid_to_allowed": safe_ratio(financial_totals["Paid_Amount"], financial_totals["Allowed_Amount"]),
            "patient_to_allowed": safe_ratio(
                financial_totals["Patient_Responsibility"], financial_totals["Allowed_Amount"]
            ),
        }

        cpt_groups: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            cpt = str(claim.get("CPT_Code") or "")
            if cpt:
                cpt_groups.setdefault(cpt, []).append(claim)
        repeated = []
        for cpt, group in cpt_groups.items():
            if len(group) > 1:
                repeated.append(
                    {
                        "CPT_Code": cpt,
                        "CPT_Description": group[0].get("CPT_Description"),
                        "count": len(group),
                        "first_date": iso_claim_date(group[0].get("Service_Date_From")),
                        "last_date": iso_claim_date(group[-1].get("Service_Date_From")),
                        "claim_ids": [item.get("Claim_ID") for item in group[-5:]],
                    }
                )
        repeated.sort(key=lambda item: (-item["count"], item["CPT_Code"]))

        short_intervals = []
        dated_claims = [(parse_claim_date(claim.get("Service_Date_From")), claim) for claim in claims]
        dated_claims = [(when, claim) for when, claim in dated_claims if when]
        for (first_date, first), (second_date, second) in zip(dated_claims, dated_claims[1:]):
            days = (second_date - first_date).days
            if 0 <= days <= 3:
                short_intervals.append(
                    {
                        "first_claim_id": first.get("Claim_ID"),
                        "first_date": first_date.isoformat(),
                        "second_claim_id": second.get("Claim_ID"),
                        "second_date": second_date.isoformat(),
                        "days_apart": days,
                        "same_CPT": first.get("CPT_Code") == second.get("CPT_Code"),
                        "same_ICD10": first.get("ICD10_Diagnosis_Code") == second.get("ICD10_Diagnosis_Code"),
                        "same_provider": first.get("Billing_Provider_NPI") == second.get("Billing_Provider_NPI"),
                    }
                )

        patterns = []
        if repeated:
            patterns.append(
                {
                    "type": "repeated_procedures",
                    "statement": f"{len(repeated)} CPT code(s) occurred more than once in this member's available claims.",
                    "evidence_claim_ids": sorted({claim_id for row in repeated for claim_id in row["claim_ids"] if claim_id})[:20],
                }
            )
        if short_intervals:
            patterns.append(
                {
                    "type": "short_timeframes",
                    "statement": f"{len(short_intervals)} adjacent claim pair(s) occurred zero to three days apart.",
                    "evidence_claim_ids": sorted(
                        {value for row in short_intervals for value in (row["first_claim_id"], row["second_claim_id"]) if value}
                    )[:20],
                }
            )

        insufficient = [
            "Claims data does not establish the clinical reason for a diagnosis, procedure, repeated service, or timing pattern."
        ]
        if len(claims) < 2:
            insufficient.append("Fewer than two claims are available, so repeated or longitudinal patterns cannot be assessed.")

        return {
            "analysis_type": "member_history",
            "member_id": member_id,
            "facts": {
                "claim_count": len(claims),
                "historical_reference_count": sum(
                    1 for claim in claims if claim.get("Is_Historical_Reference_Record") == "Y"
                ),
                "date_range": {
                    "from": min(valid_dates).isoformat() if valid_dates else None,
                    "to": max(valid_dates).isoformat() if valid_dates else None,
                },
                "financial_totals": financial_totals,
                "financial_ratios": ratios,
                "top_diagnosis_codes": top_counts(claim.get("ICD10_Diagnosis_Code") for claim in claims),
                "top_procedure_codes": top_counts(claim.get("CPT_Code") for claim in claims),
                "top_billing_providers": top_counts(claim.get("Billing_Provider_Name") for claim in claims),
            },
            "patterns": patterns,
            "repeated_procedures": repeated[:20],
            "short_timeframe_events": short_intervals[:30],
            "insufficient_evidence": insufficient,
            "evidence_records": [compact_claim(claim) for claim in claims[-12:]][::-1],
            "guardrail": "Data-driven claims analytics only; no clinical advice, diagnosis, treatment recommendation, or inferred clinical rationale.",
        }

    def claim_analysis(self, claim_id: str) -> dict[str, Any]:
        target = self.claims.find_one({"Claim_ID": claim_id}, {"_id": 0})
        if not target:
            raise LookupError(f"Claim {claim_id} was not found")

        target_date_value = target.get("Service_Date_From")
        target_date = parse_claim_date(target_date_value)
        historical_filter: dict[str, Any] = {"Claim_ID": {"$ne": claim_id}}
        if target_date_value:
            historical_filter["Service_Date_From"] = {"$lt": target_date_value}

        candidates = list(self.claims.find(historical_filter, {"_id": 0}))
        scored = []
        for candidate in candidates:
            score, reasons = similarity_score(target, candidate)
            if score >= 0.35:
                scored.append((score, candidate, reasons))
        scored.sort(
            key=lambda item: (item[0], item[1].get("Service_Date_From") or 0),
            reverse=True,
        )
        similar = [
            {
                "similarity_score": score,
                "match_reasons": reasons,
                "record": compact_claim(candidate),
            }
            for score, candidate, reasons in scored[:8]
        ]

        same_member_history = list(
            self.claims.find(
                {
                    "Member_ID": target.get("Member_ID"),
                    "Claim_ID": {"$ne": claim_id},
                    **({"Service_Date_From": {"$lt": target_date_value}} if target_date_value else {}),
                },
                {"_id": 0},
            ).sort("Service_Date_From", DESCENDING)
        )
        repeated_cpt = [claim for claim in same_member_history if claim.get("CPT_Code") == target.get("CPT_Code")]

        nearby = []
        if target_date:
            for claim in same_member_history:
                claim_date = parse_claim_date(claim.get("Service_Date_From"))
                if claim_date:
                    days = (target_date - claim_date).days
                    if 0 <= days <= 3:
                        nearby.append(
                            {
                                "days_apart": days,
                                "same_CPT": claim.get("CPT_Code") == target.get("CPT_Code"),
                                "same_ICD10": claim.get("ICD10_Diagnosis_Code") == target.get("ICD10_Diagnosis_Code"),
                                "same_provider": claim.get("Billing_Provider_NPI") == target.get("Billing_Provider_NPI"),
                                "record": compact_claim(claim),
                            }
                        )

        cohort_filter = {
            "CPT_Code": target.get("CPT_Code"),
            "ICD10_Diagnosis_Code": target.get("ICD10_Diagnosis_Code"),
            "Claim_ID": {"$ne": claim_id},
        }
        if target_date_value:
            cohort_filter["Service_Date_From"] = {"$lt": target_date_value}
        cohort = list(self.claims.find(cohort_filter, {"_id": 0, **{field: 1 for field in FINANCIAL_FIELDS}}))
        comparisons = {}
        unusual = []
        for field in FINANCIAL_FIELDS:
            values = [as_number(row.get(field)) for row in cohort if row.get(field) not in (None, "")]
            if values:
                median = statistics.median(values)
                target_value = as_number(target.get(field))
                delta_pct = None if median == 0 else round((target_value - median) / median, 4)
                percentile = round(sum(value <= target_value for value in values) / len(values), 4)
                comparisons[field] = {
                    "target": round(target_value, 2),
                    "historical_median": round(median, 2),
                    "difference_from_median": round(target_value - median, 2),
                    "difference_percent": delta_pct,
                    "percentile_rank": percentile,
                    "cohort_size": len(values),
                }
                if len(values) >= 5 and delta_pct is not None and abs(delta_pct) >= 0.20:
                    direction = "above" if delta_pct > 0 else "below"
                    unusual.append(
                        {
                            "field": field,
                            "statement": f"{field} is {abs(delta_pct):.1%} {direction} the historical median for the same CPT and ICD-10 combination.",
                            "cohort_size": len(values),
                        }
                    )

        financial = {
            "amounts": {field: money(target.get(field)) for field in FINANCIAL_FIELDS},
            "ratios": {
                "allowed_to_charge": safe_ratio(target.get("Allowed_Amount"), target.get("Charge_Amount")),
                "paid_to_allowed": safe_ratio(target.get("Paid_Amount"), target.get("Allowed_Amount")),
                "patient_to_allowed": safe_ratio(
                    target.get("Patient_Responsibility"), target.get("Allowed_Amount")
                ),
            },
            "same_code_historical_comparison": comparisons,
        }

        patterns = []
        if similar:
            patterns.append(
                {
                    "type": "similar_historical_claims",
                    "statement": f"{len(similar)} highest-scoring historical claim match(es) are shown using codes, provider, payer, place of service, member, and amount similarity.",
                    "evidence_claim_ids": [item["record"]["Claim_ID"] for item in similar],
                }
            )
        if repeated_cpt:
            patterns.append(
                {
                    "type": "repeated_procedure",
                    "statement": f"The same member had {len(repeated_cpt)} earlier claim(s) with CPT {target.get('CPT_Code')}.",
                    "evidence_claim_ids": [claim.get("Claim_ID") for claim in repeated_cpt[:10]],
                }
            )
        if nearby:
            patterns.append(
                {
                    "type": "short_timeframe",
                    "statement": f"{len(nearby)} earlier member claim(s) occurred within three days of this claim.",
                    "evidence_claim_ids": [item["record"]["Claim_ID"] for item in nearby],
                }
            )
        patterns.extend({"type": "financial_outlier", **item, "evidence_claim_ids": [claim_id]} for item in unusual)

        insufficient = [
            "Claims data does not establish why a procedure, service, treatment, or repeated activity occurred.",
            "The analysis does not determine clinical appropriateness, effectiveness, diagnosis, or treatment outcome.",
        ]
        if not similar:
            insufficient.append("No historical claim reached the configured similarity threshold of 0.35.")
        if len(cohort) < 5:
            insufficient.append(
                f"Only {len(cohort)} earlier claim(s) share the same CPT and ICD-10 combination; this is insufficient for a strong financial outlier conclusion."
            )

        return {
            "analysis_type": "claim_analysis",
            "claim_id": claim_id,
            "member_id": target.get("Member_ID"),
            "facts": {
                "target_claim": compact_claim(target),
                "same_member_prior_claim_count": len(same_member_history),
                "same_member_prior_same_CPT_count": len(repeated_cpt),
                "same_code_historical_cohort_size": len(cohort),
            },
            "financial_analysis": financial,
            "patterns": patterns,
            "similar_historical_claims": similar,
            "short_timeframe_events": nearby,
            "insufficient_evidence": insufficient,
            "evidence_records": [compact_claim(target)]
            + [item["record"] for item in similar]
            + [item["record"] for item in nearby],
            "guardrail": "Data-driven claims analytics only; no clinical advice, diagnosis, treatment recommendation, or inferred clinical rationale.",
        }

    def payer_savings_prediction(self, claim_id: str) -> dict[str, Any]:
        """Return one rule-based payer-spend prediction for a selected claim.

        The method intentionally reads the source claim rows and performs all
        peer selection, episode construction, benchmarking, and arithmetic in
        Python.  The optional narrator receives this completed result only.
        """
        if self._payer_prediction_rows is None:
            try:
                self._payer_prediction_rows = list(self.claims.find({}, {"_id": 0}))
            except TypeError:
                self._payer_prediction_rows = list(self.claims.find({}))
        source_rows = self._payer_prediction_rows
        matches = [row for row in source_rows if _payer_claim_id(row) == _payer_text(claim_id)]
        target = next((row for row in matches if not _payer_historical(row)), None) or (matches[0] if matches else None)
        if not target:
            raise LookupError(f"Claim {claim_id} was not found")
        if _payer_historical(target):
            raise LookupError(f"Claim {claim_id} is a historical reference record, not a selectable claim")
        if not _payer_member_id(target) or not _payer_family(target) or not _payer_claim_date(target):
            raise ValueError("The selected claim is missing Member_ID, ICD10_Family, or Service_Date_From")

        # Build the bounded cohort once for this request.  This keeps the
        # endpoint deterministic while avoiding repeated scans of the Mongo
        # collection when a member has many claims.
        if self._payer_prediction_episodes is None:
            self._payer_prediction_episodes = _payer_rolling_episodes(
                source_rows,
                PAYER_COHORT_EPISODE_DAYS,
                include_historical_metrics=False,
            )
        if self._payer_prediction_peer_episodes is None:
            self._payer_prediction_peer_episodes = _payer_rolling_episodes(
                source_rows,
                PAYER_COHORT_EPISODE_DAYS,
                include_historical_metrics=True,
            )
        all_episodes = self._payer_prediction_episodes
        target_episode = next(
            (episode for episode in all_episodes if _payer_claim_id(target) in episode["claim_ids"]),
            None,
        )
        if not target_episode:
            raise LookupError(f"Claim {claim_id} could not be placed in a {PAYER_COHORT_EPISODE_DAYS}-day disease episode")
        all_peer_episodes = self._payer_prediction_peer_episodes
        scenario_selection, selected_peers = select_payer_scenario(target, all_peer_episodes)
        selected_number = scenario_selection["selected"]["number"]
        if selected_peers:
            prediction = calculate_payer_savings(target_episode, selected_peers, target)
            confidence = calculate_rule_confidence(
                selected_number,
                target,
                selected_peers,
                target_episode_duration_days=target_episode["episode_duration_days"],
            )
        else:
            prediction = {
                "utilisation_reduction_opportunity": 0.0,
                "payer_spend_reduction_opportunity": 0.0,
                "episode_predicted_payer_avoidable_spend": 0.0,
                "claim_attributed_payer_avoidable_spend": 0.0,
                "selected_claim": {
                    "claim_id": _payer_claim_id(target),
                    "paid_amount": _payer_money(target.get("Paid_Amount")),
                    "episode_spend_share": 0.0,
                    "attributed_payer_avoidable_spend": 0.0,
                },
                "range": {"low": 0.0, "mid": 0.0, "high": 0.0, "label": "No Cross-Member Benchmark"},
                "utilisation_benchmark": build_utilisation_benchmark([], target_episode["claim_count"]),
                "lower_spend_benchmark": build_lower_spend_benchmark([], target_episode["total_paid"]),
                "peer_spend_distribution": {"q25": 0.0, "median": 0.0, "q75": 0.0},
            }
            confidence = {"score": 0, "level": "Low", "drivers": [], "penalties": ["No external peer evidence"]}

        lower_utilisation_ids = set(prediction["utilisation_benchmark"]["peer_episode_ids"])
        lower_spend_ids = set(prediction["lower_spend_benchmark"]["peer_episode_ids"])
        peer_members_used: list[dict[str, Any]] = []
        for member_id in sorted({peer["member_id"] for peer in selected_peers}):
            member_episodes = [peer for peer in selected_peers if peer["member_id"] == member_id]
            peer_members_used.append(
                {
                    "member_id": member_id,
                    "peer_episode_ids": [peer["episode_id"] for peer in member_episodes],
                    "claim_ids": [claim_id for peer in member_episodes for claim_id in peer["claim_ids"]],
                    "episodes": [
                        {
                            "peer_episode_id": peer["episode_id"],
                            "claim_count": peer["claim_count"],
                            "total_paid": peer["total_paid"],
                            "median_paid_per_claim": peer["median_paid_per_claim"],
                            "episode_duration_days": peer["episode_duration_days"],
                            "claim_ids": peer["claim_ids"],
                            **_payer_match_metrics(target, peer, PAYER_SCENARIO1_UNIT_TOLERANCE),
                        }
                        for peer in member_episodes
                    ],
                }
            )

        supporting_evidence: list[dict[str, Any]] = []
        supporting_evidence.extend(_payer_evidence_row(row, "Target Episode") for row in target_episode["rows"])
        supporting_evidence.extend(
            _payer_evidence_row(row, "Historical Reference")
            for row in target_episode.get("historical_reference_rows", [])
        )
        for peer in selected_peers:
            roles: list[str] = []
            if peer["episode_id"] in lower_utilisation_ids:
                roles.append("Lower-Utilisation Benchmark Evidence")
            if peer["episode_id"] in lower_spend_ids:
                roles.append("Lower-Spend Benchmark Evidence")
            if not roles:
                roles.append("Matched Peer")
            for role in roles:
                supporting_evidence.extend(_payer_evidence_row(row, role) for row in peer["rows"])
            supporting_evidence.extend(
                _payer_evidence_row(row, "Historical Reference")
                for row in peer.get("historical_reference_rows", [])
            )

        target_claim_ids = target_episode["claim_ids"]
        trace = {
            "target_claim_id": _payer_claim_id(target),
            "target_member_id": _payer_member_id(target),
            "icd10_family": _payer_family(target),
            "comparison_episode_id": target_episode["episode_id"],
            "scenario_used": selected_number,
            "scenario_name": scenario_selection["selected"]["name"],
            "target_claim_count": target_episode["claim_count"],
            "target_total_paid": target_episode["total_paid"],
            "target_claim_ids": target_claim_ids,
            "peer_member_count": len(peer_members_used),
            "peer_episode_count": len(selected_peers),
            "peer_claim_count": sum(peer["claim_count"] for peer in selected_peers),
            "utilisation_benchmark_claim_count": prediction["utilisation_benchmark"]["claim_count"],
            "excess_claim_count": prediction["utilisation_benchmark"]["excess_claim_count"],
            "median_peer_paid_per_claim": prediction["utilisation_benchmark"]["median_peer_paid_per_claim"],
            "utilisation_reduction_opportunity": prediction["utilisation_reduction_opportunity"],
            "lower_spend_benchmark": prediction["lower_spend_benchmark"]["value"],
            "lower_spend_benchmark_method": prediction["lower_spend_benchmark"]["method"],
            "payer_spend_reduction_opportunity": prediction["payer_spend_reduction_opportunity"],
            "predicted_payer_avoidable_spend": prediction["episode_predicted_payer_avoidable_spend"],
            "claim_attributed_payer_avoidable_spend": prediction["claim_attributed_payer_avoidable_spend"],
            "range": {
                "low": prediction["range"]["low"],
                "high": prediction["range"]["high"],
            },
            "confidence": confidence,
        }
        return {
            "analysis_type": "payer_savings_prediction",
            "claim_id": _payer_claim_id(target),
            "member_id": _payer_member_id(target),
            "target": {
                "claim_id": _payer_claim_id(target),
                "member_id": _payer_member_id(target),
                "diagnosis_family": _payer_family(target),
                "comparison_episode_id": target_episode["episode_id"],
                "episode_start": target_episode["episode_start"],
                "episode_end": target_episode["episode_end"],
                "episode_duration_days": target_episode["episode_duration_days"],
                "claim_count": target_episode["claim_count"],
                "total_paid": target_episode["total_paid"],
                "median_paid_per_claim": target_episode["median_paid_per_claim"],
                "claim_ids": target_episode["claim_ids"],
            },
            "scenario_selection": scenario_selection,
            "peer_summary": {
                "member_count": len(peer_members_used),
                "episode_count": len(selected_peers),
                "claim_count": sum(peer["claim_count"] for peer in selected_peers),
            },
            "utilisation_benchmark": prediction["utilisation_benchmark"],
            "lower_spend_benchmark": prediction["lower_spend_benchmark"],
            "prediction": {
                "utilisation_reduction_opportunity": prediction["utilisation_reduction_opportunity"],
                "payer_spend_reduction_opportunity": prediction["payer_spend_reduction_opportunity"],
                "episode_predicted_payer_avoidable_spend": prediction["episode_predicted_payer_avoidable_spend"],
                "claim_attributed_payer_avoidable_spend": prediction["claim_attributed_payer_avoidable_spend"],
                "selected_claim": prediction["selected_claim"],
                "range": prediction["range"],
            },
            "confidence": confidence,
            "peer_members_used": peer_members_used,
            "supporting_evidence": supporting_evidence,
            "evidence_records": supporting_evidence,
            "calculation_trace": trace,
            "guardrail": "Payer-spend and claims-utilisation benchmarking only; no clinical interpretation or medical-necessity conclusion.",
        }
