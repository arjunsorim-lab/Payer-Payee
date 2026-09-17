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


def _boolean(value):
    text = _text(value).upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


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


def _is_template_coverage_demo(claim):
    """Identify synthetic support rows reserved for the same-patient demo."""
    return _text(_field(claim, "UI_Detail_Reason")).lower() == "demo-only template-coverage episode"


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
        "intervention_performed": _boolean(_field(claim, "Intervention_Performed")),
        "reason_code": _text(_field(claim, "Reason_Code")),
        "episode_duration_days": _number(_field(claim, "Episode_Duration_Days")),
        "synthetic_demo": _text(_field(claim, "Reason_Code")).upper() == "SYNTHETIC_PRESENTATION_CASE",
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
        "synthetic_demo": any(s["synthetic_demo"] for s in summaries),
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
    # The selector is an evidence directory: include every member with this
    # disease family, including historical-reference records. The response
    # identifies those rows so reviewers can distinguish reference evidence.
    claims = [claim for claim in database.claims if not _is_template_coverage_demo(claim)]
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
                "historical_reference_claim_count": sum(
                    int(_is_historical_reference(original))
                    for original in claims
                    if _member_id(original) == mid and _family(original) == fam
                ),
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

    claims = [claim for claim in database.claims if not _is_template_coverage_demo(claim)]
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
        "disclaimer": "This comparison shows claims-based billed-cost differences between two patients with the same condition. Historical-reference claims may be included when they are the only available evidence. Differences may reflect clinical necessity, severity variation, or care pathway choices. Clinical review is required before attributing savings.",
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


def _is_template_intervention_line(line, earlier_lines):
    """Require an explicitly marked, distinct later intervention service."""
    cpt = _text(line.get("cpt"))
    description = _text(line.get("procedure_description")).lower()
    earlier_keys = {(_text(item.get("cpt")), _text(item.get("procedure_description")).lower()) for item in earlier_lines}
    if (cpt, description) in earlier_keys:
        return False
    return bool(cpt or description) and line.get("intervention_performed") is True


