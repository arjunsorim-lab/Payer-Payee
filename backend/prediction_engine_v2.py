"""Payer historical-savings V2 engine (implementation of the plan).

Identifies a target member's condition-specific claim history, compares it against
historically comparable members over an equivalent observation window, and reports a
potential historical savings opportunity with claim-level evidence.

This is decision support for STATISTICAL utilization / cost deviation. It never
classifies a claim as medically unnecessary solely because it is more expensive or
more frequent than historical peers.

Implemented plan dimensions
- Phase 3  Cohort Engine with a Level 1/2/3 data-sufficiency hierarchy
- Phase 4  Timeline & Episode Engine with configurable observation windows
- Phase 5  CPT Utilization Engine
- Phase 6  Historical Benchmark Engine (P25 / median / average / P75)
- Phase 7  Configurable Similarity & Evidence Engine
- Phase 8  Savings Opportunity Engine with claim/CPT attribution
- Phase 9  Member-level prediction entry point
- Phase 11 Temporal backtest validation hook
"""

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import median

OBSERVATION_WINDOWS = (7, 30, 90, 180, 365)


def _observation_window():
    """Return a supported observation window (7/30/90/180/365), default 90 for MVP."""
    raw = os.getenv("PAYER_OBSERVATION_WINDOW_DAYS", "90").strip()
    try:
        value = int(raw)
        return value if value in OBSERVATION_WINDOWS else 90
    except (TypeError, ValueError):
        return 90


DEFAULT_SIMILARITY_WEIGHTS = {
    "icd10_weight": 0.30,
    "cpt_weight": 0.25,
    "timeline_weight": 0.15,
    "observation_weight": 0.10,
    "payer_plan_weight": 0.10,
    "provider_specialty_weight": 0.10,
}


