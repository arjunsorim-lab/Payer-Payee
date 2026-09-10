"""Cross-patient episode comparison engine.

Compares claims from two different patients sharing the same organ system
and ICD-10 diagnosis family, calculates the payer spend difference, and
surfaces a categorised breakdown of divergent services.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re


# ── ICD-10 chapter → organ system mapping ────────────────────────────────
# Each entry maps an ICD-10 family prefix letter+digits range to a human-
# readable organ-system label and emoji badge.

_ORGAN_SYSTEMS = [
    # Genitourinary (kidneys, bladder, urinary tract)
    ("N", "Kidneys & Urinary Tract", "💧"),
    # Respiratory
    ("J", "Lungs & Airways", "🫁"),
    # Circulatory
    ("I", "Heart & Blood Vessels", "❤️"),
    # Endocrine / metabolic
    ("E", "Endocrine & Metabolic", "🔬"),
    # Digestive
    ("K", "Digestive System", "🍽️"),
    # Musculoskeletal
    ("M", "Bones & Joints", "🦴"),
    # Nervous system
    ("G", "Brain & Nerves", "🧠"),
    # Skin
    ("L", "Skin", "🩹"),
    # Mental / behavioural
    ("F", "Mental Health", "🧘"),
    # Neoplasms
    ("C", "Cancer / Neoplasms", "🎗️"),
    ("D0", "Cancer / Neoplasms", "🎗️"),
    ("D1", "Cancer / Neoplasms", "🎗️"),
    ("D2", "Cancer / Neoplasms", "🎗️"),
    ("D3", "Cancer / Neoplasms", "🎗️"),
    ("D4", "Haematological", "🩸"),
    ("D5", "Haematological", "🩸"),
    ("D6", "Haematological", "🩸"),
    ("D7", "Haematological", "🩸"),
    ("D8", "Haematological", "🩸"),
    # Blood / immune
    ("D", "Haematological", "🩸"),
    # Eye
    ("H0", "Eye", "👁️"),
    ("H1", "Eye", "👁️"),
    ("H2", "Eye", "👁️"),
    ("H3", "Eye", "👁️"),
    ("H4", "Eye", "👁️"),
    ("H5", "Eye", "👁️"),
    # Ear
    ("H6", "Ear", "👂"),
    ("H7", "Ear", "👂"),
    ("H8", "Ear", "👂"),
    ("H9", "Ear", "👂"),
    # Infectious
    ("A", "Infectious Disease", "🦠"),
    ("B", "Infectious Disease", "🦠"),
    # Pregnancy / perinatal
    ("O", "Pregnancy & Childbirth", "🤰"),
    ("P", "Perinatal", "👶"),
    # Congenital
    ("Q", "Congenital Conditions", "🧬"),
    # Symptoms / signs / abnormal findings
    ("R", "Symptoms & Signs", "🩺"),
    # Injury / external causes
    ("S", "Injury", "🚑"),
    ("T", "Injury & Poisoning", "🚑"),
    ("V", "External Causes", "⚠️"),
    ("W", "External Causes", "⚠️"),
    ("X", "External Causes", "⚠️"),
    ("Y", "External Causes", "⚠️"),
    # Health services / factors
    ("Z", "Health Services", "🏥"),
]


def organ_for_family(icd10_family: str) -> tuple[str, str]:
    """Return (organ_system_label, emoji) for an ICD-10 family code."""
    family = (icd10_family or "").strip().upper()
    if not family:
        return ("Unknown", "❓")
    # Try longest-prefix first (e.g. "D0" before "D")
    for prefix, label, emoji in _ORGAN_SYSTEMS:
        if family.startswith(prefix):
            return (label, emoji)
    return ("Other", "❓")


# ── Helpers (reuse patterns from payer_prediction / value_based_case) ────

def _text(value):
    return "" if value is None else str(value).strip()


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _field(claim, name, canonical=None, default=""):
    value = claim.get("workbookFields", {}).get(name)
    if value not in (None, ""):
        return value
    return claim.get(canonical or name, default)


def _claim_id(claim):
    return _text(_field(claim, "Claim_ID", "claimId"))


def _member_id(claim):
    return _text(_field(claim, "Member_ID", "memberId"))


def _family(claim):
    explicit = _text(_field(claim, "ICD10_Family"))
    diagnosis = _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode"))
    return explicit or diagnosis.split(".")[0][:3]


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


def _is_historical_reference(claim):
    return _text(_field(claim, "Is_Historical_Reference_Record")).upper() in {
        "Y", "YES", "TRUE", "1",
    }


def _claim_summary(claim):
    """Build a lightweight claim summary dict."""
    service_date = _day(_field(claim, "Service_Date_From", "dos"))
    return {
        "claim_id": _claim_id(claim),
        "member_id": _member_id(claim),
        "service_date": service_date.isoformat() if service_date else "",
        "icd10": _text(_field(claim, "ICD10_Diagnosis_Code", "diagnosisCode")),
        "icd10_family": _family(claim),
        "diagnosis_description": _text(_field(claim, "ICD10_Diagnosis_Description", "diagnosisDescription")),
        "cpt": _text(_field(claim, "CPT_Code", "cptCode")),
        "procedure_description": _text(_field(claim, "CPT_Description", "cptDescription")),
        "paid_amount": _number(_field(claim, "Paid_Amount", "paid")),
        "billed_amount": _number(_field(claim, "Charge_Amount", "totalCharge")),
        "allowed_amount": _number(_field(claim, "Allowed_Amount", "allowed")),
        "units": _number(_field(claim, "Units", "units")),
    }


# ── Episode builder ──────────────────────────────────────────────────────

EPISODE_WINDOW_DAYS = 90


def _build_episodes(claims, window_days=EPISODE_WINDOW_DAYS):
    """Group claims into (member_id, diagnosis_family) episodes split by gaps."""
    grouped = defaultdict(list)
    for claim in claims:
        mid = _member_id(claim)
        fam = _family(claim)
        sdate = _day(_field(claim, "Service_Date_From", "dos"))
        if mid and fam and sdate:
            grouped[(mid, fam)].append(claim)

    episodes = []
    for (mid, fam), rows in grouped.items():
        ordered = sorted(rows, key=lambda r: (_day(_field(r, "Service_Date_From", "dos")) or date.min, _claim_id(r)))
        current = []
        prev_date = None
        for row in ordered:
            row_date = _day(_field(row, "Service_Date_From", "dos"))
            if current and prev_date and (row_date - prev_date).days > window_days:
                episodes.append(_make_episode(current, mid, fam))
                current = []
            current.append(row)
            prev_date = row_date
        if current:
            episodes.append(_make_episode(current, mid, fam))
    return episodes


def _make_episode(rows, member_id, family):
    summaries = [_claim_summary(r) for r in rows]
    dates = [s["service_date"] for s in summaries if s["service_date"]]
    total_paid = sum(s["paid_amount"] for s in summaries)
    total_billed = sum(s["billed_amount"] for s in summaries)
    total_allowed = sum(s["allowed_amount"] for s in summaries)

    # Categorise services
    cpt_breakdown = defaultdict(lambda: {"count": 0, "total_paid": 0.0, "total_billed": 0.0, "description": ""})
    for s in summaries:
        cpt = s["cpt"] or "unknown"
        cpt_breakdown[cpt]["count"] += 1
        cpt_breakdown[cpt]["total_paid"] += s["paid_amount"]
        cpt_breakdown[cpt]["total_billed"] += s["billed_amount"]
        if not cpt_breakdown[cpt]["description"]:
            cpt_breakdown[cpt]["description"] = s["procedure_description"]

    organ, emoji = organ_for_family(family)
    return {
        "member_id": member_id,
        "diagnosis_family": family,
        "diagnosis_description": summaries[0]["diagnosis_description"] if summaries else "",
        "organ_system": organ,
        "organ_emoji": emoji,
        "start_date": min(dates) if dates else "",
        "end_date": max(dates) if dates else "",
        "claim_count": len(rows),
        "total_paid": _money(total_paid),
        "total_billed": _money(total_billed),
        "total_allowed": _money(total_allowed),
        "claims": summaries,
        "cpt_breakdown": dict(cpt_breakdown),
    }


# ── Comparable pairs discovery ───────────────────────────────────────────

def discover_comparable_pairs(database):
    """Find all pairs of patients that share a diagnosis family.

    Returns a structure grouped by organ system and then by disease family,
    with each family listing the members that have episodes for it.
    """
    claims = [c for c in database.selectable_claims if not _is_historical_reference(c)]
    episodes = _build_episodes(claims)

    # Group episodes by family
    family_members = defaultdict(lambda: defaultdict(list))
    family_descriptions = {}
    for ep in episodes:
        fam = ep["diagnosis_family"]
        mid = ep["member_id"]
        family_members[fam][mid].append(ep)
        if fam not in family_descriptions:
            family_descriptions[fam] = ep["diagnosis_description"]

    # Build response grouped by organ system
    organ_groups = defaultdict(list)
    for fam, member_map in sorted(family_members.items()):
        if len(member_map) < 2:
            continue  # Need at least 2 patients
        organ, emoji = organ_for_family(fam)
        members = []
        for mid, member_episodes in sorted(member_map.items()):
            # Find patient name from claims
            patient_name = ""
            for ep in member_episodes:
                for c in ep["claims"]:
                    if not patient_name:
                        # Try to get from original claims
                        for orig in claims:
                            if _claim_id(orig) == c["claim_id"]:
                                patient_name = _text(orig.get("patient", ""))
                                break
                    if patient_name:
                        break
            members.append({
                "member_id": mid,
                "patient_name": patient_name or mid,
                "episode_count": len(member_episodes),
                "total_paid": _money(sum(ep["total_paid"] for ep in member_episodes)),
                "total_billed": _money(sum(ep["total_billed"] for ep in member_episodes)),
                "claim_count": sum(ep["claim_count"] for ep in member_episodes),
            })
        organ_groups[organ].append({
            "diagnosis_family": fam,
            "diagnosis_description": family_descriptions.get(fam, ""),
            "organ_system": organ,
            "organ_emoji": emoji,
            "member_count": len(members),
            "members": members,
        })

    result = []
    for organ_name, diseases in sorted(organ_groups.items()):
        emoji = diseases[0]["organ_emoji"] if diseases else "❓"
        result.append({
            "organ_system": organ_name,
            "organ_emoji": emoji,
            "disease_families": diseases,
            "total_diseases": len(diseases),
            "total_members": len(set(m["member_id"] for d in diseases for m in d["members"])),
        })
    return {"organ_groups": result, "source": database.source_banner()}


# ── Two-patient comparison ───────────────────────────────────────────────

def compare_patients(database, member_id_1, member_id_2, diagnosis_family):
    """Compare episodes of two different patients sharing the same disease.

    Returns a detailed comparison with per-service breakdown and savings.
    """
    if member_id_1 == member_id_2:
        raise ValueError("The two patients must be different members.")

    claims = [c for c in database.selectable_claims if not _is_historical_reference(c)]
    episodes = _build_episodes(claims)

    # Find episodes for each member in this disease family
    eps_1 = [ep for ep in episodes if ep["member_id"] == member_id_1 and ep["diagnosis_family"] == diagnosis_family]
    eps_2 = [ep for ep in episodes if ep["member_id"] == member_id_2 and ep["diagnosis_family"] == diagnosis_family]

    if not eps_1:
        raise ValueError(f"No episodes found for member {member_id_1} with diagnosis family {diagnosis_family}")
    if not eps_2:
        raise ValueError(f"No episodes found for member {member_id_2} with diagnosis family {diagnosis_family}")

    # Use the most expensive episode for each patient (most clinically significant)
    ep1 = max(eps_1, key=lambda e: e["total_billed"])
    ep2 = max(eps_2, key=lambda e: e["total_billed"])

    # Determine which is the higher-cost patient
    if ep1["total_billed"] >= ep2["total_billed"]:
        higher, lower = ep1, ep2
    else:
        higher, lower = ep2, ep1

    total_diff = _money(higher["total_billed"] - lower["total_billed"])
    pct_savings = round((total_diff / higher["total_billed"]) * 100, 1) if higher["total_billed"] > 0 else 0.0

    # Build CPT-level divergence analysis
    all_cpts = set(list(higher["cpt_breakdown"].keys()) + list(lower["cpt_breakdown"].keys()))
    divergence = []
    for cpt in sorted(all_cpts):
        h = higher["cpt_breakdown"].get(cpt, {"count": 0, "total_paid": 0.0, "total_billed": 0.0, "description": ""})
        l = lower["cpt_breakdown"].get(cpt, {"count": 0, "total_paid": 0.0, "total_billed": 0.0, "description": ""})
        diff = _money(h["total_billed"] - l["total_billed"])
        divergence.append({
            "cpt": cpt,
            "description": h["description"] or l["description"],
            "higher_cost_patient": {
                "member_id": higher["member_id"],
                "count": h["count"],
                "total_billed": _money(h["total_billed"]),
            },
            "lower_cost_patient": {
                "member_id": lower["member_id"],
                "count": l["count"],
                "total_billed": _money(l["total_billed"]),
            },
            "difference": diff,
            "category": "additional" if l["count"] == 0 else "excess" if h["count"] > l["count"] else "price_variation",
        })
    divergence.sort(key=lambda d: -abs(d["difference"]))

    # Patient names
    p1_name = _find_patient_name(claims, member_id_1)
    p2_name = _find_patient_name(claims, member_id_2)

    organ, emoji = organ_for_family(diagnosis_family)

    return {
        "available": True,
        "diagnosis_family": diagnosis_family,
        "diagnosis_description": ep1["diagnosis_description"] or ep2["diagnosis_description"],
        "organ_system": organ,
        "organ_emoji": emoji,
        "higher_cost_patient": {
            "member_id": higher["member_id"],
            "patient_name": _find_patient_name(claims, higher["member_id"]),
            "episode": _episode_summary(higher),
        },
        "lower_cost_patient": {
            "member_id": lower["member_id"],
            "patient_name": _find_patient_name(claims, lower["member_id"]),
            "episode": _episode_summary(lower),
        },
        "savings_summary": {
            "total_savings": total_diff,
            "savings_percentage": pct_savings,
            "higher_total": _money(higher["total_billed"]),
            "lower_total": _money(lower["total_billed"]),
            "metric": "billed_amount",
            "claim_count_difference": higher["claim_count"] - lower["claim_count"],
        },
        "service_divergence": divergence,
        "clinical_review_required": True,
        "disclaimer": "This comparison shows claims-based cost differences between two patients with the same condition. Differences may reflect clinical necessity, severity variation, or care pathway choices. Clinical review is required before attributing savings.",
        "source": database.source_banner(),
    }


_UTI_CULTURE_CPTS = {"81001", "87086", "99000"}


def _is_uti_culture_line(claim_summary):
    """Identify the culture/specimen bundle without treating unrelated lines as add-ons."""
    cpt = _text(claim_summary.get("cpt"))
    description = _text(claim_summary.get("procedure_description")).lower()
    return cpt in _UTI_CULTURE_CPTS or any(
        phrase in description
        for phrase in ("urine culture", "culture urine", "urinalysis", "urine specimen collection")
    )


def build_same_patient_billed_intervention_savings(database, member_id, diagnosis_family):
    """Build the workbook-required same-patient UTI culture counterfactual.

    The result compares the observed billed cost of two episodes with a hypothetical
    single earlier episode plus culture/specimen lines priced at their observed billed
    amounts in the later episode. It deliberately does not claim clinical causation.
    """
    family = _text(diagnosis_family).upper()
    if not family.startswith(("N30", "N39")):
        return {
            "available": False,
            "member_id": member_id,
            "diagnosis_family": family,
            "reason": "The supplied requirement is specific to UTI/cystitis episodes (N30 or N39).",
        }

    claims = [c for c in database.selectable_claims if not _is_historical_reference(c)]
    episodes = sorted(
        (
            episode for episode in _build_episodes(claims)
            if episode["member_id"] == member_id and episode["diagnosis_family"] == family
        ),
        key=lambda episode: (episode["start_date"], episode["end_date"]),
    )

    selected_pair = None
    for later_index in range(1, len(episodes)):
        later = episodes[later_index]
        add_on_lines = [line for line in later["claims"] if _is_uti_culture_line(line)]
        if not add_on_lines:
            continue
        for earlier in reversed(episodes[:later_index]):
            if any(_is_uti_culture_line(line) for line in earlier["claims"]):
                continue
            selected_pair = (earlier, later, add_on_lines)
            break
        if selected_pair:
            break

    if not selected_pair:
        return {
            "available": False,
            "member_id": member_id,
            "diagnosis_family": family,
            "reason": "No earlier UTI episode without culture followed by a later UTI episode with recorded culture/specimen billed lines was found.",
        }

    earlier, later, add_on_lines = selected_pair
    earlier_billed = _money(earlier["total_billed"])
    later_billed = _money(later["total_billed"])
    add_on_billed = _money(sum(line["billed_amount"] for line in add_on_lines))
    actual_billed = _money(earlier_billed + later_billed)
    proposed_billed = _money(earlier_billed + add_on_billed)
    billed_difference = _money(actual_billed - proposed_billed)

    earlier_end = _day(earlier["end_date"])
    later_start = _day(later["start_date"])
    days_between = (later_start - earlier_end).days if earlier_end and later_start else None

    return {
        "available": True,
        "member_id": member_id,
        "diagnosis_family": family,
        "calculation_basis": "billed_amount",
        "earlier_episode": _episode_summary(earlier),
        "later_episode": _episode_summary(later),
        "days_between_episodes": days_between,
        "culture_add_on_lines": add_on_lines,
        "calculation": {
            "earlier_episode_actual_billed": earlier_billed,
            "later_episode_actual_billed": later_billed,
            "culture_and_specimen_add_on_billed": add_on_billed,
            "actual_two_episode_billed": actual_billed,
            "proposed_earlier_episode_with_add_on_billed": proposed_billed,
            "potential_billed_difference": billed_difference,
            "formula": "actual two-episode billed amount - proposed earlier episode plus observed culture/specimen billed amount",
        },
        "includes_all_later_episode_lines": True,
        "clinical_review_required": True,
        "disclaimer": "This is a billed-charge counterfactual for review. It does not establish that the later episode would have been prevented or that the billed difference is confirmed savings.",
    }


def _find_patient_name(claims, member_id):
    for c in claims:
        if _member_id(c) == member_id:
            name = _text(c.get("patient", ""))
            if name:
                return name
    return member_id


def _episode_summary(episode):
    return {
        "start_date": episode["start_date"],
        "end_date": episode["end_date"],
        "claim_count": episode["claim_count"],
        "total_paid": episode["total_paid"],
        "total_billed": episode["total_billed"],
        "total_allowed": episode["total_allowed"],
        "claims": episode["claims"],
        "cpt_breakdown": [
            {"cpt": cpt, **info}
            for cpt, info in sorted(episode["cpt_breakdown"].items(), key=lambda x: -x[1]["total_billed"])
        ],
    }
