"""Authoritative deterministic payer spend benchmark predictions."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
import os
from statistics import median

import numpy as np


PAYER_COHORT_EPISODE_DAYS = int(os.getenv("PAYER_COHORT_EPISODE_DAYS", "90"))
PAYER_SCENARIO1_UNIT_TOLERANCE = float(os.getenv("PAYER_SCENARIO1_UNIT_TOLERANCE", "1"))
_COHORT_EPISODE_CACHE = {}
_TARGET_EPISODE_INDEX_CACHE = {}
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


def _is_historical_reference(claim):
    """Return whether the workbook marks this row as reference-only evidence."""
    return _text(_field(claim, "Is_Historical_Reference_Record")).upper() in {
        "Y", "YES", "TRUE", "1",
    }


def _is_synthetic_sequence_companion(claim):
    """Keep generated clinical-sequence rows out of unrelated payer cohorts."""
    return _text(_field(claim, "Reason_Code")).upper() in {
        "SYNTHETIC_SEQUENCE_WORSENING",
        "SYNTHETIC_SEQUENCE_PREVENTIVE",
    }


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
            # A new disease episode begins only when consecutive same-family
            # claims are separated by more than the configured 90-day window.
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
    claims = (
        [claim for claim in database.selectable_claims if not _is_historical_reference(claim)]
        if source == "target"
        else [claim for claim in database.claims if not _is_synthetic_sequence_companion(claim)]
    )
    episodes = _rolling_episodes(claims)
    _COHORT_EPISODE_CACHE[key] = episodes
    return episodes


def _target_episode_index(database):
    """Index selectable claim IDs once instead of scanning every episode per request."""
    key = (database.workbook_hash, PAYER_COHORT_EPISODE_DAYS)
    cached = _TARGET_EPISODE_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    index = {
        _claim_id(row): episode
        for episode in _database_episodes(database, "target")
        for row in episode["rows"]
    }
    if len(_TARGET_EPISODE_INDEX_CACHE) >= 2:
        _TARGET_EPISODE_INDEX_CACHE.pop(next(iter(_TARGET_EPISODE_INDEX_CACHE)), None)
    _TARGET_EPISODE_INDEX_CACHE[key] = index
    return index


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
    episodes = _database_episodes(database, "target")
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
        "icd10_family": _family(claim),
        "cpt": _text(_field(claim, "CPT_Code", "cptCode")),
        "payer_id": _text(_field(claim, "Payer_ID", "payerId")),
        "payer_name": _text(_field(claim, "Payer_Name", "payer")),
        "provider_npi": _text(_field(claim, "Billing_Provider_NPI", "billingProviderNpi")),
        "provider_name": _text(_field(claim, "Billing_Provider_Name", "billingProvider")),
        "pos": _text(_field(claim, "Place_of_Service_Code", "placeOfServiceCode")),
        "units": _number(_field(claim, "Units", "units")),
        "paid_amount": round(_number(_field(claim, "Paid_Amount", "paid")), 2),
        "is_historical_reference": _is_historical_reference(claim),
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
    level = "High" if score >= 80 else "Medium" if score >= 60 else "Low"
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
        selected = ordered[: max(1, math.floor(count * 0.40))]
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
        selected = ordered[: max(1, math.floor(count * 0.40))]
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


def _prediction_range(target_payer_spend, predicted, peer_episode_spends):
    """Use the selected-scenario peer episode distribution, never claim rows."""
    q25, peer_median, q75 = [
        _money(value) for value in np.percentile(peer_episode_spends, [25, 50, 75])
    ]
    low = _money(max(target_payer_spend - q75, 0))
    mid = _money(max(target_payer_spend - peer_median, 0))
    high = _money(max(target_payer_spend - q25, 0))
    return {
        "low": min(low, predicted),
        "mid": min(max(mid, low), predicted) if predicted else 0.0,
        "high": max(high, predicted),
        "q25_peer_episode_spend": q25,
        "median_peer_episode_spend": peer_median,
        "q75_peer_episode_spend": q75,
    }


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
    """Compatibility entry point for the existing modal.

    The modal still chooses a member episode, but the final calculation is always
    delegated to the selected-claim rule engine below.  This prevents two payer
    savings definitions from drifting apart.
    """
    target = next(
        (
            item for item in _database_episodes(database, "target")
            if item["member_id"] == member_id
            and item["diagnosis_family"] == diagnosis_family
            and item["episode_id"] == comparison_episode_id
        ),
        None,
    )
    if not target:
        raise ValueError("The selected comparison episode was not found for this member and disease family.")
    anchor = max(
        target["rows"],
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)),
    )
    return build_payer_prediction_for_claim(database, _claim_id(anchor))


def _similar_units(target_units, peer_units):
    return abs(peer_units - target_units) <= PAYER_SCENARIO1_UNIT_TOLERANCE


def _strict_peer_match(target, peer):
    selected = target["selected_identity"]
    for identity in peer["anchor_identities"]:
        procedure_matches = bool(selected["cpt"] and selected["cpt"] == identity["cpt"])
        if (
            selected["exact_icd"]
            and selected["exact_icd"] == identity["exact_icd"]
            and selected["payer_id"]
            and selected["payer_id"] == identity["payer_id"]
            and selected["provider"]
            and selected["provider"] == identity["provider"]
            and selected["pos"]
            and selected["pos"] == identity["pos"]
            and procedure_matches
            and selected["units"] == identity["units"]
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
    base["level"] = "High" if base["score"] >= 80 else "Medium" if base["score"] >= 60 else "Low"
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
    """Keep only peer episodes fully available on the selected claim's date."""
    return [
        episode for episode in matches
        if _day(episode["end_date"]) <= target["selected_date"]
    ]


def _historically_available_peer_episodes(database, target):
    """Build peer episodes using only claims known at the selected-claim cutoff."""
    cutoff = target["selected_date"]
    available = []
    for episode in _database_episodes(database, "peer"):
        if episode["member_id"] == target["member_id"]:
            continue
        if _day(episode["start_date"]) > cutoff:
            continue
        if _day(episode["end_date"]) <= cutoff:
            available.append(episode)
            continue

        # A disease window that crosses the cutoff must expose only the rows
        # that were known on that date. Rebuilding this one partial episode is
        # equivalent to rebuilding the entire historical database, at a small
        # fraction of the cost.
        rows = [
            row for row in episode["rows"]
            if (_day(_field(row, "Service_Date_From", "dos")) or date.max) <= cutoff
        ]
        if rows:
            available.append(_cohort_episode(
                rows,
                episode["member_id"],
                episode["diagnosis_family"],
                PAYER_COHORT_EPISODE_DAYS,
            ))
    return available