def _similarity_weights():
    """Configurable similarity weights, optionally overridden by PAYER_SIMILARITY_CONFIG JSON."""
    base = dict(DEFAULT_SIMILARITY_WEIGHTS)
    raw = os.getenv("PAYER_SIMILARITY_CONFIG", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            for key in base:
                value = parsed.get(key)
                if isinstance(value, (int, float)):
                    base[key] = float(value)
        except (TypeError, ValueError):
            pass
    total = sum(base.values())
    if total <= 0:
        base = dict(DEFAULT_SIMILARITY_WEIGHTS)
        total = sum(base.values())
    return {key: value / total for key, value in base.items()}


def _min_cohort_size():
    try:
        return max(int(os.getenv("PAYER_MIN_COHORT_SIZE", "10")), 1)
    except (TypeError, ValueError):
        return 10


# Level 3 related clinical condition groups (ICD-10 family prefix -> group).
CLINICAL_CONDITION_GROUPS = {
    "E08": "DIABETES", "E09": "DIABETES", "E10": "DIABETES", "E11": "DIABETES", "E13": "DIABETES",
    "I10": "HYPERTENSION", "I11": "HYPERTENSION", "I12": "HYPERTENSION",
    "I13": "HYPERTENSION", "I15": "HYPERTENSION", "I16": "HYPERTENSION",
    "J44": "RESPIRATORY", "J45": "RESPIRATORY", "J20": "RESPIRATORY",
    "K21": "GASTRO", "K29": "GASTRO", "K31": "GASTRO", "K35": "GASTRO",
    "M05": "MUSCULOSKELETAL", "M06": "MUSCULOSKELETAL", "M25": "MUSCULOSKELETAL",
    "M48": "MUSCULOSKELETAL", "M51": "MUSCULOSKELETAL", "M54": "MUSCULOSKELETAL",
    "N18": "RENAL", "N19": "RENAL", "N20": "RENAL", "N32": "RENAL",
    "F32": "MENTAL_HEALTH", "F33": "MENTAL_HEALTH", "F41": "MENTAL_HEALTH",
    "E78": "LIPID",
}


def _icd_family(value):
    if not value:
        return ""
    return str(value or "").split(".")[0][:3]


def _condition_group(value):
    fam = _icd_family(value)
    if not fam:
        return ""
    group = CLINICAL_CONDITION_GROUPS.get(fam)
    if group:
        return group
    # Comprehensive chapter mapping based on ICD-10 standard taxonomy
    prefix = fam[0].upper() if fam else ""
    if prefix in ("A", "B"):
        return "INFECTIOUS"
    if prefix in ("C", "D") and fam[:2] in ("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "D0", "D1", "D2", "D3", "D4"):
        return "ONCOLOGY"
    if prefix == "E":
        if fam.startswith(("E00", "E01", "E02", "E03", "E04", "E05", "E06", "E07")):
            return "THYROID"
        return "METABOLIC"
    if prefix == "F":
        return "MENTAL_HEALTH"
    if prefix == "G":
        return "NEUROLOGY"
    if prefix == "H":
        return "SENSORY"
    if prefix == "I":
        return "CARDIOVASCULAR"
    if prefix == "J":
        return "RESPIRATORY"
    if prefix == "K":
        return "GASTRO"
    if prefix == "L":
        return "DERMATOLOGY"
    if prefix == "M":
        return "MUSCULOSKELETAL"
    if prefix == "N":
        return "RENAL"
    if prefix == "Z":
        return "PREVENTIVE"
    return "GENERAL"


def _day(value):
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = "{0}-{1}-{2}".format(text[:4], text[4:6], text[6:])
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _field(claim, name, canonical=None, default=""):
    value = claim.get("workbookFields", {}).get(name)
    if value not in (None, ""):
        return value
    return claim.get(canonical or name, default)


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value):
    return round(_number(value), 2)


def _is_historical(value):
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1", "1.0"}


def _percentile(values, proportion):
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * proportion
    if index.is_integer():
        return float(ordered[int(index)])
    lower, upper = int(index), int(index) + 1
    return float(ordered[lower] * (upper - index) + ordered[upper] * (index - lower))


def _service_category(cpt):
    digits = "".join(character for character in str(cpt or "") if character.isdigit())
    if not digits:
        return "SERVICE"
    first = digits[0]
    if first == "9":
        return "OFFICE_VISIT"
    if first == "8":
        return "LAB"
    if first == "7":
        return "IMAGING"
    if first in ("0", "1"):
        return "PROCEDURE"
    return "SERVICE"


def _find_claim(database, claim_number):
    if hasattr(database, "find_claim"):
        found = database.find_claim(claim_number, selectable_only=False)
        if found:
            return found
    target = "".join(character for character in str(claim_number or "").upper() if character.isalnum())
    for claim in getattr(database, "claims", ()) or ():
        key = "".join(character for character in str(_field(claim, "Claim_ID", "claimId") or "").upper() if character.isalnum())
        if key == target:
            return claim
    raise KeyError("Claim not found: {0}".format(claim_number))


def _median_gap_days(claims):
    dates = []
    for claim in claims:
        parsed = _day(_field(claim, "Service_Date_From", "dos"))
        if parsed:
            dates.append(parsed)
    dates.sort()
    gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    return median(gaps) if gaps else 0.0


def similarity_score(target_claims, member_claims, weights=None):
    """Weighted Phase 7 similarity between a target claim window and a peer window."""
    weights = weights or _similarity_weights()
    target_cpts = set(str(_field(c, "CPT_Code", "cptCode")) for c in target_claims)
    member_cpts = set(str(_field(c, "CPT_Code", "cptCode")) for c in member_claims)
    union = target_cpts | member_cpts
    cpt_sim = len(target_cpts & member_cpts) / len(union) if union else 0.0

    target_icd = _field(target_claims[0], "ICD10_Diagnosis_Code", "diagnosisCode") or ""
    member_icd = _field(member_claims[0], "ICD10_Diagnosis_Code", "diagnosisCode") or ""
    if target_icd and target_icd == member_icd:
        icd_sim = 1.0
    elif target_icd and _icd_family(target_icd) == _icd_family(member_icd):
        icd_sim = 0.7
    elif _condition_group(target_icd) and _condition_group(target_icd) == _condition_group(member_icd):
        icd_sim = 0.4
    else:
        icd_sim = 0.0

    target_gap = _median_gap_days(target_claims)
    member_gap = _median_gap_days(member_claims)
    if target_gap > 0:
        timeline_sim = max(0.0, 1.0 - abs(target_gap - member_gap) / max(target_gap, 1.0))
    elif member_gap == 0:
        timeline_sim = 1.0
    else:
        timeline_sim = 0.5

    count_sim = max(0.0, 1.0 - abs(len(target_claims) - len(member_claims)) / max(len(target_claims), 1))

    target_payer = _field(target_claims[0], "Payer_Name", "payer")
    member_payer = _field(member_claims[0], "Payer_Name", "payer")
    target_plan = _field(target_claims[0], "Claim_Filing_Indicator", "filingIndicator")
    member_plan = _field(member_claims[0], "Claim_Filing_Indicator", "filingIndicator")
    payer_sim = 1.0 if (target_payer and target_payer == member_payer) else (0.5 if (target_plan and target_plan == member_plan) else 0.0)

    target_provider = _field(target_claims[0], "Billing_Provider_NPI", "billingProviderNpi")
    member_provider = _field(member_claims[0], "Billing_Provider_NPI", "billingProviderNpi")
    target_pos = _field(target_claims[0], "Place_of_Service_Code", "placeOfServiceCode")
    member_pos = _field(member_claims[0], "Place_of_Service_Code", "placeOfServiceCode")
    provider_sim = 1.0 if (target_provider and target_provider == member_provider) else (0.5 if (target_pos and target_pos == member_pos) else 0.0)

    weighted = (
        weights["icd10_weight"] * icd_sim
        + weights["cpt_weight"] * cpt_sim
        + weights["timeline_weight"] * timeline_sim
        + weights["observation_weight"] * count_sim
        + weights["payer_plan_weight"] * payer_sim
        + weights["provider_specialty_weight"] * provider_sim
    )
    reasons = []
    if icd_sim >= 0.7:
        reasons.append("matched ICD-10")
    elif icd_sim == 0.4:
        reasons.append("matched clinical condition group")
    if cpt_sim:
        reasons.append("shared CPT mix")
    if payer_sim:
        reasons.append("same payer or plan")
    if provider_sim:
        reasons.append("similar provider setting")
    return round(100 * weighted, 1), reasons


def build_timeline(claims):
    """Phase 4 timeline with service dates, intervals and rolling utilization."""
    ordered = sorted(claims, key=lambda c: _day(_field(c, "Service_Date_From", "dos")) or date.min)
    events = []
    prior_dates = []
    previous = None
    for claim in ordered:
        service_date = _day(_field(claim, "Service_Date_From", "dos"))
        if not service_date:
            continue
        cpt = _field(claim, "CPT_Code", "cptCode")
        category = _service_category(cpt)
        events.append({
            "date": str(service_date),
            "cpt": cpt,
            "description": _field(claim, "CPT_Description", "cptDescription") or category,
            "category": category,
            "allowed": _money(_field(claim, "Allowed_Amount", "allowed")),
            "days_since_prior": None if previous is None else (service_date - previous).days,
            "prior_7d_claims": sum(1 for prior in prior_dates if service_date > prior and (service_date - prior).days <= 7),
            "prior_30d_claims": sum(1 for prior in prior_dates if service_date > prior and (service_date - prior).days <= 30),
            "prior_90d_claims": sum(1 for prior in prior_dates if service_date > prior and (service_date - prior).days <= 90),
        })
        prior_dates.append(service_date)
        previous = service_date
    return events


def build_care_pattern(claims):
    """Phase 5 condition-specific care journey derived from CPT categories."""
    ordered = sorted(claims, key=lambda c: _day(_field(c, "Service_Date_From", "dos")) or date.min)
    steps = [_service_category(_field(c, "CPT_Code", "cptCode")) for c in ordered]
    return {
        "steps": steps,
        "distinct_services": sorted(set(steps)),
        "journey": " -> ".join(steps) if steps else "",
    }


def build_savings_attribution(target_claims, cohort_members):
    """Phase 8 claim-level and CPT-level contribution to the opportunity estimate."""
    cpt_cost_history = defaultdict(list)
    for claims in cohort_members.values():
        for claim in claims:
            cpt_cost_history[str(_field(claim, "CPT_Code", "cptCode"))].append(
                _money(_field(claim, "Allowed_Amount", "allowed"))
            )
    cpt_median_cost = {
        cpt: median(values) if values else 0.0 for cpt, values in cpt_cost_history.items()
    }
    target_cpt_counts = Counter(str(_field(c, "CPT_Code", "cptCode")) for c in target_claims)

    cpt_contributions = []
    for cpt, count in target_cpt_counts.items():
        target_cost = round(
            sum(
                _money(_field(c, "Allowed_Amount", "allowed"))
                for c in target_claims
                if str(_field(c, "CPT_Code", "cptCode")) == cpt
            ),
            2,
        )
        median_per_claim = round(cpt_median_cost.get(cpt, 0.0), 2)
        contribution = round(max(0.0, target_cost - median_per_claim * count), 2)
        cpt_contributions.append({
            "cpt": cpt,
            "target_cost": target_cost,
            "historical_median_per_claim": median_per_claim,
            "target_frequency": count,
            "contribution": contribution,
        })
    cpt_contributions.sort(key=lambda item: item["contribution"], reverse=True)

    claim_contributions = []
    for claim in target_claims:
        cpt = str(_field(claim, "CPT_Code", "cptCode"))
        allowed = _money(_field(claim, "Allowed_Amount", "allowed"))
        claim_contributions.append({
            "claim_id": _field(claim, "Claim_ID", "claimId"),
            "cpt": cpt,
            "service_date": str(_day(_field(claim, "Service_Date_From", "dos"))),
            "allowed": allowed,
            "contribution": round(max(0.0, allowed - cpt_median_cost.get(cpt, 0.0)), 2),
        })
    claim_contributions.sort(key=lambda item: item["contribution"], reverse=True)
    return cpt_contributions, claim_contributions


def _build_cohort_candidates(database, target_member_id, target_start_date):
    """Group eligible claims of other members into per-member lists."""
    candidates = defaultdict(list)
    for claim in getattr(database, "claims", ()) or ():
        member_id = _field(claim, "Member_ID", "memberId")
        if member_id == target_member_id:
            continue
        is_hist = _is_historical(_field(claim, "Is_Historical_Reference")) or _field(claim, "isHistorical")
        service_date = _day(_field(claim, "Service_Date_From", "dos"))
        if is_hist or (service_date and service_date < target_start_date):
            candidates[member_id].append(claim)
    if len(candidates) < _min_cohort_size():
        for claim in getattr(database, "claims", ()) or ():
            member_id = _field(claim, "Member_ID", "memberId")
            if member_id != target_member_id and claim not in candidates[member_id]:
                candidates[member_id].append(claim)
    return candidates


def _window_claims(claims, observation_days):
    """Restrict a member's claims to the equivalent window from first service date."""
    ordered = sorted(claims, key=lambda c: _day(_field(c, "Service_Date_From", "dos")) or date.min)
    if not ordered:
        return []
    start = _day(_field(ordered[0], "Service_Date_From", "dos")) or date.min
    end = start + timedelta(days=observation_days)
    windowed = [c for c in ordered if start <= (_day(_field(c, "Service_Date_From", "dos")) or date.min) <= end]
    return windowed or ordered


def _filter_cohort_members(candidates, predicate, observation_days):
    cohort = {}
    for member_id, claims in candidates.items():
        matching = [c for c in claims if predicate(c)]
        if not matching:
            continue
        window = _window_claims(matching, observation_days)
        if window:
            cohort[member_id] = window
    return cohort


def _select_cohort(database, target_member_id, exact_icd10, target_start_date, observation_days, min_size):
    """Level 1 -> 2 -> 3 -> 4 data-sufficiency cohort hierarchy."""
    candidates = _build_cohort_candidates(database, target_member_id, target_start_date)
    if not candidates:
        raise ValueError("No different-member historical cohort available for this condition.")
    icd_family = _icd_family(exact_icd10)
    icd_group = _condition_group(exact_icd10)

    exact = lambda c: _field(c, "ICD10_Diagnosis_Code", "diagnosisCode") == exact_icd10
    family = lambda c: bool(_icd_family(_field(c, "ICD10_Diagnosis_Code", "diagnosisCode"))) and _icd_family(_field(c, "ICD10_Diagnosis_Code", "diagnosisCode")) == icd_family
    group = lambda c: bool(icd_group) and _condition_group(_field(c, "ICD10_Diagnosis_Code", "diagnosisCode")) == icd_group
    any_diag = lambda c: True

    cohort = _filter_cohort_members(candidates, exact, observation_days)
    level = "EXACT_ICD10"
    if len(cohort) < min_size:
        cohort = _filter_cohort_members(candidates, family, observation_days)
        level = "ICD10_FAMILY"
    if len(cohort) < min_size:
        cohort = _filter_cohort_members(candidates, group, observation_days)
        level = "CONDITION_GROUP"
    if len(cohort) < min_size:
        cohort = _filter_cohort_members(candidates, any_diag, observation_days)
        level = "POPULATION_BENCHMARK"
    if not cohort:
        for member_id, claims in candidates.items():
            window = _window_claims(claims, observation_days)
            if window:
                cohort[member_id] = window
        level = "ALL_MEMBERS"
    if not cohort:
        raise ValueError("No different-member historical cohort available for this condition.")
    return cohort, level, candidates


def _coefficient_of_variation(values):
    if not values:
        return 0.0
    mean_value = sum(values) / len(values)
    if mean_value == 0:
        return 0.0
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return (variance ** 0.5) / mean_value


def _build_payer_prediction(database, member_id, exact_icd10, anchor_claim_number, observation_days):
    icd_family = _icd_family(exact_icd10)
    icd_group = _condition_group(exact_icd10)

    target_claims = [
        c for c in getattr(database, "claims", ()) or ()
        if _field(c, "Member_ID", "memberId") == member_id
        and _field(c, "ICD10_Diagnosis_Code", "diagnosisCode") == exact_icd10
    ]
    if not target_claims:
        target_claims = [
            c for c in getattr(database, "claims", ()) or ()
            if _field(c, "Member_ID", "memberId") == member_id
        ]
    target_claims.sort(key=lambda c: _day(_field(c, "Service_Date_From", "dos")) or date.min)
    if not target_claims:
        raise ValueError("No claims found for target member.")

    target_start = _day(_field(target_claims[0], "Service_Date_From", "dos")) or date.min
    target_end = target_start + timedelta(days=observation_days)
    target_window = [
        c for c in target_claims
        if target_start <= (_day(_field(c, "Service_Date_From", "dos")) or date.min) <= target_end
    ]
    if not target_window:
        target_window = target_claims

    t_allowed = sum(_money(_field(c, "Allowed_Amount", "allowed")) for c in target_window)
    t_paid = sum(_money(_field(c, "Paid_Amount", "paid")) for c in target_window)
    t_cpt_counts = Counter(str(_field(c, "CPT_Code", "cptCode")) for c in target_window)

    min_size = _min_cohort_size()
    cohort, level, candidates = _select_cohort(database, member_id, exact_icd10, target_start, observation_days, min_size)

    member_claim_counts = []
    member_allowed_amounts = []
    member_paid_amounts = []
    for claims in cohort.values():
        member_claim_counts.append(len(claims))
        member_allowed_amounts.append(sum(_money(_field(c, "Allowed_Amount", "allowed")) for c in claims))
        member_paid_amounts.append(sum(_money(_field(c, "Paid_Amount", "paid")) for c in claims))

    med_claims = median(member_claim_counts) if member_claim_counts else 0
    med_allowed = median(member_allowed_amounts) if member_allowed_amounts else 0.0
    med_paid = median(member_paid_amounts) if member_paid_amounts else 0.0
    avg_allowed = sum(member_allowed_amounts) / len(member_allowed_amounts) if member_allowed_amounts else 0.0
    p25_allowed = _percentile(member_allowed_amounts, 0.25)
    p75_allowed = _percentile(member_allowed_amounts, 0.75)

    potential_savings = max(0.0, t_allowed - med_allowed)
    p25_opportunity = max(0.0, t_allowed - p25_allowed)
    avg_opportunity = max(0.0, t_allowed - avg_allowed)
    excess_claims = max(0, len(target_window) - med_claims)

    weights = _similarity_weights()
    evidence = []
    for member_id_key, claims in cohort.items():
        similarity, reasons = similarity_score(target_window, claims, weights)
        evidence.append({
            "historical_member_id": member_id_key,
            "condition_match": _field(claims[0], "ICD10_Diagnosis_Code", "diagnosisCode"),
            "claim_count": len(claims),
            "historical_cost": round(sum(_money(_field(c, "Allowed_Amount", "allowed")) for c in claims), 2),
            "similarity_score": similarity,
            "similarity_reasons": reasons,
            "relevant_claims": [
                {
                    "claim_id": _field(c, "Claim_ID", "claimId"),
                    "cpt": _field(c, "CPT_Code", "cptCode"),
                    "date": str(_day(_field(c, "Service_Date_From", "dos"))),
                    "allowed": _money(_field(c, "Allowed_Amount", "allowed")),
                }
                for c in claims
            ],
        })
    evidence.sort(key=lambda item: (-item["similarity_score"], item["historical_cost"]))
    evidence = evidence[:8]

    cpt_analysis = []
    for cpt, count in t_cpt_counts.items():
        cohort_cpt_counts = [
            sum(1 for c in claims if _field(c, "CPT_Code", "cptCode") == cpt)
            for claims in cohort.values()
        ]
        historical_frequency = median(cohort_cpt_counts) if cohort_cpt_counts else 0
        cpt_analysis.append({
            "cpt": cpt,
            "target_frequency": count,
            "historical_frequency": historical_frequency,
            "difference": round(count - historical_frequency, 1),
            "target_cost": round(sum(
                _money(_field(c, "Allowed_Amount", "allowed"))
                for c in target_window if _field(c, "CPT_Code", "cptCode") == cpt
            ), 2),
        })

    timeline = build_timeline(target_window)
    care_pattern = build_care_pattern(target_window)
    cpt_contributions, claim_contributions = build_savings_attribution(target_window, cohort)

    quality = "HIGH"
    if len(cohort) < min_size:
        quality = "LOW"
    elif len(cohort) < 20 or level != "EXACT_ICD10":
        quality = "MEDIUM"

    validation = {
        "method": "TEMPORAL_HOLDOUT_OFFLINE",
        "observation_window_days": observation_days,
        "cohort_members": len(cohort),
        "benchmark_stability": round(_coefficient_of_variation(member_allowed_amounts), 3) if len(member_allowed_amounts) > 1 else 0.0,
        "similarity_config": weights,
        "note": (
            "Statistical deviation only. This is a potential historical savings "
            "opportunity and does not determine clinical medical necessity."
        ),
    }

    delta_pct = round(((t_allowed - med_allowed) / med_allowed * 100), 1) if med_allowed > 0 else 0.0
    cond_desc = _field(target_window[0], "ICD10_Diagnosis_Description", "diagnosisDescription") or exact_icd10

    forecast = {
        "predicted_total_allowed": round(med_allowed, 2),
        "predicted_total_paid": round(med_paid, 2) if med_paid else round(med_allowed * 0.82, 2),
        "current_total_allowed": round(t_allowed, 2),
        "current_total_paid": round(t_paid, 2),
        "delta_percent": delta_pct,
        "delta_direction": "higher" if delta_pct >= 0 else "lower",
        "potential_savings": round(potential_savings, 2),
        "benchmark_allowed_range": f"${round(p25_allowed, 2):,.2f} – ${round(p75_allowed, 2):,.2f}",
        "confidence_level": quality,
        "confidence_score": 0.85 if quality == "HIGH" else (0.6 if quality == "MEDIUM" else 0.40),
        "peers_used": len(cohort),
        "selection_level": level,
    }

    key_insights = [
        {
            "icon": "activity",
            "tone": "green" if delta_pct <= 0 else "orange",
            "text": f"{cond_desc} costs are {abs(delta_pct):.1f}% {'higher than' if delta_pct > 0 else 'below'} peer median ({med_allowed:,.2f})",
            "bold": f"{abs(delta_pct):.1f}%",
        },
        {
            "icon": "dollar",
            "tone": "purple",
            "text": f"${potential_savings:,.2f} in total historical optimization opportunity",
            "bold": f"${potential_savings:,.2f}",
        },
        {
            "icon": "bell",
            "tone": "blue",
            "text": f"Utilization: {len(target_window)} claim(s) vs. peer median of {med_claims} claim(s)",
            "bold": f"{len(target_window)} vs {med_claims}",
        },
    ]
    if cpt_analysis:
        top_cpt = max(cpt_analysis, key=lambda x: x.get("target_cost", 0))
        key_insights.append({
            "icon": "file-text",
            "tone": "teal",
            "text": f"Top CPT code {top_cpt['cpt']} accounts for ${top_cpt['target_cost']:,.2f} ({top_cpt['target_frequency']} claim(s))",
            "bold": f"${top_cpt['target_cost']:,.2f}",
        })

    benchmark_summary = {
        "utilisation_benchmark_claim_count": med_claims,
        "median_allowed_amount": round(med_allowed, 2),
        "p25_allowed_amount": round(p25_allowed, 2),
        "p75_allowed_amount": round(p75_allowed, 2),
        "average_allowed_amount": round(avg_allowed, 2),
        "selection_level": level,
        "peer_count": len(cohort),
    }

    return {
        "member": {
            "member_id": member_id,
            "condition": {
                "icd10": exact_icd10,
                "icd10_family": icd_family,
                "condition_group": icd_group,
                "description": cond_desc,
            },
        },
        "observation": {
            "start_date": str(target_start),
            "end_date": str(target_end),
            "days": observation_days,
        },
        "target": {
            "claim_count": len(target_window),
            "allowed_amount": round(t_allowed, 2),
            "paid_amount": round(t_paid, 2),
        },
        "forecast": forecast,
        "key_insights": key_insights,
        "benchmark_summary": benchmark_summary,
        "historical_cohort": {
            "selection_level": level,
            "members": len(cohort),
            "median_claim_count": med_claims,
            "median_allowed_amount": round(med_allowed, 2),
            "p25_allowed_amount": round(p25_allowed, 2),
            "average_allowed_amount": round(avg_allowed, 2),
            "p75_allowed_amount": round(p75_allowed, 2),
        },
        "utilization": {
            "target_claims": len(target_window),
            "benchmark_claims": med_claims,
            "excess_claims": excess_claims,
        },
        "cpt_analysis": cpt_analysis,
        "timeline": timeline,
        "care_pattern": care_pattern,
        "potential_savings": {
            "benchmark_method": "MEDIAN",
            "current_amount": round(t_allowed, 2),
            "p25_benchmark": round(p25_allowed, 2),
            "median_benchmark": round(med_allowed, 2),
            "average_benchmark": round(avg_allowed, 2),
            "p75_benchmark": round(p75_allowed, 2),
            "median_opportunity": round(potential_savings, 2),
            "p25_opportunity": round(p25_opportunity, 2),
            "average_opportunity": round(avg_opportunity, 2),
            "cpt_level_attribution": cpt_contributions,
            "claim_level_attribution": claim_contributions,
        },
        "cohort_quality": {
            "members_found": len(candidates),
            "members_used": len(cohort),
            "selection_level": level,
            "minimum_required": min_size,
            "quality": quality,
        },
        "evidence": evidence,
        "validation": validation,
        "confidence": {
            "score": 0.85 if quality == "HIGH" else (0.6 if quality == "MEDIUM" else 0.40),
            "level": quality,
        },
    }


def build_payer_prediction_for_claim_v2(database, claim_number, observation_days=None):
    """Public claim-anchored entry point (kept for the claim endpoint)."""
    observation = observation_days or _observation_window()
    selected = _find_claim(database, claim_number)
    member_id = _field(selected, "Member_ID", "memberId")
    exact_icd10 = _field(selected, "ICD10_Diagnosis_Code", "diagnosisCode")
    return _build_payer_prediction(database, member_id, exact_icd10, claim_number, observation)


def build_member_payer_prediction(database, member_id, observation_days=None):
    """Phase 9 member-level entry point using the member's dominant diagnosis."""
    observation = observation_days or _observation_window()
    counts = Counter()
    for claim in database.claims:
        if _field(claim, "Member_ID", "memberId") == member_id:
            code = _field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")
            if code:
                counts[code] += 1
    if not counts:
        raise ValueError("No condition-specific claims found for target member.")
    exact_icd10 = counts.most_common(1)[0][0]
    anchor = None
    for claim in database.claims:
        if (
            _field(claim, "Member_ID", "memberId") == member_id
            and _field(claim, "ICD10_Diagnosis_Code", "diagnosisCode") == exact_icd10
        ):
            anchor = _field(claim, "Claim_ID", "claimId")
            break
    return _build_payer_prediction(database, member_id, exact_icd10, anchor, observation)


def run_payer_temporal_backtest(database, window_days=None):
    """Phase 11 temporal holdout backtest over the available dated claims.

    Splits each member's claim history at a reference (median) date and checks how
    well the member's own early per-claim cost predicts later total spend. The
    reported accuracy / precision is a statistical reference for benchmark stability,
    not a clinical necessity statement.
    """
    window_days = window_days or _observation_window()
    dated = []
    for claim in database.claims:
        if _day(_field(claim, "Service_Date_From", "dos")):
            dated.append(claim)
    if not dated:
        return {"validated": False, "method": "TEMPORAL_HOLDOUT", "sample_members": 0,
            "mean_absolute_error": None, "mean_absolute_percentage_error": None,
            "savings_prediction_accuracy": None, "note": "no dated claims"}

    cutoff = sorted(
        _day(_field(c, "Service_Date_From", "dos")) for c in dated
    )[len(dated) // 2]

    early_claims = defaultdict(list)
    late_claims = defaultdict(list)
    for claim in dated:
        service_date = _day(_field(claim, "Service_Date_From", "dos")) or date.max
        target = early_claims if service_date < cutoff else late_claims
        target[_field(claim, "Member_ID", "memberId")].append(claim)

    predictions = []
    for member_id in set(early_claims) | set(late_claims):
        early = early_claims.get(member_id, [])
        late = late_claims.get(member_id, [])
        if len(early) < 2 or not late:
            continue
        early_costs = [_money(_field(c, "Allowed_Amount", "allowed")) for c in early]
        median_per_claim = median(early_costs)
        actual_total = sum(_money(_field(c, "Allowed_Amount", "allowed")) for c in late)
        predicted_total = median_per_claim * len(late)
        predictions.append((member_id, actual_total, predicted_total))

    if len(predictions) < 2:
        return {"validated": False, "method": "TEMPORAL_HOLDOUT", "sample_members": len(predictions),
            "mean_absolute_error": None, "mean_absolute_percentage_error": None,
            "savings_prediction_accuracy": None, "note": "insufficient historical span for backtest"}

    errors = [abs(actual - predicted) for _, actual, predicted in predictions]
    mae = sum(errors) / len(errors)
    mape = sum(
        min(1.0, abs(actual - predicted) / max(abs(actual), 1.0))
        for _, actual, predicted in predictions
    ) / len(predictions)

    return {
        "validated": True,
        "method": "TEMPORAL_HOLDOUT",
        "window_days": window_days,
        "sample_members": len(predictions),
        "mean_absolute_error": round(mae, 2),
        "mean_absolute_percentage_error": round(mape, 3),
        "savings_prediction_accuracy": round(max(0.0, 1.0 - mape), 3),
        "note": "Same-member temporal split reference; not a clinical necessity statement.",
    }