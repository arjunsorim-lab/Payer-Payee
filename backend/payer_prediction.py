"""Compact, deterministic payer benchmark predictions for the generation modal."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import math
import os
from statistics import median

import numpy as np


PAYER_COHORT_EPISODE_DAYS = int(os.getenv("PAYER_COHORT_EPISODE_DAYS", "90"))
PAYER_SCENARIO1_UNIT_TOLERANCE = float(os.getenv("PAYER_SCENARIO1_UNIT_TOLERANCE", "1"))
_COHORT_EPISODE_CACHE = {}
_MEMBER_PAYER_SUMMARY_CACHE = {}
_PORTFOLIO_PAYER_SUMMARY_CACHE = {}


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
    text = _text(value)
    if not text:
        return None
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _family(claim):
    explicit = _text(_field(claim, "ICD10_Family"))
    diagnosis = _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode"))
    return explicit or diagnosis.split(".")[0][:3]


def _claim_id(claim):
    return _text(_field(claim, "Claim_ID", "claimId"))


def _member_id(claim):
    return _text(_field(claim, "Member_ID", "memberId"))


def _episode_id(claim):
    return _text(_field(claim, "Episode_ID", "episodeId")) or _claim_id(claim)


def _episode(rows):
    ordered = sorted(rows, key=lambda row: (_day(_field(row, "Service_Date_From", "dos")) or date.min, _claim_id(row)))
    dates = [_day(_field(row, "Service_Date_From", "dos")) for row in ordered]
    dates = [value for value in dates if value]
    start = min(dates) if dates else None
    end = max(dates) if dates else None
    paid_values = [_number(_field(row, "Paid_Amount", "paid")) for row in ordered]
    charge_values = [_number(_field(row, "Charge_Amount", "totalCharge")) for row in ordered]
    allowed_values = [_number(_field(row, "Allowed_Amount", "allowed")) for row in ordered]
    patient_values = [_number(_field(row, "Patient_Responsibility", "patientResp")) for row in ordered]
    adjustment_values = [_number(_field(row, "Adjustment_Amount", "adjustment")) for row in ordered]
    return {
        "episode_id": _episode_id(ordered[0]),
        "member_id": _member_id(ordered[0]),
        "diagnosis_family": _family(ordered[0]),
        "diagnosis_description": _text(_field(ordered[0], "ICD10_Diagnosis_Description", "diagnosisDescription")),
        "payer": _text(_field(ordered[0], "Payer_Name", "payer")),
        "payer_id": _text(_field(ordered[0], "Payer_ID", "payerId")),
        "provider": _text(_field(ordered[0], "Billing_Provider_NPI", "billingProviderNpi")),
        "cpt": _text(_field(ordered[0], "CPT_Code", "cptCode")),
        "procedure_family": _procedure_family(ordered[0]),
        "pos": _text(_field(ordered[0], "Place_of_Service_Code", "placeOfServiceCode")),
        "units": _number(_field(ordered[0], "Units", "units")),
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "duration_days": (end - start).days + 1 if start and end else 0,
        "claim_count": len(ordered),
        "total_charge": round(sum(charge_values), 2),
        "total_allowed": round(sum(allowed_values), 2),
        "total_paid": round(sum(paid_values), 2),
        "total_patient_responsibility": round(sum(patient_values), 2),
        "total_adjustment": round(sum(adjustment_values), 2),
        "median_paid": round(float(median(paid_values)), 2) if paid_values else 0.0,
        "unique_cpt_count": len({_text(_field(row, "CPT_Code", "cptCode")) for row in ordered if _text(_field(row, "CPT_Code", "cptCode"))}),
        "unique_provider_count": len({_text(_field(row, "Billing_Provider_NPI", "billingProviderNpi")) for row in ordered if _text(_field(row, "Billing_Provider_NPI", "billingProviderNpi"))}),
        "rows": ordered,
    }


def _episodes(claims):
    grouped = defaultdict(list)
    for claim in claims:
        grouped[(_member_id(claim), _family(claim), _episode_id(claim))].append(claim)
    return [_episode(rows) for rows in grouped.values() if rows]


def _procedure_family(claim):
    code = "".join(character for character in _text(_field(claim, "CPT_Code", "cptCode")) if character.isalnum())
    return code[:3]


def _rolling_episodes(claims, window_days=PAYER_COHORT_EPISODE_DAYS):
    """Partition each member/disease history into bounded disease windows."""
    grouped = defaultdict(list)
    for claim in claims:
        member_id = _member_id(claim)
        family = _family(claim)
        service_date = _day(_field(claim, "Service_Date_From", "dos"))
        if member_id and family and service_date:
            grouped[(member_id, family)].append(claim)

    episodes = []
    for (member_id, family), rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)))
        current = []
        previous_date = None
        for row in ordered:
            row_date = _day(_field(row, "Service_Date_From", "dos"))
            if current and (row_date - previous_date).days > window_days:
                episodes.append(_cohort_episode(current, member_id, family, window_days))
                current = []
            current.append(row)
            previous_date = row_date
        if current:
            episodes.append(_cohort_episode(current, member_id, family, window_days))
    return episodes


def _database_episodes(database, source):
    key = (database.workbook_hash, source, PAYER_COHORT_EPISODE_DAYS)
    cached = _COHORT_EPISODE_CACHE.get(key)
    if cached is not None:
        return cached
    claims = database.selectable_claims if source == "target" else database.claims
    episodes = _rolling_episodes(claims)
    _COHORT_EPISODE_CACHE[key] = episodes
    return episodes


def _cohort_episode(rows, member_id, family, window_days):
    episode = _episode(rows)
    episode["episode_id"] = f"PAYER-{member_id}-{family}-{episode['start_date'].replace('-', '')}-{window_days}D"
    episode["anchor_identities"] = [
        {
            "exact_icd": _text(_field(row, "ICD10_Diagnosis_Code", "diagnosisCode")),
            "payer_id": _text(_field(row, "Payer_ID", "payerId")),
            "provider": _text(_field(row, "Billing_Provider_NPI", "billingProviderNpi")),
            "cpt": _text(_field(row, "CPT_Code", "cptCode")),
            "procedure_family": _procedure_family(row),
            "pos": _text(_field(row, "Place_of_Service_Code", "placeOfServiceCode")),
            "units": _number(_field(row, "Units", "units")),
        }
        for row in rows
    ]
    return episode


def build_payer_prediction_options(database):
    selectable_keys = {
        (_member_id(claim), _family(claim), _episode_id(claim))
        for claim in database.selectable_claims
        if _member_id(claim) and _family(claim)
    }
    episodes = [
        episode for episode in _episodes(database.claims)
        if (episode["member_id"], episode["diagnosis_family"], episode["episode_id"]) in selectable_keys
        and episode["duration_days"] <= 90
    ]
    members = defaultdict(lambda: {"diseases": defaultdict(list)})
    for episode in episodes:
        member = members[episode["member_id"]]
        member["member_id"] = episode["member_id"]
        member["diseases"][episode["diagnosis_family"]].append({
            "episode_id": episode["episode_id"],
            "start_date": episode["start_date"],
            "end_date": episode["end_date"],
            "claim_count": episode["claim_count"],
        })
    descriptions = {}
    for episode in episodes:
        descriptions.setdefault(episode["diagnosis_family"], episode["diagnosis_description"])
    items = []
    for member_id in sorted(members):
        diseases = []
        for family, family_episodes in sorted(members[member_id]["diseases"].items()):
            diseases.append({
                "family": family,
                "description": descriptions.get(family, ""),
                "episodes": sorted(family_episodes, key=lambda item: item["start_date"], reverse=True),
            })
        items.append({"member_id": member_id, "diseases": diseases})
    return {"members": items, "source": database.source_banner()}


def _scenario_match(target, peer, number):
    if peer["member_id"] == target["member_id"] or peer["diagnosis_family"] != target["diagnosis_family"]:
        return False
    if number == 1:
        return all(peer[key] == target[key] and target[key] for key in ("payer", "provider", "cpt", "pos"))
    if number == 2:
        return bool(target["payer"] and peer["payer"] == target["payer"])
    return True


def _similarity(target, peer, scenario_number):
    fields = ("diagnosis_family", "payer", "provider", "cpt", "pos")
    matches = sum(bool(target[key]) and target[key] == peer[key] for key in fields)
    floor = {1: 0.9, 2: 0.7, 3: 0.5}[scenario_number]
    return round(min(0.99, max(floor, matches / len(fields))), 2)


def _evidence_row(claim, role):
    service_date = _day(_field(claim, "Service_Date_From", "dos"))
    return {
        "claim_id": _claim_id(claim),
        "member_id": _member_id(claim),
        "service_date": service_date.isoformat() if service_date else "",
        "icd10": _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "cpt": _text(_field(claim, "CPT_Code", "cptCode")),
        "payer": _text(_field(claim, "Payer_Name", "payer")),
        "provider": _text(_field(claim, "Billing_Provider_Name", "billingProvider")),
        "pos": _text(_field(claim, "Place_of_Service_Code", "placeOfServiceCode")),
        "paid_amount": round(_number(_field(claim, "Paid_Amount", "paid")), 2),
        "evidence_role": role,
    }


def _confidence(peer_count, peer_claim_count, scenario_number, paid_totals):
    count_score = min(peer_count / 5, 1)
    claims_score = min(peer_claim_count / 20, 1)
    scenario_score = {1: 1, 2: 0.78, 3: 0.58}[scenario_number]
    mean_paid = float(np.mean(paid_totals)) if paid_totals else 0
    dispersion = float(np.std(paid_totals) / mean_paid) if mean_paid else 1
    dispersion_score = max(0, 1 - min(dispersion, 1))
    score = round(100 * (0.35 * count_score + 0.2 * claims_score + 0.3 * scenario_score + 0.15 * dispersion_score))
    level = "High" if score >= 80 else "Medium" if score >= 55 else "Low"
    descriptor = "Low" if dispersion < 0.2 else "Moderate" if dispersion < 0.5 else "High"
    return {"score": score, "level": level, "dispersion": f"{descriptor} payer-spend dispersion"}


def _lower_spend_group(peer_episodes, target_payer_spend):
    """Select the deterministic lower-cost episode cohort without inventing peers."""
    ordered = sorted(
        peer_episodes,
        key=lambda episode: (
            episode["total_paid"],
            episode["claim_count"],
            episode["member_id"],
            episode["episode_id"],
        ),
    )
    count = len(ordered)
    if count >= 5:
        selected = ordered[: max(1, math.ceil(count * 0.40))]
        method = "Median payer spend of the lowest 40% of qualifying peer episodes"
    elif count >= 3:
        selected = ordered[:2]
        method = "Median payer spend of the two lowest-cost qualifying peer episodes"
    elif count == 2:
        selected = ordered[:1]
        method = "Lowest-cost qualifying peer episode"
    elif count == 1 and ordered[0]["total_paid"] < target_payer_spend:
        selected = ordered
        method = "Single lower-cost external peer episode"
    else:
        selected = []
        method = "No qualifying lower-cost external peer episode"

    benchmark = (
        _money(median(episode["total_paid"] for episode in selected))
        if selected
        else None
    )
    return selected, benchmark, method


def _lower_utilisation_group(peer_episodes):
    ordered = sorted(
        peer_episodes,
        key=lambda episode: (
            episode["claim_count"],
            episode["total_paid"],
            episode["member_id"],
            episode["episode_id"],
        ),
    )
    count = len(ordered)
    if count >= 5:
        selected = ordered[: max(1, math.ceil(count * 0.40))]
        method = "Median claim count of the lowest 40% utilisation peer episodes"
    elif count >= 3:
        selected = ordered[:2]
        method = "Median claim count of the two lowest-utilisation peer episodes"
    elif count == 2:
        selected = ordered[:1]
        method = "Lowest-utilisation qualifying peer episode"
    else:
        selected = ordered[:1]
        method = "Single qualifying external peer episode"
    return selected, method


def _prediction_range(
    target_payer_spend,
    predicted,
    excess_claim_count,
    q25_paid_per_claim,
    q75_paid_per_claim,
    lower_spend_episodes,
):
    utilisation_low = round(excess_claim_count * q25_paid_per_claim, 2)
    utilisation_high = round(excess_claim_count * q75_paid_per_claim, 2)
    cost_low = cost_high = 0.0
    if lower_spend_episodes:
        lower_totals = [episode["total_paid"] for episode in lower_spend_episodes]
        lower_q25, lower_q75 = [float(value) for value in np.percentile(lower_totals, [25, 75])]
        cost_low = round(max(target_payer_spend - lower_q75, 0), 2)
        cost_high = round(max(target_payer_spend - lower_q25, 0), 2)

    low = round(min(predicted, max(utilisation_low, cost_low)), 2)
    high = round(min(target_payer_spend, max(predicted, utilisation_high, cost_high)), 2)
    return {"low": max(0.0, low), "high": max(0.0, high)}


def _benchmark_label(target_claim_count, benchmark_claim_count, target_payer_spend, lower_spend_benchmark):
    lower_utilisation = benchmark_claim_count < target_claim_count
    lower_spend = lower_spend_benchmark is not None and lower_spend_benchmark < target_payer_spend
    if lower_utilisation and lower_spend:
        return "Lower-Utilisation + Lower-Spend Benchmark"
    if lower_utilisation:
        return "Lower-Utilisation Benchmark"
    if lower_spend:
        return "Lower-Spend Peer Benchmark"
    return "Matched Payer-Spend Benchmark"


def build_payer_prediction(database, member_id, diagnosis_family, comparison_episode_id):
    all_episodes = _episodes(database.claims)
    target = next((item for item in all_episodes if item["member_id"] == member_id and item["diagnosis_family"] == diagnosis_family and item["episode_id"] == comparison_episode_id), None)
    if not target:
        raise ValueError("The selected comparison episode was not found for this member and disease family.")
    if target["duration_days"] > 90:
        raise ValueError("The selected comparison episode exceeds the 90-day comparison window.")
    selectable_claim_ids = {_claim_id(claim) for claim in database.selectable_claims}
    detail_claim = next(
        (row for row in target["rows"] if _claim_id(row) in selectable_claim_ids),
        target["rows"][0],
    )

    scenario_names = {1: "Strict Match", 2: "Same Disease + Same Payer", 3: "Same Disease Only"}
    candidates = []
    scenario_number = 0
    for number in (1, 2, 3):
        matches = [item for item in all_episodes if _scenario_match(target, item, number) and item["duration_days"] <= 90]
        if matches:
            scenario_number, candidates = number, matches
            break
    if not candidates:
        raise ValueError("No different-member peer episodes satisfy any of the three comparison scenarios.")

    best_episode_by_member = {}
    for candidate in sorted(candidates, key=lambda item: (item["claim_count"], item["total_paid"], item["episode_id"])):
        best_episode_by_member.setdefault(candidate["member_id"], candidate)
    ordered = list(best_episode_by_member.values())
    lower_group_size = max(1, (len(ordered) + 1) // 2)
    peers = ordered[:lower_group_size]
    peer_paid_per_claim = [
        _number(_field(row, "Paid_Amount", "paid"))
        for peer in candidates for row in peer["rows"]
    ]
    lower_spend_peers, lower_spend_benchmark, lower_spend_method = _lower_spend_group(
        candidates,
        target["total_paid"],
    )
    benchmark_claim_count = round(float(median([peer["claim_count"] for peer in peers])), 1)
    display_benchmark_paid = (
        lower_spend_benchmark
        if lower_spend_benchmark is not None
        else round(float(median([peer["total_paid"] for peer in peers])), 2)
    )
    duration_source = lower_spend_peers or peers
    benchmark_duration = round(float(median([peer["duration_days"] for peer in duration_source])), 1)
    benchmark_median_paid = round(float(median(peer_paid_per_claim)), 2)
    excess_claim_count = max(target["claim_count"] - benchmark_claim_count, 0)
    utilisation_opportunity = _money(excess_claim_count * benchmark_median_paid)
    payer_spend_opportunity = (
        _money(max(target["total_paid"] - lower_spend_benchmark, 0))
        if lower_spend_benchmark is not None
        else 0.0
    )
    predicted = _money(min(target["total_paid"], max(utilisation_opportunity, payer_spend_opportunity)))
    q25, q75 = [round(float(value), 2) for value in np.percentile(peer_paid_per_claim, [25, 75])]
    range_value = _prediction_range(
        target["total_paid"],
        predicted,
        excess_claim_count,
        q25,
        q75,
        lower_spend_peers,
    )
    unique_peer_members = {peer["member_id"] for peer in candidates}
    confidence = _confidence(
        len(unique_peer_members),
        sum(peer["claim_count"] for peer in candidates),
        scenario_number,
        [peer["total_paid"] for peer in candidates],
    )

    peer_members = [{
        "member_id": peer["member_id"],
        "related_claims": peer["claim_count"],
        "payer_spend": peer["total_paid"],
        "similarity": _similarity(target, peer, scenario_number),
        "role": "Benchmark Peer",
    } for peer in peers]
    evidence = [_evidence_row(row, "Target Episode") for row in target["rows"]]
    evidence.extend(_evidence_row(row, "Benchmark Peer") for peer in peers for row in peer["rows"])
    zero_reason = ""
    if predicted == 0:
        zero_reason = "Neither excess utilisation nor a lower matched payer-spend benchmark produced a positive opportunity."

    return {
        "target": {
            "member_id": target["member_id"], "diagnosis_family": target["diagnosis_family"],
            "comparison_episode_id": target["episode_id"], "claim_id": _claim_id(detail_claim),
            "claim_count": target["claim_count"], "total_paid": target["total_paid"],
            "episode_duration_days": target["duration_days"],
        },
        "scenario": {
            "number": scenario_number, "name": scenario_names[scenario_number],
            "peer_member_count": len(unique_peer_members),
            "peer_claim_count": sum(peer["related_claims"] for peer in peer_members),
        },
        "benchmark_summary": {
            "target_claim_count": target["claim_count"], "benchmark_claim_count": benchmark_claim_count,
            "target_payer_spend": target["total_paid"], "benchmark_payer_spend": display_benchmark_paid,
            "target_median_paid_per_claim": target["median_paid"], "benchmark_median_paid_per_claim": benchmark_median_paid,
            "target_episode_duration": target["duration_days"], "benchmark_episode_duration": benchmark_duration,
            "benchmark_label": _benchmark_label(
                target["claim_count"], benchmark_claim_count, target["total_paid"], lower_spend_benchmark,
            ),
            "benchmark_method": lower_spend_method,
        },
        "peer_members_used": peer_members,
        "calculation_summary": {
            "excess_claim_count": excess_claim_count, "median_peer_paid_per_claim": benchmark_median_paid,
            "q25_peer_paid_per_claim": q25, "q75_peer_paid_per_claim": q75,
            "utilisation_reduction_opportunity": utilisation_opportunity,
            "payer_spend_reduction_opportunity": payer_spend_opportunity,
            "lower_spend_peer_benchmark": lower_spend_benchmark,
            "count_based_excess_spend": utilisation_opportunity,
            "cost_based_excess_spend": payer_spend_opportunity,
            "predicted_payer_avoidable_spend": predicted, "range": range_value,
            "zero_reason": zero_reason, "confidence": confidence,
        },
        "supporting_evidence": evidence,
        "evidence_trace": {
            "source": "837_Claims", "scenario": scenario_number,
            "peer_member_count": len(unique_peer_members),
            "peer_claim_count": sum(peer["related_claims"] for peer in peer_members),
        },
    }


def _similar_units(target_units, peer_units):
    return abs(peer_units - target_units) <= PAYER_SCENARIO1_UNIT_TOLERANCE


def _strict_peer_match(target, peer):
    selected = target["selected_identity"]
    for identity in peer["anchor_identities"]:
        procedure_matches = (
            selected["cpt"] == identity["cpt"]
            or (
                selected["procedure_family"]
                and selected["procedure_family"] == identity["procedure_family"]
            )
        )
        if (
            selected["payer_id"]
            and selected["payer_id"] == identity["payer_id"]
            and selected["provider"]
            and selected["provider"] == identity["provider"]
            and selected["pos"]
            and selected["pos"] == identity["pos"]
            and procedure_matches
            and _similar_units(selected["units"], identity["units"])
        ):
            return True
    return False


def _claim_scenario_match(target, peer, number):
    if peer["member_id"] == target["member_id"] or peer["diagnosis_family"] != target["diagnosis_family"]:
        return False
    if number == 1:
        return _strict_peer_match(target, peer)
    if number == 2:
        return bool(target["selected_identity"]["payer_id"] and any(
            identity["payer_id"] == target["selected_identity"]["payer_id"]
            for identity in peer["anchor_identities"]
        ))
    return True


def _claim_similarity(target, peer, scenario_number):
    selected = target["selected_identity"]
    best = 0
    for identity in peer["anchor_identities"]:
        matches = 1
        matches += int(bool(selected["payer_id"]) and selected["payer_id"] == identity["payer_id"])
        matches += int(bool(selected["provider"]) and selected["provider"] == identity["provider"])
        matches += int(bool(selected["pos"]) and selected["pos"] == identity["pos"])
        matches += int(
            selected["cpt"] == identity["cpt"]
            or (selected["procedure_family"] and selected["procedure_family"] == identity["procedure_family"])
        )
        matches += int(_similar_units(selected["units"], identity["units"]))
        best = max(best, matches)
    floor = {1: 0.9, 2: 0.65, 3: 0.45}[scenario_number]
    return round(100 * min(0.99, max(floor, best / 6)))


def _episode_match_metrics(target, peer):
    selected = target["selected_identity"]
    identities = peer["anchor_identities"]
    return {
        "exact_icd_match": any(
            bool(selected["exact_icd"]) and selected["exact_icd"] == identity["exact_icd"]
            for identity in identities
        ),
        "payer_match": any(
            bool(selected["payer_id"]) and selected["payer_id"] == identity["payer_id"]
            for identity in identities
        ),
        "provider_match": any(
            bool(selected["provider"]) and selected["provider"] == identity["provider"]
            for identity in identities
        ),
        "cpt_match": any(
            selected["cpt"] == identity["cpt"]
            or (
                selected["procedure_family"]
                and selected["procedure_family"] == identity["procedure_family"]
            )
            for identity in identities
        ),
        "pos_match": any(
            bool(selected["pos"]) and selected["pos"] == identity["pos"]
            for identity in identities
        ),
        "units_match": any(
            _similar_units(selected["units"], identity["units"])
            for identity in identities
        ),
    }


def _benchmark_group_size(peer_count):
    total_cohort_members = peer_count + 1
    if total_cohort_members >= 5:
        return max(1, min(peer_count, math.ceil(peer_count * 0.40)))
    if total_cohort_members >= 3:
        return min(2, peer_count)
    return 1


def _claim_confidence(peer_count, peer_claim_count, scenario_number, paid_totals, target, peers):
    base = _confidence(peer_count, peer_claim_count, scenario_number, paid_totals)
    drivers = [
        f"Scenario {scenario_number} rule specificity",
        f"{peer_count} different-member peer member(s)",
        f"{len(peers)} qualifying peer episode(s)",
        f"{peer_claim_count} peer claim(s)",
    ]
    penalties = []
    match_metrics = [_episode_match_metrics(target, peer) for peer in peers]
    agreement_rates = {
        name: sum(int(metrics[name]) for metrics in match_metrics) / len(match_metrics)
        for name in (
            "exact_icd_match",
            "payer_match",
            "provider_match",
            "cpt_match",
            "pos_match",
            "units_match",
        )
    }
    labels = {
        "exact_icd_match": "exact ICD",
        "payer_match": "payer",
        "provider_match": "provider/location proxy",
        "cpt_match": "CPT or procedure family",
        "pos_match": "place of service",
        "units_match": "units",
    }
    for name, rate in agreement_rates.items():
        if rate == 1:
            drivers.append(f"All peer episodes matched {labels[name]}")
        elif rate == 0:
            penalties.append(f"Peer episodes differed on {labels[name]}")
        else:
            penalties.append(f"Only {round(rate * 100)}% of peer episodes matched {labels[name]}")
    agreement_score = float(np.mean(list(agreement_rates.values())))
    base["score"] = max(0, min(100, round(base["score"] + (agreement_score - 0.5) * 20)))
    base["level"] = "High" if base["score"] >= 80 else "Medium" if base["score"] >= 55 else "Low"
    historical_count = sum(
        1 for peer in peers for row in peer["rows"]
        if _text(_field(row, "Is_Historical_Reference_Record")).upper() == "Y"
    )
    if historical_count:
        drivers.append(f"{historical_count} historical-reference claim(s) support the cohort")
    duration_difference = abs(target["duration_days"] - float(median(peer["duration_days"] for peer in peers)))
    if duration_difference <= 30:
        drivers.append("Comparable episode-duration pattern")
    else:
        penalties.append("Peer episode durations differ materially from the target")
    claim_counts = [peer["claim_count"] for peer in peers]
    claim_mean = float(np.mean(claim_counts)) if claim_counts else 0
    claim_dispersion = float(np.std(claim_counts) / claim_mean) if claim_mean else 0
    if claim_dispersion > 0.5:
        penalties.append("High peer claim-count dispersion")
    else:
        drivers.append("Stable peer claim-count distribution")
    if base["dispersion"].startswith("High"):
        penalties.append(base["dispersion"])
    else:
        drivers.append(base["dispersion"])
    if peer_count == 1:
        base["score"] = min(base["score"], 49)
        base["level"] = "Low"
        penalties.append("Only one external peer member was available")
    base["drivers"] = drivers
    base["penalties"] = penalties
    return base


def _choose_member_episode(target, episodes):
    target_date = target["selected_date"]
    prior = [episode for episode in episodes if _day(episode["end_date"]) <= target_date]
    source = prior or episodes
    return max(source, key=lambda episode: (episode["end_date"], episode["start_date"]))


def _comparison_peer_episodes(target, matches):
    """Keep every comparable prior episode while preserving different-member counts."""
    by_member = defaultdict(list)
    for episode in matches:
        by_member[episode["member_id"]].append(episode)
    selected = []
    for episodes in by_member.values():
        prior = [episode for episode in episodes if _day(episode["end_date"]) <= target["selected_date"]]
        selected.extend(prior or episodes)
    return selected


def _episode_display_value(episode, workbook_field, canonical=None):
    values = {
        _text(_field(row, workbook_field, canonical))
        for row in episode["rows"]
        if _text(_field(row, workbook_field, canonical))
    }
    return ", ".join(sorted(values))


def _match_status(values):
    if values and all(values):
        return "Yes"
    if any(values):
        return "Partial"
    return "No"


def _aggregate_peer_members(target, peers, lower_utilisation_episode_ids, lower_spend_episode_ids, scenario_number):
    grouped = defaultdict(list)
    for peer in peers:
        grouped[peer["member_id"]].append(peer)

    rows = []
    for member_id in sorted(grouped):
        episodes = sorted(grouped[member_id], key=lambda episode: (episode["start_date"], episode["episode_id"]))
        episode_rows = []
        for episode in episodes:
            metrics = _episode_match_metrics(target, episode)
            episode_rows.append({
                "peer_episode_id": episode["episode_id"],
                "episode_start": episode["start_date"],
                "episode_end": episode["end_date"],
                "claim_count": episode["claim_count"],
                "total_paid": episode["total_paid"],
                "median_paid_per_claim": episode["median_paid"],
                "episode_duration_days": episode["duration_days"],
                "claim_ids": [_claim_id(row) for row in episode["rows"]],
                **metrics,
            })

        lower_utilisation = any(
            episode["episode_id"] in lower_utilisation_episode_ids
            and episode["claim_count"] < target["claim_count"]
            for episode in episodes
        )
        lower_spend = any(episode["episode_id"] in lower_spend_episode_ids for episode in episodes)
        if lower_utilisation and lower_spend:
            role = "Lower-Utilisation + Lower-Spend Peer"
        elif lower_utilisation:
            role = "Lower-Utilisation Peer"
        elif lower_spend:
            role = "Lower-Spend Peer"
        else:
            role = "Matched Peer"

        totals = [episode["total_paid"] for episode in episodes]
        rows.append({
            "member_id": member_id,
            "diagnosis_family": target["diagnosis_family"],
            "exact_icd_match": _match_status([episode["exact_icd_match"] for episode in episode_rows]),
            "payer_match": _match_status([episode["payer_match"] for episode in episode_rows]),
            "provider_match": _match_status([episode["provider_match"] for episode in episode_rows]),
            "cpt_match": _match_status([episode["cpt_match"] for episode in episode_rows]),
            "pos_match": _match_status([episode["pos_match"] for episode in episode_rows]),
            "units_match": _match_status([episode["units_match"] for episode in episode_rows]),
            "peer_episode_count": len(episodes),
            "peer_claim_count": sum(episode["claim_count"] for episode in episodes),
            "payer_spend_range": {"low": min(totals), "high": max(totals)},
            "similarity": round(float(median(_claim_similarity(target, episode, scenario_number) for episode in episodes))),
            "benchmark_role": role,
            "episodes": episode_rows,
        })
    return rows


def build_payer_prediction_for_claim(database, claim_number):
    """Build one canonical payer cohort result anchored to a selectable claim."""
    selected_claim = database.find_claim(claim_number, selectable_only=True)
    if not selected_claim:
        raise KeyError(f"Selectable claim not found: {claim_number}")
    selected_claim_id = _claim_id(selected_claim)
    selected_member_id = _member_id(selected_claim)
    selected_family = _family(selected_claim)
    selected_date = _day(_field(selected_claim, "Service_Date_From", "dos"))
    if not selected_member_id or not selected_family or not selected_date:
        raise ValueError("The selected claim is missing Member_ID, ICD10_Family, or Service_Date_From.")

    cached_target = next(
        (
            episode for episode in _database_episodes(database, "target")
            if any(_claim_id(row) == selected_claim_id for row in episode["rows"])
        ),
        None,
    )
    if not cached_target:
        raise ValueError("A 90-day disease comparison episode could not be built for the selected claim.")
    target = dict(cached_target)
    target["selected_date"] = selected_date
    target["selected_identity"] = {
        "exact_icd": _text(_field(selected_claim, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "payer_id": _text(_field(selected_claim, "Payer_ID", "payerId")),
        "provider": _text(_field(selected_claim, "Billing_Provider_NPI", "billingProviderNpi")),
        "cpt": _text(_field(selected_claim, "CPT_Code", "cptCode")),
        "procedure_family": _procedure_family(selected_claim),
        "pos": _text(_field(selected_claim, "Place_of_Service_Code", "placeOfServiceCode")),
        "units": _number(_field(selected_claim, "Units", "units")),
    }

    peer_episodes = _database_episodes(database, "peer")
    scenario_names = {
        1: "Strict Match",
        2: "Same ICD-10 Family + Same Payer",
        3: "Same ICD-10 Family Only",
    }
    scenario_rule_descriptions = {
        1: "different-member peers matched ICD-10 family, payer, provider/location proxy, CPT or procedure family, POS, and similar units",
        2: "different-member peers matched ICD-10 family and payer; provider, CPT or procedure family, POS, and units may differ and do not qualify the cohort",
        3: "different-member peers matched ICD-10 family; payer, provider, CPT or procedure family, POS, and units may differ and do not qualify the cohort",
    }
    scenario_unavailable_reasons = {
        1: "no different-member peer matched payer, provider/location proxy, CPT or procedure family, POS, and similar units",
        2: "no different-member peer matched ICD-10 family and payer",
        3: "no different-member peer matched ICD-10 family",
    }
    scenario_cohorts = {}
    scenario_selection = {}
    for number in (1, 2, 3):
        matches = [episode for episode in peer_episodes if _claim_scenario_match(target, episode, number)]
        comparable = _comparison_peer_episodes(target, matches) if matches else []
        scenario_cohorts[number] = comparable
        unique_members = {episode["member_id"] for episode in comparable}
        available = bool(comparable)
        reason = (
            f"Scenario {number} available because {len(unique_members)} external member(s) and "
            f"{len(comparable)} peer episode(s) satisfied the rule: {scenario_rule_descriptions[number]}."
            if available
            else f"Scenario {number} unavailable because {scenario_unavailable_reasons[number]}."
        )
        scenario_selection[f"scenario_{number}"] = {
            "available": available,
            "reason": reason,
            "peer_member_count": len(unique_members),
            "peer_episode_count": len(comparable),
            "peer_claim_count": sum(episode["claim_count"] for episode in comparable),
        }

    scenario_number = 0
    cohort = []
    for number in (1, 2, 3):
        if scenario_selection[f"scenario_{number}"]["available"]:
            cohort = scenario_cohorts[number]
            scenario_number = number
            break
    if not cohort:
        raise ValueError("No different-member peer cohort was available after evaluating Scenarios 1, 2, and 3.")

    unique_peer_member_ids = {episode["member_id"] for episode in cohort}
    earlier_failures = " ".join(
        scenario_selection[f"scenario_{number}"]["reason"]
        for number in range(1, scenario_number)
    )
    selected_reason = (
        f"{earlier_failures} Scenario {scenario_number} selected because "
        f"{len(unique_peer_member_ids)} different member(s) supplied {len(cohort)} qualifying episode(s) "
        f"under the {scenario_names[scenario_number]} rule: {scenario_rule_descriptions[scenario_number]}."
    ).strip()
    scenario_selection["selected"] = {
        "number": scenario_number,
        "name": scenario_names[scenario_number],
        "reason": selected_reason,
        "peer_member_count": len(unique_peer_member_ids),
        "peer_episode_count": len(cohort),
        "peer_claim_count": sum(episode["claim_count"] for episode in cohort),
    }

    lower_utilisation_peers, lower_utilisation_method = _lower_utilisation_group(cohort)
    lower_spend_peers, lower_spend_benchmark, lower_spend_method = _lower_spend_group(
        cohort,
        target["total_paid"],
    )
    lower_utilisation_episode_ids = {peer["episode_id"] for peer in lower_utilisation_peers}
    lower_spend_episode_ids = {peer["episode_id"] for peer in lower_spend_peers}
    ordered_peers = sorted(
        cohort,
        key=lambda episode: (episode["member_id"], episode["start_date"], episode["total_paid"]),
    )
    all_peer_paid = [
        _number(_field(row, "Paid_Amount", "paid"))
        for peer in ordered_peers for row in peer["rows"]
    ]
    benchmark_claim_count = round(float(median(peer["claim_count"] for peer in lower_utilisation_peers)), 1)
    q25, peer_median, q75 = [round(float(value), 2) for value in np.percentile(all_peer_paid, [25, 50, 75])]
    excess_claim_count = max(target["claim_count"] - benchmark_claim_count, 0)
    utilisation_opportunity = _money(excess_claim_count * peer_median)
    payer_spend_opportunity = (
        _money(max(target["total_paid"] - lower_spend_benchmark, 0))
        if lower_spend_benchmark is not None
        else 0.0
    )
    predicted = _money(min(target["total_paid"], max(utilisation_opportunity, payer_spend_opportunity)))
    prediction_range = _prediction_range(
        target["total_paid"],
        predicted,
        excess_claim_count,
        q25,
        q75,
        lower_spend_peers,
    )
    confidence = _claim_confidence(
        len(unique_peer_member_ids),
        sum(peer["claim_count"] for peer in ordered_peers),
        scenario_number,
        [peer["total_paid"] for peer in ordered_peers],
        target,
        ordered_peers,
    )

    peer_members = _aggregate_peer_members(
        target,
        ordered_peers,
        lower_utilisation_episode_ids,
        lower_spend_episode_ids,
        scenario_number,
    )
    evidence = [_evidence_row(row, "Target Episode") for row in target["rows"]]
    for peer in ordered_peers:
        is_lower_utilisation = (
            peer["episode_id"] in lower_utilisation_episode_ids
            and peer["claim_count"] < target["claim_count"]
        )
        is_lower_spend = peer["episode_id"] in lower_spend_episode_ids
        if is_lower_spend:
            evidence_role = "Lower-Spend Benchmark Evidence"
        elif is_lower_utilisation:
            evidence_role = "Lower-Utilisation Benchmark Evidence"
        else:
            evidence_role = "Matched Peer"
        evidence.extend(_evidence_row(row, evidence_role) for row in peer["rows"])

    zero_reason = ""
    if predicted == 0:
        zero_reason = "Neither excess utilisation nor a lower matched payer-spend benchmark produced a positive opportunity."
    benchmark_label = _benchmark_label(
        target["claim_count"],
        benchmark_claim_count,
        target["total_paid"],
        lower_spend_benchmark,
    )
    if benchmark_label == "Lower-Utilisation + Lower-Spend Benchmark":
        benchmark_method = f"{lower_utilisation_method}; {lower_spend_method}"
    elif benchmark_label == "Lower-Utilisation Benchmark":
        benchmark_method = lower_utilisation_method
    else:
        benchmark_method = lower_spend_method
    lower_spend_detail = {
        "value": lower_spend_benchmark,
        "episodes_used": [peer["episode_id"] for peer in lower_spend_peers],
        "member_ids": sorted({peer["member_id"] for peer in lower_spend_peers}),
        "claim_ids": [_claim_id(row) for peer in lower_spend_peers for row in peer["rows"]],
        "method": lower_spend_method,
    }
    utilisation_detail = {
        "value": benchmark_claim_count,
        "episodes_used": [peer["episode_id"] for peer in lower_utilisation_peers],
        "member_ids": sorted({peer["member_id"] for peer in lower_utilisation_peers}),
        "claim_ids": [_claim_id(row) for peer in lower_utilisation_peers for row in peer["rows"]],
        "method": lower_utilisation_method,
    }
    return {
        "target": {
            "claim_id": selected_claim_id,
            "member_id": selected_member_id,
            "diagnosis_family": selected_family,
            "comparison_episode_id": target["episode_id"],
            "episode_start": target["start_date"],
            "episode_end": target["end_date"],
            "claim_count": target["claim_count"],
            "payer_spend": target["total_paid"],
            "median_paid_per_claim": target["median_paid"],
            "duration_days": target["duration_days"],
        },
        "scenario_selection": scenario_selection,
        "benchmark_summary": {
            "utilisation_benchmark_claim_count": benchmark_claim_count,
            "lower_spend_benchmark": lower_spend_benchmark,
            "benchmark_label": benchmark_label,
            "benchmark_method": benchmark_method,
            "peer_member_count": len(unique_peer_member_ids),
            "peer_episode_count": len(ordered_peers),
            "peer_claim_count": sum(peer["claim_count"] for peer in ordered_peers),
            "utilisation_benchmark": utilisation_detail,
            "lower_spend_benchmark_detail": lower_spend_detail,
        },
        "peer_members_used": peer_members,
        "calculation_summary": {
            "excess_claim_count": excess_claim_count,
            "q25_peer_paid_per_claim": q25,
            "median_peer_paid_per_claim": peer_median,
            "q75_peer_paid_per_claim": q75,
            "utilisation_reduction_opportunity": utilisation_opportunity,
            "lower_spend_benchmark": lower_spend_benchmark,
            "payer_spend_reduction_opportunity": payer_spend_opportunity,
            "predicted_payer_avoidable_spend": predicted,
            "range": prediction_range,
            "range_label": "Benchmark-Based Estimate Range",
            "confidence": confidence,
            "zero_reason": zero_reason,
            "formula_trace": [
                {"name": "utilisation", "formula": "excess_claim_count × median_peer_paid_per_claim", "value": utilisation_opportunity},
                {"name": "payer_spend", "formula": "max(target_episode_payer_spend − lower_spend_peer_benchmark, 0)", "value": payer_spend_opportunity},
                {"name": "final", "formula": "min(target_episode_payer_spend, max(utilisation_opportunity, payer_spend_opportunity))", "value": predicted},
            ],
        },
        "supporting_evidence": evidence,
    }


def _episode_anchor_claim_id(episode):
    anchor = min(
        episode["rows"],
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)),
    )
    return _claim_id(anchor)


def build_member_payer_cohort_summary(database, member_id):
    """Sum one selected-scenario result per non-overlapping member/disease episode."""
    cache_key = (database.workbook_hash, member_id, PAYER_COHORT_EPISODE_DAYS, PAYER_SCENARIO1_UNIT_TOLERANCE)
    cached = _MEMBER_PAYER_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    episodes = [
        episode for episode in _database_episodes(database, "target")
        if episode["member_id"] == member_id
    ]
    scenario_counts = {1: 0, 2: 0, 3: 0}
    episode_results = []
    unavailable_episodes = []
    for episode in sorted(episodes, key=lambda item: (item["start_date"], item["diagnosis_family"], item["episode_id"])):
        anchor_claim_id = _episode_anchor_claim_id(episode)
        try:
            result = build_payer_prediction_for_claim(database, anchor_claim_id)
        except ValueError as error:
            unavailable_episodes.append({
                "comparison_episode_id": episode["episode_id"],
                "claim_ids": [_claim_id(row) for row in episode["rows"]],
                "reason": str(error),
            })
            continue
        selected = result["scenario_selection"]["selected"]
        prediction = result["calculation_summary"]["predicted_payer_avoidable_spend"]
        scenario_counts[selected["number"]] += 1
        episode_results.append({
            "comparison_episode_id": episode["episode_id"],
            "claim_ids": [_claim_id(row) for row in episode["rows"]],
            "selected_scenario": selected,
            "predicted_payer_avoidable_spend": prediction,
        })

    claims_evaluated = sum(episode["claim_count"] for episode in episodes)
    summary = {
        "member_id": member_id,
        "claims_evaluated": claims_evaluated,
        "episodes_evaluated": len(episodes),
        "episodes_with_predictions": len(episode_results),
        "scenario_1_selected_count": scenario_counts[1],
        "scenario_2_selected_count": scenario_counts[2],
        "scenario_3_selected_count": scenario_counts[3],
        "positive_savings_predictions": sum(item["predicted_payer_avoidable_spend"] > 0 for item in episode_results),
        "zero_predictions": sum(item["predicted_payer_avoidable_spend"] == 0 for item in episode_results),
        "unavailable_predictions": len(unavailable_episodes),
        "member_predicted_payer_avoidable_spend": _money(sum(
            item["predicted_payer_avoidable_spend"] for item in episode_results
        )),
        "episodes": episode_results,
        "unavailable_episodes": unavailable_episodes,
    }
    _MEMBER_PAYER_SUMMARY_CACHE[cache_key] = summary
    return summary


def build_payer_cohort_portfolio_summary(database):
    """Aggregate deduplicated primary episode predictions across all selectable members."""
    cache_key = (database.workbook_hash, PAYER_COHORT_EPISODE_DAYS, PAYER_SCENARIO1_UNIT_TOLERANCE)
    cached = _PORTFOLIO_PAYER_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    member_ids = sorted({_member_id(claim) for claim in database.selectable_claims if _member_id(claim)})
    member_summaries = [build_member_payer_cohort_summary(database, member_id) for member_id in member_ids]
    summary = {
        "members_evaluated": len(member_summaries),
        "claims_evaluated": sum(item["claims_evaluated"] for item in member_summaries),
        "episodes_evaluated": sum(item["episodes_evaluated"] for item in member_summaries),
        "episodes_with_predictions": sum(item["episodes_with_predictions"] for item in member_summaries),
        "scenario_1_selected_count": sum(item["scenario_1_selected_count"] for item in member_summaries),
        "scenario_2_selected_count": sum(item["scenario_2_selected_count"] for item in member_summaries),
        "scenario_3_selected_count": sum(item["scenario_3_selected_count"] for item in member_summaries),
        "positive_savings_predictions": sum(item["positive_savings_predictions"] for item in member_summaries),
        "zero_predictions": sum(item["zero_predictions"] for item in member_summaries),
        "unavailable_predictions": sum(item["unavailable_predictions"] for item in member_summaries),
        "total_predicted_payer_avoidable_spend": _money(sum(
            item["member_predicted_payer_avoidable_spend"] for item in member_summaries
        )),
    }
    _PORTFOLIO_PAYER_SUMMARY_CACHE[cache_key] = summary
    return summary