def _episode_display_value(episode, workbook_field, canonical=None):
    values = {
        _text(_field(row, workbook_field, canonical))
        for row in episode["rows"]
        if _text(_field(row, workbook_field, canonical))
    }
    return ", ".join(sorted(values))


def _procedure_comparison_claim(claim):
    """Return only the recorded fields needed to explain a two-patient comparison."""
    service_date = _day(_field(claim, "Service_Date_From", "dos"))
    return {
        "claim_id": _claim_id(claim),
        "member_id": _member_id(claim),
        "service_date": service_date.isoformat() if service_date else "",
        "icd10": _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "icd10_family": _family(claim),
        "diagnosis_description": _text(_field(
            claim,
            "ICD10_Diagnosis_Description",
            "diagnosisDescription",
        )),
        "cpt": _text(_field(claim, "CPT_Code", "cptCode")),
        "procedure_description": _text(_field(claim, "CPT_Description", "cptDescription")),
        "units": _number(_field(claim, "Units", "units")),
        "billed_amount": _money(_number(_field(claim, "Charge_Amount", "totalCharge"))),
        "paid_amount": _money(_number(_field(claim, "Paid_Amount", "paid"))),
        "payer_id": _text(_field(claim, "Payer_ID", "payerId")),
        "provider_npi": _text(_field(claim, "Billing_Provider_NPI", "billingProviderNpi")),
        "pos": _text(_field(claim, "Place_of_Service_Code", "placeOfServiceCode")),
        "is_historical_reference": _is_historical_reference(claim),
    }


def _prior_recorded_services(database, member_id, before_date, diagnosis_family):
    """List only earlier bills in the comparison visit's diagnosis family.

    Showing every bill in a 90-day window made unrelated care look like part
    of the comparison. Historical-reference rows are also excluded because
    they are synthetic comparators in this workbook, not the member's care
    history.
    """
    start_date = before_date - timedelta(days=PAYER_COHORT_EPISODE_DAYS)
    rows = [
        row for row in database.selectable_claims
        if _member_id(row) == member_id
        and _family(row) == diagnosis_family
        and (service_date := _day(_field(row, "Service_Date_From", "dos")))
        and start_date <= service_date < before_date
    ]
    ordered = sorted(
        rows,
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)),
        reverse=True,
    )
    return [_procedure_comparison_claim(row) for row in ordered[:10]]


def _procedure_match_label(target_claim, peer_claim):
    target_cpt = _text(_field(target_claim, "CPT_Code", "cptCode"))
    peer_cpt = _text(_field(peer_claim, "CPT_Code", "cptCode"))
    if target_cpt and target_cpt == peer_cpt:
        return "Exact billing-code match"
    if _procedure_family(target_claim) and _procedure_family(target_claim) == _procedure_family(peer_claim):
        return "Same procedure-code family"
    return "Different recorded procedure"


def _problem_match_label(target_claim, peer_claim):
    target_icd = _text(_field(target_claim, "ICD10_Diagnosis_Code", "diagnosisCode"))
    peer_icd = _text(_field(peer_claim, "ICD10_Diagnosis_Code", "diagnosisCode"))
    if target_icd and target_icd == peer_icd:
        return "Exact medical-code match"
    if _family(target_claim) == _family(peer_claim):
        return "Same medical-code family"
    return "Different recorded problem"