def build_same_patient_billed_intervention_savings(database, member_id, diagnosis_family, anchor_claim_id=""):
    """Build the workbook-required same-patient UTI culture counterfactual.

    The result compares the observed billed cost of two episodes with a hypothetical
    single earlier episode plus culture/specimen lines priced at their observed billed
    amounts in the later episode. It deliberately does not claim clinical causation.
    """
    family = _text(diagnosis_family).upper()
    if not family:
        return {"available": False, "member_id": member_id, "diagnosis_family": family, "reason": "No diagnosis family was supplied."}

    source_claims = getattr(database, "claims", database.selectable_claims)
    claims = [
        claim for claim in source_claims
        if not _is_historical_reference(claim) or _is_template_coverage_demo(claim)
    ]
    # The workbook treats each encounter date as one episode. Do not chain
    # several visits together through the broader 90-day comparison window.
    episodes = sorted(
        (
            episode for episode in _build_episodes(claims, window_days=0)
            if episode["member_id"] == member_id and episode["diagnosis_family"] == family
        ),
        key=lambda episode: (episode["start_date"], episode["end_date"]),
    )

    normalized_anchor = _text(anchor_claim_id).replace("-", "")
    anchor_episode_indexes = {
        index for index, episode in enumerate(episodes)
        if any(_text(line.get("claim_id")).replace("-", "") == normalized_anchor for line in episode["claims"])
    } if normalized_anchor else set()

    selected_pair = None
    for later_index in range(1, len(episodes)):
        if anchor_episode_indexes and later_index not in anchor_episode_indexes:
            continue
        later = episodes[later_index]
        anchor_line = next(
            (line for line in later["claims"] if _text(line.get("claim_id")).replace("-", "") == normalized_anchor),
            later["claims"][0] if later["claims"] else {},
        )
        gynecological_journey = (
            family == "N92"
            and _text(anchor_line.get("reason_code")).upper() == "SYNTHETIC_GYNECOLOGICAL_PREVENTIVE_OUTCOME_REFERENCE"
        )
        earlier_candidates = episodes[:later_index] if gynecological_journey else reversed(episodes[:later_index])
        for earlier in earlier_candidates:
            if family.startswith(("N30", "N39")):
                add_on_lines = [line for line in later["claims"] if _is_uti_culture_line(line)]
                if any(_is_uti_culture_line(line) for line in earlier["claims"]):
                    continue
            else:
                add_on_lines = [line for line in later["claims"] if _is_template_intervention_line(line, earlier["claims"])]
                if any(_is_template_intervention_line(line, []) for line in earlier["claims"]):
                    continue
            if not add_on_lines:
                continue
            selected_pair = (earlier, later, add_on_lines, later_index)
            break
        if selected_pair:
            break

    if not selected_pair:
        return {
            "available": False,
            "member_id": member_id,
            "diagnosis_family": family,
            "anchor_claim_id": anchor_claim_id or None,
            "reason": "No earlier related episode without an intervention followed by a later related episode with a separately identifiable billed intervention line was found in this member's data.",
        }

    earlier, later, add_on_lines, later_index = selected_pair
    earlier_index = episodes.index(earlier)
    intervening_episodes = episodes[earlier_index + 1:later_index]
    intervening_billed = _money(sum(episode["total_billed"] for episode in intervening_episodes))
    journey_mode = bool(intervening_episodes)
    earlier_billed = _money(earlier["total_billed"])
    later_billed = _money(later["total_billed"])
    add_on_billed = _money(sum(line["billed_amount"] for line in add_on_lines))
    actual_billed = _money(earlier_billed + intervening_billed + later_billed)
    proposed_billed = _money(earlier_billed + add_on_billed)
    billed_difference = _money(actual_billed - proposed_billed)

    earlier_end = _day(earlier["end_date"])
    later_start = _day(later["start_date"])
    days_between = (later_start - earlier_end).days if earlier_end and later_start else None
    first_intervening_start = _day(intervening_episodes[0]["start_date"]) if intervening_episodes else None
    days_to_first_intervening = (
        (first_intervening_start - earlier_end).days
        if earlier_end and first_intervening_start else None
    )
    anchor_line = next(
        (line for line in later["claims"] if _text(line.get("claim_id")).replace("-", "") == normalized_anchor),
        later["claims"][0] if later["claims"] else {},
    )
    earlier_line = earlier["claims"][-1] if earlier["claims"] else {}
    exact_diagnosis_match = bool(
        _text(anchor_line.get("icd10"))
        and _text(anchor_line.get("icd10")) == _text(earlier_line.get("icd10"))
    )
    same_procedure = bool(
        _text(anchor_line.get("cpt"))
        and _text(anchor_line.get("cpt")) == _text(earlier_line.get("cpt"))
    )
    candidate_visits = []
    ranked_candidates = episodes[:later_index] if journey_mode else list(reversed(episodes[:later_index]))
    for rank, candidate in enumerate(ranked_candidates, start=1):
        candidate_line = candidate["claims"][-1] if candidate["claims"] else {}
        selected = candidate is earlier
        candidate_visits.append({
            "rank": rank,
            "claim_id": candidate_line.get("claim_id"),
            "service_date": candidate.get("end_date"),
            "diagnosis_code": candidate_line.get("icd10"),
            "procedure_code": candidate_line.get("cpt"),
            "units": candidate_line.get("units"),
            "billed_amount": _money(candidate.get("total_billed")),
            "selected": selected,
            "decision": (
                "Selected: first symptomatic visit in the documented progression journey."
                if selected and journey_mode else
                "Selected: closest earlier visit meeting the same-member and diagnosis-family rule."
                if selected else
                "Included as a subsequent visit in the documented progression journey."
                if journey_mode else
                "Ranked lower because another eligible visit occurred more recently."
            ),
        })
    intervention_detail = "; ".join(
        f"{line.get('claim_id') or 'claim'} ({line.get('cpt') or 'procedure'}): {_money(line.get('billed_amount')):,.2f} billed"
        for line in add_on_lines
    ) or "No separately identifiable intervention lines were found."
    earlier_line_keys = {
        (_text(item.get("cpt")), _text(item.get("procedure_description")).lower())
        for item in earlier["claims"]
    }
    excluded_marked_lines = [
        line for line in later["claims"]
        if line.get("intervention_performed") is True
        and line not in add_on_lines
        and (_text(line.get("cpt")), _text(line.get("procedure_description")).lower()) in earlier_line_keys
    ]
    excluded_detail = "; ".join(
        f"{line.get('claim_id') or 'claim'} ({line.get('cpt') or 'procedure'})"
        for line in excluded_marked_lines
    )
    intervention_reason = (
        "The later UTI visit records urine-culture or specimen services that are absent from the earlier visit, matching the attached workbook's intervention bundle."
        if family.startswith(("N30", "N39"))
        else f"The add-on is limited to these later-visit lines: {intervention_detail}. Each is marked as an intervention and was absent from the selected earlier visit; no other later lines are included."
    )
    if excluded_detail:
        intervention_reason += f" {excluded_detail} was not added because the same billed service was already present in the earlier visit."

    return {
        "available": True,
        "member_id": member_id,
        "diagnosis_family": family,
        "anchor_claim_id": anchor_claim_id or None,
        "synthetic_demo": bool(earlier.get("synthetic_demo") or later.get("synthetic_demo")),
        "template_logic": {
            "earlier_episode_without_intervention": True,
            "later_related_episode_with_intervention": True,
            "billed_intervention_lines_added_to_earlier": True,
            "intervention_scope": "UTI culture/specimen bundle" if family.startswith(("N30", "N39")) else "Distinct later service lines explicitly recorded as interventions in this member's claims",
        },
        "selection_audit": {
            "rule": (
                "Anchor the preventive follow-up, keep the same member and ICD-10 diagnosis family, then start the calculation at the first symptomatic visit and include each intervening visit. Billed amount does not choose the visit."
                if journey_mode else
                "Anchor the selected later claim, keep the same member and ICD-10 diagnosis family, then choose the closest qualifying earlier visit. Billed amount does not choose the visit."
            ),
            "anchor_reason": f"{anchor_line.get('claim_id')} is used because it is the claim opened on this prediction page.",
            "diagnosis_reason": f"Both visits are for ICD-10 family {family}." + (f" Both record the exact diagnosis {anchor_line.get('icd10')}." if exact_diagnosis_match else " The exact diagnosis codes differ within that family."),
            "earlier_visit_reason": (
                f"{earlier_line.get('claim_id')} is the first symptomatic visit. The later worsening visit is included in the actual journey and treated as the visit that may have been avoided by applying the intervention at this first visit."
                if journey_mode else
                f"{earlier_line.get('claim_id')} is the most recent earlier visit for this member that satisfies the diagnosis-family and intervention-evidence rules."
            ),
            "procedure_reason": f"Both visits record procedure {anchor_line.get('cpt')}." if same_procedure else "The procedures differ, but the later visit contains a separately identifiable intervention line.",
            "intervention_reason": intervention_reason,
            "candidate_visits": candidate_visits,
        },
        "calculation_basis": "billed_amount",
        "earlier_episode": _episode_summary(earlier),
        "intervening_episodes": [_episode_summary(episode) for episode in intervening_episodes],
        "later_episode": _episode_summary(later),
        "days_between_episodes": days_between,
        "days_to_first_intervening_episode": days_to_first_intervening,
        "culture_add_on_lines": add_on_lines,
        "intervention_lines": add_on_lines,
        "calculation": {
            "earlier_episode_actual_billed": earlier_billed,
            "later_episode_actual_billed": later_billed,
            "intervening_episode_actual_billed": intervening_billed,
            "culture_and_specimen_add_on_billed": add_on_billed,
            "actual_two_episode_billed": actual_billed,
            "proposed_earlier_episode_with_add_on_billed": proposed_billed,
            "potential_billed_difference": billed_difference,
            "formula": (
                "actual first visit + intervening worsening visits + preventive follow-up - (first visit + preventive intervention moved to the first visit)"
                if journey_mode else
                "actual earlier episode billed amount + actual later episode billed amount - (earlier episode billed amount + separately identifiable later intervention lines)"
            ),
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
        "synthetic_demo": episode.get("synthetic_demo", False),
        "total_paid": episode["total_paid"],
        "total_billed": episode["total_billed"],
        "total_allowed": episode["total_allowed"],
        "claims": episode["claims"],
        "cpt_breakdown": [
            {"cpt": cpt, **info}
            for cpt, info in sorted(episode["cpt_breakdown"].items(), key=lambda x: -x[1]["total_billed"])
        ],
    }