def _peer_claim_rank_details(target, row):
    """Return the transparent claim-level ranking inputs used inside a peer episode."""
    selected = target["selected_identity"]
    identity = {
        "exact_icd": _text(_field(row, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "payer_id": _text(_field(row, "Payer_ID", "payerId")),
        "provider": _text(_field(row, "Billing_Provider_NPI", "billingProviderNpi")),
        "cpt": _text(_field(row, "CPT_Code", "cptCode")),
        "procedure_family": _procedure_family(row),
        "pos": _text(_field(row, "Place_of_Service_Code", "placeOfServiceCode")),
        "units": _number(_field(row, "Units", "units")),
    }
    checks = {
        "exact_cpt": bool(selected["cpt"] and selected["cpt"] == identity["cpt"]),
        "procedure_family": bool(
            selected["procedure_family"] and selected["procedure_family"] == identity["procedure_family"]
        ),
        "exact_diagnosis": bool(selected["exact_icd"] and selected["exact_icd"] == identity["exact_icd"]),
        "payer": bool(selected["payer_id"] and selected["payer_id"] == identity["payer_id"]),
        "provider": bool(selected["provider"] and selected["provider"] == identity["provider"]),
        "place_of_service": bool(selected["pos"] and selected["pos"] == identity["pos"]),
        "similar_units": _similar_units(selected["units"], identity["units"]),
    }
    service_date = _day(_field(row, "Service_Date_From", "dos")) or date.min
    return {
        "checks": checks,
        "key": tuple(int(checks[name]) for name in (
            "exact_cpt",
            "procedure_family",
            "exact_diagnosis",
            "payer",
            "provider",
            "place_of_service",
            "similar_units",
        )) + (service_date, _claim_id(row)),
    }


def _representative_peer_claim(target, peer_episode):
    """Choose the closest billed service within an already eligible peer episode."""
    return max(peer_episode["rows"], key=lambda row: _peer_claim_rank_details(target, row)["key"])


def _claim_rank_lower_reason(selected_details, candidate_details):
    priorities = (
        ("exact_cpt", "it did not have the selected claim's exact procedure code"),
        ("procedure_family", "it did not have the selected claim's procedure-code family"),
        ("exact_diagnosis", "it did not have the selected claim's exact diagnosis code"),
        ("payer", "it did not have the selected claim's payer"),
        ("provider", "it did not have the selected claim's billing provider"),
        ("place_of_service", "it did not have the selected claim's place of service"),
        ("similar_units", "its units were not within the allowed similarity tolerance"),
    )
    for key, reason in priorities:
        if selected_details["checks"][key] and not candidate_details["checks"][key]:
            return reason
        if candidate_details["checks"][key] and not selected_details["checks"][key]:
            return f"a higher-priority field outweighed its advantage on {key.replace('_', ' ')}"
    if selected_details["key"][-2] > candidate_details["key"][-2]:
        return "it was an older otherwise-equivalent claim"
    return "it ranked lower after the deterministic claim-ID tie-breaker"


def _build_procedure_comparison(database, target, selected_claim, peers, scenario_number):
    """Build an evidence-only, different-member procedure comparison for the UI."""
    target_claim = _procedure_comparison_claim(selected_claim)
    target_prior_services = _prior_recorded_services(
        database,
        target["member_id"],
        target["selected_date"],
        target["diagnosis_family"],
    )
    target_context = {
        "claim": target_claim,
        "prior_services": target_prior_services,
        "prior_services_total_paid": _money(sum(row["paid_amount"] for row in target_prior_services)),
    }
    fallback_match_type = ""
    fallback_reason = ""
    selection_audit = None
    if not peers:
        # Show the closest broad peer as an explicitly limited review aid when
        # no strict peer exists. The UI exposes every mismatch and never calls
        # this an accurate or savings-supported comparison.
        candidate_pool = [
            c for c in (getattr(database, "selectable_claims", None) or getattr(database, "claims", []))
            if _member_id(c) != target["member_id"]
        ]
        if not candidate_pool:
            return {
                "available": False,
                "history_window_days": PAYER_COHORT_EPISODE_DAYS,
                "reason": "No different-member peer visit was available.",
                "target": target_context,
            }
        target_family = target["diagnosis_family"]
        same_fam = [c for c in candidate_pool if _family(c) == target_family]
        try:
            from backend.patient_comparison import organ_for_family as _get_organ
            target_organ, _ = _get_organ(target_family)
            same_organ = [c for c in candidate_pool if _get_organ(_family(c))[0] == target_organ]
        except Exception:
            target_organ = target_family[:1]
            same_organ = [c for c in candidate_pool if _family(c).startswith(target_organ)]

        target_cpt = _text(_field(selected_claim, "CPT_Code", "cptCode"))
        same_cpt = [c for c in candidate_pool if _text(_field(c, "CPT_Code", "cptCode")) == target_cpt]

        if same_fam:
            chosen_claims = same_fam
            fallback_match_type = "same medical-code family"
            fallback_reason = f"This different-member peer visit was selected using the same diagnosis family ({target_family}) benchmark."
        elif same_organ:
            chosen_claims = same_organ
            fallback_match_type = f"same organ system ({target_organ})"
            fallback_reason = f"This different-member peer visit was selected using the same organ system ({target_organ}) benchmark."
        elif same_cpt:
            chosen_claims = same_cpt
            fallback_match_type = "same clinical procedure"
            fallback_reason = "This different-member peer visit was selected using the same procedure benchmark."
        else:
            chosen_claims = candidate_pool
            fallback_match_type = "clinical peer comparison"
            fallback_reason = "This different-member peer visit was selected using clinical peer comparison."

        def _score(c):
            s = 0
            if _text(_field(c, "CPT_Code", "cptCode")) == target_cpt:
                s += 10
            if _text(_field(c, "Payer_ID", "payerId")) == target.get("selected_identity", {}).get("payer_id"):
                s += 5
            return s

        best_peer_claim = max(
            chosen_claims,
            key=lambda c: (_score(c), _day(_field(c, "Service_Date_From", "dos")) or date.min, _claim_id(c)),
        )
        peer_mid = _member_id(best_peer_claim)
        peer_fam = _family(best_peer_claim)
        peer_member_rows = [c for c in candidate_pool if _member_id(c) == peer_mid and _family(c) == peer_fam]
        if not peer_member_rows:
            peer_member_rows = [best_peer_claim]
        peer_episode = _episode(peer_member_rows)
        peer_episode["episode_id"] = f"PEER-{peer_mid}-{peer_fam}"
        peer_claim = best_peer_claim
        ranked_fallback_claims = sorted(
            chosen_claims,
            key=lambda row: (_score(row), _day(_field(row, "Service_Date_From", "dos")) or date.min, _claim_id(row)),
            reverse=True,
        )
        selected_details = _peer_claim_rank_details(target, peer_claim)
        fallback_claim_candidates = []
        for rank, row in enumerate(ranked_fallback_claims[:10], 1):
            candidate = _procedure_comparison_claim(row)
            details = _peer_claim_rank_details(target, row)
            selected = candidate["claim_id"] == _claim_id(peer_claim)
            fallback_claim_candidates.append({
                "rank": rank,
                "selected": selected,
                "claim_id": candidate["claim_id"],
                "member_id": candidate["member_id"],
                "service_date": candidate["service_date"],
                "diagnosis_code": candidate["icd10"],
                "procedure_code": candidate["cpt"],
                "units": candidate["units"],
                "billed_amount": candidate["billed_amount"],
                "checks": details["checks"],
                "decision": "Selected by the broad fallback rule." if selected else f"Ranked lower because {_claim_rank_lower_reason(selected_details, details)}.",
            })
        selection_audit = {
            "scenario_number": 0,
            "scenario_name": f"Broad fallback: {fallback_match_type}",
            "scenario_reason": "No strict eligible peer was available. This broader comparison is shown for transparency only and is not an accurate comparison.",
            "eligible_episode_count": len({(_member_id(row), _family(row)) for row in chosen_claims}),
            "eligible_claim_count": len(chosen_claims),
            "exact_procedure_episode_count": sum(_text(_field(row, "CPT_Code", "cptCode")) == target_cpt for row in chosen_claims),
            "procedure_filter_reason": f"The broad fallback considered {len(chosen_claims)} claim(s) with {fallback_match_type} evidence; it did not require every comparison field to match.",
            "amounts_used_for_selection": False,
            "plain_language_steps": [
                {"title": "Check strict matching", "text": "No different-member claim matched every required diagnosis, procedure, units, payer, provider, and place-of-service field."},
                {"title": "Choose the broadest relevant pool", "text": fallback_reason},
                {"title": "Rank the available claims", "text": "Claims were ranked by matching procedure and payer identifiers, then recency and claim ID. Billed and paid amounts were excluded."},
                {"title": "Show the limitation", "text": f"{_claim_id(peer_claim)} is the closest broad candidate, but the mismatch table below determines why this is not an accurate comparison."},
            ],
            "selection_guardrail": "Neither billed amount nor paid amount is used to choose the peer. Amounts are read only after selection to calculate the displayed difference.",
            "episode_ranking_priority": "Broad clinical relevance, matching procedure and payer identifiers, recency, then claim ID. Dollar amounts are excluded.",
            "claim_ranking_priority": "Matching procedure and payer identifiers, recency, then claim ID. Dollar amounts are excluded.",
            "selected_episode_id": peer_episode["episode_id"],
            "selected_claim_id": _claim_id(peer_claim),
            "episode_candidates": [{
                "rank": 1,
                "selected": True,
                "episode_id": peer_episode["episode_id"],
                "representative_claim_id": _claim_id(peer_claim),
                "member_id": peer_episode["member_id"],
                "episode_start": peer_episode["start_date"],
                "episode_end": peer_episode["end_date"],
                "similarity_score": 0,
                "claim_count": peer_episode["claim_count"],
                "exact_diagnosis_match": False,
                "exact_or_family_procedure_match": _text(_field(peer_claim, "CPT_Code", "cptCode")) == target_cpt,
                "similar_units_present": False,
                "payer_match": _text(_field(peer_claim, "Payer_ID", "payerId")) == target["selected_identity"].get("payer_id"),
                "provider_match": False,
                "place_of_service_match": False,
            }],
            "claim_candidates": fallback_claim_candidates,
        }
    else:
        target_cpt = _text(_field(selected_claim, "CPT_Code", "cptCode"))
        exact_procedure_peers = [
            episode for episode in peers
            if any(_text(_field(row, "CPT_Code", "cptCode")) == target_cpt for row in episode["rows"])
        ]
        comparison_peers = exact_procedure_peers or peers
        def episode_rank_key(episode):
            return (
                _claim_similarity(target, episode, scenario_number),
                _episode_match_metrics(target, episode)["exact_icd_match"],
                _episode_match_metrics(target, episode)["cpt_match"],
                episode["end_date"],
                episode["episode_id"],
            )

        ranked_episodes = sorted(comparison_peers, key=episode_rank_key, reverse=True)
        peer_episode = ranked_episodes[0]
        peer_claim = _representative_peer_claim(target, peer_episode)
        selected_claim_details = _peer_claim_rank_details(target, peer_claim)
        ranked_claims = sorted(
            peer_episode["rows"],
            key=lambda row: _peer_claim_rank_details(target, row)["key"],
            reverse=True,
        )
        episode_candidates = []
        for rank, episode in enumerate(ranked_episodes, 1):
            representative = _procedure_comparison_claim(_representative_peer_claim(target, episode))
            metrics = _episode_match_metrics(target, episode)
            episode_candidates.append({
                "rank": rank,
                "selected": episode["episode_id"] == peer_episode["episode_id"],
                "episode_id": episode["episode_id"],
                "representative_claim_id": representative["claim_id"],
                "member_id": episode["member_id"],
                "episode_start": episode["start_date"],
                "episode_end": episode["end_date"],
                "similarity_score": _claim_similarity(target, episode, scenario_number),
                "claim_count": episode["claim_count"],
                "exact_diagnosis_match": metrics["exact_icd_match"],
                "exact_or_family_procedure_match": metrics["cpt_match"],
                "similar_units_present": metrics["units_match"],
                "payer_match": metrics["payer_match"],
                "provider_match": metrics["provider_match"],
                "place_of_service_match": metrics["pos_match"],
            })
        claim_candidates = []
        for rank, row in enumerate(ranked_claims, 1):
            candidate = _procedure_comparison_claim(row)
            details = _peer_claim_rank_details(target, row)
            selected = candidate["claim_id"] == _claim_id(peer_claim)
            claim_candidates.append({
                "rank": rank,
                "selected": selected,
                "claim_id": candidate["claim_id"],
                "member_id": candidate["member_id"],
                "service_date": candidate["service_date"],
                "diagnosis_code": candidate["icd10"],
                "procedure_code": candidate["cpt"],
                "units": candidate["units"],
                "billed_amount": candidate["billed_amount"],
                "checks": details["checks"],
                "decision": (
                    "Selected as the highest-ranked claim in the winning episode."
                    if selected
                    else f"Ranked lower because {_claim_rank_lower_reason(selected_claim_details, details)}."
                ),
            })
        scenario_names = {1: "Strict match", 2: "Same ICD-10 family + same payer", 3: "Same ICD-10 family"}
        scenario_gate = {
            1: "another member, an earlier service date, the same diagnosis family, payer, provider, procedure family, place of service, and similar units",
            2: "another member, an earlier service date, the same diagnosis family, and the same payer",
            3: "another member, an earlier service date, and the same diagnosis family",
        }.get(scenario_number, "another member and an earlier service date")
        selected_episode_candidate = episode_candidates[0]
        next_episode_candidate = episode_candidates[1] if len(episode_candidates) > 1 else None
        lower_claim_reasons = " ".join(
            f"{candidate['claim_id']} was not selected: {candidate['decision'].removeprefix('Ranked lower because ').rstrip('.')} ."
            for candidate in claim_candidates[1:3]
        ).replace(" .", ".")
        selection_audit = {
            "scenario_number": scenario_number,
            "scenario_name": scenario_names.get(scenario_number, "Fallback comparison"),
            "scenario_reason": (
                f"Scenario {scenario_number} was the most specific available comparison rule. "
                + (f"Scenario{'s' if scenario_number > 2 else ''} {', '.join(str(n) for n in range(1, scenario_number))} had no qualifying peer. " if scenario_number > 1 else "")
                + "Broader available scenarios were not used."
            ),
            "eligible_episode_count": len(peers),
            "eligible_claim_count": sum(episode["claim_count"] for episode in peers),
            "exact_procedure_episode_count": len(exact_procedure_peers),
            "procedure_filter_reason": (
                f"{len(exact_procedure_peers)} eligible episode(s) contained exact CPT {target_cpt}, so only those episodes were ranked."
                if exact_procedure_peers
                else f"No eligible episode contained exact CPT {target_cpt}, so all {len(peers)} eligible episode(s) were ranked and the limitation is disclosed."
            ),
            "amounts_used_for_selection": False,
            "plain_language_steps": [
                {
                    "title": "Create the eligible pool",
                    "text": f"The rule required {scenario_gate}. That left {len(peers)} eligible episode(s) containing {sum(episode['claim_count'] for episode in peers)} claim(s).",
                },
                {
                    "title": "Check for the same procedure",
                    "text": (
                        f"{len(exact_procedure_peers)} episode(s) contained exact CPT {target_cpt}; only those were considered further."
                        if exact_procedure_peers
                        else f"None of the eligible claims used CPT {target_cpt}. The model therefore continued with the closest available candidates, but marks the result as limited rather than an exact procedure comparison."
                    ),
                },
                {
                    "title": "Choose the closest episode",
                    "text": (
                        f"Episode {selected_episode_candidate['episode_id']} ranked first with {selected_episode_candidate['similarity_score']}% field agreement"
                        + (f", ahead of the next episode at {next_episode_candidate['similarity_score']}%." if next_episode_candidate else ".")
                        + " Episode matching can use evidence from more than one claim; it does not mean the final claim matches every field."
                    ),
                },
                {
                    "title": "Choose one claim from that episode",
                    "text": (
                        f"{_claim_id(peer_claim)} ranked first inside the selected episode using clinical and billing identifiers. "
                        + lower_claim_reasons
                    ).strip(),
                },
            ],
            "selection_guardrail": "Neither billed amount nor paid amount is used to choose the peer. Amounts are read only after selection to calculate the displayed difference.",
            "episode_ranking_priority": "Similarity score, exact diagnosis, procedure code or family, recency, then episode ID. Dollar amounts are excluded.",
            "claim_ranking_priority": "Exact CPT, procedure family, exact diagnosis, payer, provider, place of service, similar units, recency, then claim ID.",
            "selected_episode_id": peer_episode["episode_id"],
            "selected_claim_id": _claim_id(peer_claim),
            "episode_candidates": episode_candidates[:10],
            "claim_candidates": claim_candidates[:10],
        }

    peer_claim_payload = _procedure_comparison_claim(peer_claim)
    peer_prior_services = _prior_recorded_services(
        database,
        peer_episode["member_id"],
        _day(_field(peer_claim, "Service_Date_From", "dos")),
        target["diagnosis_family"],
    )
    target_paid = target_claim["paid_amount"]
    peer_paid = peer_claim_payload["paid_amount"]
    target_billed = target_claim["billed_amount"]
    peer_billed = peer_claim_payload["billed_amount"]
    visit_payer_spend_difference = _money(max(target_paid - peer_paid, 0))
    visit_billed_difference = _money(max(target_billed - peer_billed, 0))
    comparison_formula = (
        f"${target_paid:,.2f} - ${peer_paid:,.2f} = ${visit_payer_spend_difference:,.2f}"
        if target_paid >= peer_paid
        else (
            f"${target_paid:,.2f} is not more than ${peer_paid:,.2f}, "
            f"so the possible difference is ${0:,.2f}"
        )
    )
    billed_comparison_formula = (
        f"${target_billed:,.2f} - ${peer_billed:,.2f} = ${visit_billed_difference:,.2f}"
        if target_billed >= peer_billed
        else (
            f"${target_billed:,.2f} is not more than ${peer_billed:,.2f}, "
            f"so the billed difference is ${0:,.2f}"
        )
    )
    peer_comparison_records = []
    seen_peer_claim_ids = {peer_claim_payload["claim_id"]}
    peer_source_rows = (
        [r for ep in peers if ep["member_id"] == peer_episode["member_id"] for r in ep["rows"]]
        if peers
        else peer_episode["rows"]
    )
    for row in sorted(
        peer_source_rows,
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")) or date.min, _claim_id(row)),
        reverse=True,
    ):
        record = _procedure_comparison_claim(row)
        if record["claim_id"] in seen_peer_claim_ids:
            continue
        seen_peer_claim_ids.add(record["claim_id"])
        peer_comparison_records.append(record)

    reason_str = fallback_reason or (
        f"This different-member peer episode was selected from Scenario {scenario_number} because it is the closest "
        "recorded match within the eligible payer-savings cohort."
    )
    match_problem_str = (
        f"Same organ system ({fallback_match_type.split('(')[-1].rstrip(')')})"
        if "organ" in fallback_match_type
        else _problem_match_label(selected_claim, peer_claim)
    )
    match_checks = {
        "diagnosis_code": {
            "label": "Diagnosis code",
            "target": target_claim["icd10"],
            "peer": peer_claim_payload["icd10"],
            "matches": bool(target_claim["icd10"] and target_claim["icd10"] == peer_claim_payload["icd10"]),
        },
        "diagnosis_family": {
            "label": "Diagnosis family",
            "target": target_claim["icd10_family"],
            "peer": peer_claim_payload["icd10_family"],
            "matches": target_claim["icd10_family"] == peer_claim_payload["icd10_family"],
        },
        "procedure_code": {
            "label": "Procedure code",
            "target": target_claim["cpt"],
            "peer": peer_claim_payload["cpt"],
            "matches": bool(target_claim["cpt"] and target_claim["cpt"] == peer_claim_payload["cpt"]),
        },
        "units": {
            "label": "Units",
            "target": target_claim["units"],
            "peer": peer_claim_payload["units"],
            "matches": target_claim["units"] == peer_claim_payload["units"],
        },
        "payer": {
            "label": "Payer",
            "target": target_claim["payer_id"],
            "peer": peer_claim_payload["payer_id"],
            "matches": bool(target_claim["payer_id"] and target_claim["payer_id"] == peer_claim_payload["payer_id"]),
        },
        "provider": {
            "label": "Billing provider",
            "target": target_claim["provider_npi"],
            "peer": peer_claim_payload["provider_npi"],
            "matches": bool(target_claim["provider_npi"] and target_claim["provider_npi"] == peer_claim_payload["provider_npi"]),
        },
        "place_of_service": {
            "label": "Place of service",
            "target": target_claim["pos"],
            "peer": peer_claim_payload["pos"],
            "matches": bool(target_claim["pos"] and target_claim["pos"] == peer_claim_payload["pos"]),
        },
    }
    exact_comparison_match = all(check["matches"] for check in match_checks.values())

    return {
        "available": True,
        "history_window_days": PAYER_COHORT_EPISODE_DAYS,
        "reason": reason_str,
        "target": target_context,
        "peer": {
            "episode_id": peer_episode["episode_id"],
            "claim_count": peer_episode["claim_count"],
            "claim": peer_claim_payload,
            "prior_services": peer_prior_services,
            "prior_services_total_paid": _money(sum(row["paid_amount"] for row in peer_prior_services)),
            "comparison_records": peer_comparison_records[:10],
        },
        "scenario_number": scenario_number or 3,
        "comparison_prediction": {
            "basis": "Paid_Amount",
            "target_visit_paid": target_paid,
            "peer_visit_paid": peer_paid,
            "possible_payer_spend_difference": visit_payer_spend_difference,
            "confirmed_savings": 0.0,
            "formula": comparison_formula,
            "source_rows": [
                {
                    "role": "This person's visit",
                    "claim_id": target_claim["claim_id"],
                    "field": "Paid_Amount",
                    "value": target_paid,
                },
                {
                    "role": "Other person's matching visit",
                    "claim_id": peer_claim_payload["claim_id"],
                    "field": "Paid_Amount",
                    "value": peer_paid,
                },
            ],
            "reason": (
                "This amount is the positive difference between the payer-paid amounts on the two displayed visits. "
                "It is a comparison amount for review, not confirmed savings."
            ),
        },
        "billed_comparison": {
            "basis": "Charge_Amount",
            "target_visit_billed": target_billed,
            "peer_visit_billed": peer_billed,
            "possible_billed_difference": visit_billed_difference,
            "confirmed_savings": 0.0,
            "formula": billed_comparison_formula,
            "reason": "This is the positive difference between the recorded billed charges on the two displayed visits. It is a comparison amount for review, not confirmed savings.",
            "selection_reason": reason_str,
            "selection_audit": selection_audit,
            "evidence_strength": "strong" if exact_comparison_match else "limited",
            "supports_billed_difference": True,
            "supports_savings": False,
            "limitation": (
                "All required comparison fields match. Clinical and contract review is still required before treating the difference as savings."
                if exact_comparison_match
                else "This is not an accurate comparison: one or more diagnosis, procedure, units, payer, provider, or place-of-service fields differ. The source rows prove the billed amounts only; do not use this as a savings result."
            ),
            "match_checks": list(match_checks.values()),
            "source_rows": [
                {
                    "role": "This person's visit",
                    "claim_id": target_claim["claim_id"],
                    "member_id": target_claim["member_id"],
                    "service_date": target_claim["service_date"],
                    "diagnosis_code": target_claim["icd10"],
                    "procedure_code": target_claim["cpt"],
                    "procedure_description": target_claim["procedure_description"],
                    "units": target_claim["units"],
                    "payer_id": target_claim["payer_id"],
                    "provider_npi": target_claim["provider_npi"],
                    "place_of_service": target_claim["pos"],
                    "historical_reference": target_claim["is_historical_reference"],
                    "field": "Charge_Amount",
                    "value": target_billed,
                },
                {
                    "role": "Other person's matching visit",
                    "claim_id": peer_claim_payload["claim_id"],
                    "member_id": peer_claim_payload["member_id"],
                    "service_date": peer_claim_payload["service_date"],
                    "diagnosis_code": peer_claim_payload["icd10"],
                    "procedure_code": peer_claim_payload["cpt"],
                    "procedure_description": peer_claim_payload["procedure_description"],
                    "units": peer_claim_payload["units"],
                    "payer_id": peer_claim_payload["payer_id"],
                    "provider_npi": peer_claim_payload["provider_npi"],
                    "place_of_service": peer_claim_payload["pos"],
                    "historical_reference": peer_claim_payload["is_historical_reference"],
                    "field": "Charge_Amount",
                    "value": peer_billed,
                },
            ],
            "source": database.source_banner() if hasattr(database, "source_banner") else {},
        },
        "matches": {
            "problem": match_problem_str,
            "procedure": _procedure_match_label(selected_claim, peer_claim),
            "payer": "Same payer" if target.get("selected_identity", {}).get("payer_id") and target.get("selected_identity", {}).get("payer_id") == peer_claim_payload["payer_id"] else "Different payer",
            "provider": "Same provider" if target.get("selected_identity", {}).get("provider") and target.get("selected_identity", {}).get("provider") == peer_claim_payload["provider_npi"] else "Different provider",
            "place_of_service": "Same place of service" if target.get("selected_identity", {}).get("pos") and target.get("selected_identity", {}).get("pos") == peer_claim_payload["pos"] else "Different place of service",
            "units": "Similar units" if _similar_units(target.get("selected_identity", {}).get("units", 1), peer_claim_payload["units"]) else "Different units",
        },
    }


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
    if not selected_claim or _is_historical_reference(selected_claim):
        raise KeyError(f"Selectable claim not found: {claim_number}")
    selected_claim_id = _claim_id(selected_claim)
    selected_member_id = _member_id(selected_claim)
    selected_family = _family(selected_claim)
    selected_date = _day(_field(selected_claim, "Service_Date_From", "dos"))
    if not selected_member_id or not selected_family or not selected_date:
        raise ValueError("The selected claim is missing Member_ID, ICD10_Family, or Service_Date_From.")

    cached_target = _target_episode_index(database).get(selected_claim_id)
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
    target_payload = {
        "claim_id": selected_claim_id,
        "member_id": selected_member_id,
        "diagnosis_family": selected_family,
        "comparison_episode_id": target["episode_id"],
        "episode_start": target["start_date"],
        "episode_end": target["end_date"],
        "claim_count": target["claim_count"],
        "payer_spend": target["total_paid"],
        "total_paid": target["total_paid"],
        "median_paid_per_claim": target["median_paid"],
        "duration_days": target["duration_days"],
        "claim_ids": [_claim_id(row) for row in target["rows"]],
    }

    # Peer records are constrained to the point when the selected claim occurred.
    # Historical-reference rows are allowed as peer evidence, but never as target rows.
    peer_episodes = _historically_available_peer_episodes(database, target)
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
        no_cohort_reason = (
            "No different-member peer episode met any of the three allowed comparison rules at this claim's "
            "service date. No payer-savings amount was calculated."
        )
        procedure_comparison = _build_procedure_comparison(
            database,
            target,
            selected_claim,
            [],
            0,
        )
        scenario_selection["selected"] = {
            "number": 0,
            "name": "No cross-member prediction",
            "reason": no_cohort_reason,
            "peer_member_count": 0,
            "peer_episode_count": 0,
            "peer_claim_count": 0,
        }
        return {
            "available": False,
            "reason": no_cohort_reason,
            "target": target_payload,
            "scenario_selection": scenario_selection,
            "peer_summary": {"member_count": 0, "episode_count": 0, "claim_count": 0},
            "peer_members_used": [],
            "procedure_comparison": procedure_comparison,
            "calculation_summary": {"available": False, "reason": no_cohort_reason},
            "supporting_evidence": [_evidence_row(row, "Target Episode") for row in target["rows"]],
            "calculation_trace": {
                "target_claim_id": selected_claim_id,
                "target_member_id": selected_member_id,
                "icd10_family": selected_family,
                "comparison_episode_id": target["episode_id"],
                "prediction_cutoff": selected_date.isoformat(),
                "scenario_used": 0,
                "scenario_name": "No cross-member prediction",
                "target_claim_count": target["claim_count"],
                "target_total_paid": target["total_paid"],
                "peer_member_count": 0,
                "peer_episode_count": 0,
                "peer_claim_count": 0,
            },
        }

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
    peer_episode_spends = [peer["total_paid"] for peer in ordered_peers]
    typical_peer_spend = _money(median(peer_episode_spends))
    benchmark_claim_count = round(float(median(peer["claim_count"] for peer in lower_utilisation_peers)), 1)
    q25_paid_per_claim, peer_median_paid_per_claim, q75_paid_per_claim = [
        _money(value) for value in np.percentile(all_peer_paid, [25, 50, 75])
    ]
    excess_claim_count = max(target["claim_count"] - benchmark_claim_count, 0)
    utilisation_opportunity = _money(excess_claim_count * peer_median_paid_per_claim)
    payer_spend_opportunity = (
        _money(max(target["total_paid"] - lower_spend_benchmark, 0))
        if lower_spend_benchmark is not None
        else 0.0
    )
    predicted = _money(min(target["total_paid"], max(utilisation_opportunity, payer_spend_opportunity)))
    prediction_range = _prediction_range(
        target["total_paid"],
        predicted,
        peer_episode_spends,
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
        "peer_episode_ids": [peer["episode_id"] for peer in lower_spend_peers],
        "member_ids": sorted({peer["member_id"] for peer in lower_spend_peers}),
        "claim_ids": [_claim_id(row) for peer in lower_spend_peers for row in peer["rows"]],
        "method": lower_spend_method,
    }
    utilisation_detail = {
        "value": benchmark_claim_count,
        "peer_episode_ids": [peer["episode_id"] for peer in lower_utilisation_peers],
        "member_ids": sorted({peer["member_id"] for peer in lower_utilisation_peers}),
        "claim_ids": [_claim_id(row) for peer in lower_utilisation_peers for row in peer["rows"]],
        "method": lower_utilisation_method,
    }
    selected_claim_paid = _money(_number(_field(selected_claim, "Paid_Amount", "paid")))
    selected_claim_share = (
        selected_claim_paid / target["total_paid"]
        if target["total_paid"] > 0
        else 0.0
    )
    claim_attributed_saving = _money(predicted * selected_claim_share)
    procedure_comparison = _build_procedure_comparison(
        database,
        target,
        selected_claim,
        ordered_peers,
        scenario_number,
    )
    return {
        "available": True,
        "target": target_payload,
        "scenario_selection": scenario_selection,
        "peer_summary": {
            "member_count": len(unique_peer_member_ids),
            "episode_count": len(ordered_peers),
            "claim_count": sum(peer["claim_count"] for peer in ordered_peers),
        },
        "benchmark_summary": {
            "target_claim_count": target["claim_count"],
            "target_payer_spend": target["total_paid"],
            "target_median_paid_per_claim": target["median_paid"],
            "target_episode_duration_days": target["duration_days"],
            "typical_peer_spend": typical_peer_spend,
            "utilisation_benchmark_claim_count": benchmark_claim_count,
            "median_peer_paid_per_claim": peer_median_paid_per_claim,
            "lower_spend_benchmark": lower_spend_benchmark,
            "benchmark_label": benchmark_label,
            "benchmark_method": benchmark_method,
            "peer_member_count": len(unique_peer_member_ids),
            "peer_episode_count": len(ordered_peers),
            "peer_claim_count": sum(peer["claim_count"] for peer in ordered_peers),
            "utilisation_benchmark": utilisation_detail,
            "lower_spend_benchmark_detail": lower_spend_detail,
        },
        "utilisation_benchmark": {
            "claim_count": benchmark_claim_count,
            "median_peer_paid_per_claim": peer_median_paid_per_claim,
            "method": lower_utilisation_method,
            "peer_episode_ids": utilisation_detail["peer_episode_ids"],
            "member_ids": utilisation_detail["member_ids"],
            "claim_ids": utilisation_detail["claim_ids"],
        },
        "lower_spend_benchmark": lower_spend_detail,
        "selected_claim": {
            "claim_id": selected_claim_id,
            "paid_amount": selected_claim_paid,
            "episode_spend_share": round(selected_claim_share, 6),
            "attributed_payer_avoidable_spend": claim_attributed_saving,
        },
        "peer_members_used": peer_members,
        "procedure_comparison": procedure_comparison,
        "calculation_summary": {
            "excess_claim_count": excess_claim_count,
            "q25_peer_paid_per_claim": q25_paid_per_claim,
            "median_peer_paid_per_claim": peer_median_paid_per_claim,
            "q75_peer_paid_per_claim": q75_paid_per_claim,
            "typical_peer_spend": typical_peer_spend,
            "utilisation_reduction_opportunity": utilisation_opportunity,
            "lower_spend_benchmark": lower_spend_benchmark,
            "payer_spend_reduction_opportunity": payer_spend_opportunity,
            "predicted_payer_avoidable_spend": predicted,
            "episode_predicted_payer_avoidable_spend": predicted,
            "claim_attributed_payer_avoidable_spend": claim_attributed_saving,
            "range": prediction_range,
            "range_label": "Benchmark-Based Estimate Range",
            "confidence": confidence,
            "zero_reason": zero_reason,
            "formula_trace": [
                {"name": "utilisation", "formula": "excess_claim_count × median_peer_paid_per_claim", "value": utilisation_opportunity},
                {"name": "payer_spend", "formula": "max(target_episode_payer_spend − lower_spend_opportunity_benchmark, 0)", "value": payer_spend_opportunity},
                {"name": "final", "formula": "min(target_episode_payer_spend, max(utilisation_opportunity, payer_spend_opportunity))", "value": predicted},
            ],
        },
        "supporting_evidence": evidence,
        "calculation_trace": {
            "target_claim_id": selected_claim_id,
            "target_member_id": selected_member_id,
            "icd10_family": selected_family,
            "comparison_episode_id": target["episode_id"],
            "prediction_cutoff": selected_date.isoformat(),
            "scenario_used": scenario_number,
            "scenario_name": scenario_names[scenario_number],
            "target_claim_count": target["claim_count"],
            "target_total_paid": target["total_paid"],
            "peer_member_count": len(unique_peer_member_ids),
            "peer_episode_count": len(ordered_peers),
            "peer_claim_count": sum(peer["claim_count"] for peer in ordered_peers),
            "typical_peer_spend": typical_peer_spend,
            "utilisation_benchmark_claim_count": benchmark_claim_count,
            "excess_claim_count": excess_claim_count,
            "median_peer_paid_per_claim": peer_median_paid_per_claim,
            "utilisation_reduction_opportunity": utilisation_opportunity,
            "lower_spend_benchmark": lower_spend_benchmark,
            "lower_spend_benchmark_method": lower_spend_method,
            "payer_spend_reduction_opportunity": payer_spend_opportunity,
            "predicted_payer_avoidable_spend": predicted,
            "claim_attributed_payer_avoidable_spend": claim_attributed_saving,
            "range": prediction_range,
            "confidence": confidence,
        },
    }


def _episode_anchor_claim_id(episode):
    anchor = min(
        episode["rows"],
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")), _claim_id(row)),
    )
    return _claim_id(anchor)


def run_payer_temporal_backtest(database):
    """Validate the live three-scenario Paid_Amount rule at historical cutoffs.

    Each selectable claim is treated as a historical prediction point.  The
    authoritative engine rebuilds the peer cohort at that claim's service date,
    so every benchmark row can be checked for future-data leakage.
    """
    targets = sorted(
        database.selectable_claims,
        key=lambda row: (_day(_field(row, "Service_Date_From", "dos")) or date.min, _claim_id(row)),
    )
    scenario_counts = {1: 0, 2: 0, 3: 0}
    errors = []
    evaluated_spend_pairs = []
    confidence_errors = defaultdict(list)
    leakage_found = False
    results = 0

    for claim in targets:
        try:
            prediction = build_payer_prediction_for_claim(database, _claim_id(claim))
        except (KeyError, ValueError):
            continue
        if not prediction["available"]:
            continue
        results += 1
        trace = prediction["calculation_trace"]
        scenario_counts[trace["scenario_used"]] += 1
        cutoff = _day(trace["prediction_cutoff"])
        peer_dates = [
            _day(row["service_date"])
            for row in prediction["supporting_evidence"]
            if row["evidence_role"] != "Target Episode"
        ]
        if any(peer_date and peer_date > cutoff for peer_date in peer_dates):
            leakage_found = True

        # The neutral typical-peer benchmark is the prospective payer-spend
        # estimate.  The lower-spend benchmark remains the opportunity value.
        actual = trace["target_total_paid"]
        estimated = trace["typical_peer_spend"]
        error = abs(actual - estimated)
        errors.append(error)
        evaluated_spend_pairs.append((actual, estimated))
        confidence_errors[trace["confidence"]["level"]].append(error)

    if not errors:
        return {
            "validated": False,
            "method": "HISTORICAL_THREE_SCENARIO_PAID_AMOUNT_BACKTEST",
            "targets_tested": 0,
            "prediction_coverage": 0.0,
            "future_data_leakage_found": leakage_found,
            "note": "No selectable claim had a valid historical different-member cohort.",
        }

    percentage_errors = [
        abs(actual - estimated) / actual
        for actual, estimated in evaluated_spend_pairs
        if actual > 0
    ]

    return {
        "validated": not leakage_found,
        "method": "HISTORICAL_THREE_SCENARIO_PAID_AMOUNT_BACKTEST",
        "targets_tested": results,
        "scenario_1_test_count": scenario_counts[1],
        "scenario_2_test_count": scenario_counts[2],
        "scenario_3_test_count": scenario_counts[3],
        "mean_absolute_error": _money(float(np.mean(errors))),
        "median_absolute_error": _money(float(np.median(errors))),
        "mean_absolute_percentage_error": round(float(np.mean(percentage_errors)) * 100, 2) if percentage_errors else None,
        "prediction_coverage": round(results / len(targets), 4) if targets else 0.0,
        "confidence_stratified_error": {
            level: _money(float(np.mean(values)))
            for level, values in sorted(confidence_errors.items())
        },
        "future_data_leakage_found": leakage_found,
        "note": "Typical peer spend is evaluated against the known target episode spend; opportunity values remain deterministic payer-spend benchmarks.",
    }


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
        if not result["available"]:
            unavailable_episodes.append({
                "comparison_episode_id": episode["episode_id"],
                "claim_ids": [_claim_id(row) for row in episode["rows"]],
                "reason": result["reason"],
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
            "confidence": result["calculation_summary"]["confidence"],
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
        "high_medium_confidence_predicted_savings": _money(sum(
            item["predicted_payer_avoidable_spend"]
            for item in episode_results
            if item["confidence"]["level"] in {"High", "Medium"}
        )),
        "low_confidence_predicted_savings": _money(sum(
            item["predicted_payer_avoidable_spend"]
            for item in episode_results
            if item["confidence"]["level"] == "Low"
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
        "high_medium_confidence_predicted_savings": _money(sum(
            item["high_medium_confidence_predicted_savings"] for item in member_summaries
        )),
        "low_confidence_predicted_savings": _money(sum(
            item["low_confidence_predicted_savings"] for item in member_summaries
        )),
    }
    _PORTFOLIO_PAYER_SUMMARY_CACHE[cache_key] = summary
    return summary
