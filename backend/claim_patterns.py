"""Deterministic, cutoff-safe claim matching and historical pattern evidence."""

from __future__ import annotations

from datetime import date
from statistics import median


SIMILARITY_WEIGHTS = {
    "icd_family": 25,
    "cpt": 25,
    "payer": 15,
    "provider": 10,
    "place_of_service": 10,
    "units": 5,
    "financial": 5,
    "timeframe": 5,
}
MIN_PEERS = 3
_EARLIER_CACHE = {}
_SHORT_CACHE = {}


def _text(value):
    return str(value or "").strip()


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _date(value):
    try:
        return date.fromisoformat(_text(value)[:10])
    except ValueError:
        return None


def field(claim, name, fallback=""):
    value = claim.get("workbookFields", {}).get(name)
    return fallback if value in (None, "") else value


def icd_family(claim):
    explicit = _text(field(claim, "ICD10_Family"))
    diagnosis = _text(claim.get("diagnosisCode") or field(claim, "ICD10_Diagnosis_Code"))
    return explicit or diagnosis.split(".")[0][:3]


def procedure_family(claim):
    code = "".join(ch for ch in _text(claim.get("cptCode")) if ch.isalnum())
    return code[:3]


def earlier_claims(database, claim):
    cutoff = _text(claim.get("dos"))
    key = (getattr(database, "workbook_hash", id(database)), _text(claim.get("claimId")), cutoff)
    if key in _EARLIER_CACHE:
        return _EARLIER_CACHE[key]
    source = getattr(database, "claims", getattr(database, "historical_claims", ()))
    result = [row for row in source if _text(row.get("dos")) < cutoff]
    _EARLIER_CACHE[key] = result
    return result


def peer_hierarchy(claim):
    def same(row, *dimensions):
        values = {
            "member": (_text(row.get("memberId")), _text(claim.get("memberId"))),
            "payer": (_text(row.get("payerId")), _text(claim.get("payerId"))),
            "provider": (_text(row.get("billingProviderNpi")), _text(claim.get("billingProviderNpi"))),
            "cpt": (_text(row.get("cptCode")), _text(claim.get("cptCode"))),
            "icd_family": (icd_family(row), icd_family(claim)),
            "place_of_service": (_text(row.get("placeOfServiceCode")), _text(claim.get("placeOfServiceCode"))),
        }
        return all(values[key][1] and values[key][0] == values[key][1] for key in dimensions)

    def similar_units(row):
        selected = _number(claim.get("units"))
        candidate = _number(row.get("units"))
        return selected > 0 and candidate > 0 and abs(candidate - selected) <= max(1, selected * 0.25)

    return [
        (1, "Same member + CPT + ICD family", ["member", "CPT", "ICD family"], lambda row: same(row, "member", "cpt", "icd_family")),
        (2, "Same payer + provider + CPT + ICD family + place of service + similar units", ["payer", "provider", "CPT", "ICD family", "place of service", "similar units"], lambda row: same(row, "payer", "provider", "cpt", "icd_family", "place_of_service") and similar_units(row)),
        (3, "Same payer + provider + CPT + ICD family + place of service", ["payer", "provider", "CPT", "ICD family", "place of service"], lambda row: same(row, "payer", "provider", "cpt", "icd_family", "place_of_service")),
        (4, "Same payer + CPT + ICD family + place of service", ["payer", "CPT", "ICD family", "place of service"], lambda row: same(row, "payer", "cpt", "icd_family", "place_of_service")),
        (5, "Same payer + CPT + ICD family", ["payer", "CPT", "ICD family"], lambda row: same(row, "payer", "cpt", "icd_family")),
        (6, "Same CPT + ICD family", ["CPT", "ICD family"], lambda row: same(row, "cpt", "icd_family")),
        (7, "Same ICD family + place of service", ["ICD family", "place of service"], lambda row: same(row, "icd_family", "place_of_service")),
        (8, "Same ICD family", ["ICD family"], lambda row: same(row, "icd_family")),
        (9, "Global historical baseline", ["earlier service date"], lambda row: True),
    ]


def select_peers(database, claim, minimum=MIN_PEERS):
    prior = earlier_claims(database, claim)
    for level, label, dimensions, matcher in peer_hierarchy(claim):
        matches = [row for row in prior if matcher(row)]
        if len(matches) >= minimum or level == 9:
            return matches, {
                "peer_level": level,
                "peer_label": label,
                "matching_dimensions": dimensions,
                "peer_count": len(matches),
                "claim_ids_used": [row["claimId"] for row in matches],
                "prediction_cutoff_date": claim.get("dos"),
                "readable_basis": f"{label}, {len(matches)} earlier historical claim(s).",
            }
    return [], {}


def _denied(row):
    fields = row.get("workbookFields", {})
    status = _text(row.get("status") or fields.get("Claim_Status_Description")).lower()
    return "denied" in status or "reject" in status or _text(fields.get("Denial_Correctable_Flag")).upper() in {"Y", "N"}


def similarity(row, claim):
    selected_date, row_date = _date(claim.get("dos")), _date(row.get("dos"))
    same_icd = bool(icd_family(claim) and icd_family(row) == icd_family(claim))
    same_cpt = bool(claim.get("cptCode") and row.get("cptCode") == claim.get("cptCode"))
    same_payer = bool(claim.get("payerId") and row.get("payerId") == claim.get("payerId"))
    same_provider = bool(claim.get("billingProviderNpi") and row.get("billingProviderNpi") == claim.get("billingProviderNpi"))
    same_pos = bool(claim.get("placeOfServiceCode") and row.get("placeOfServiceCode") == claim.get("placeOfServiceCode"))
    selected_units, row_units = _number(claim.get("units")), _number(row.get("units"))
    units = selected_units > 0 and row_units > 0 and abs(row_units - selected_units) <= max(1, selected_units * 0.25)
    selected_charge, row_charge = _number(claim.get("totalCharge")), _number(row.get("totalCharge"))
    financial = max(0.0, 1 - abs(row_charge - selected_charge) / max(selected_charge, row_charge, 1))
    days = (selected_date - row_date).days if selected_date and row_date else 9999
    time_score = max(0.0, 1 - days / 365) if days >= 0 else 0.0
    score = (
        SIMILARITY_WEIGHTS["icd_family"] * same_icd + SIMILARITY_WEIGHTS["cpt"] * same_cpt
        + SIMILARITY_WEIGHTS["payer"] * same_payer + SIMILARITY_WEIGHTS["provider"] * same_provider
        + SIMILARITY_WEIGHTS["place_of_service"] * same_pos + SIMILARITY_WEIGHTS["units"] * units
        + SIMILARITY_WEIGHTS["financial"] * financial + SIMILARITY_WEIGHTS["timeframe"] * time_score
    )
    reasons = [label for ok, label in ((same_cpt, f"CPT {claim.get('cptCode')}"), (same_icd, f"ICD family {icd_family(claim)}"), (same_payer, "payer"), (same_provider, "provider"), (same_pos, "place of service"), (units, "similar units")) if ok]
    return {
        "claim_id": row.get("claimId"), "service_date": row.get("dos"), "icd10": row.get("diagnosisCode"),
        "cpt": row.get("cptCode"), "provider": row.get("billingProvider"), "payer": row.get("payer"),
        "charge": round(_number(row.get("totalCharge")), 2), "allowed": round(_number(row.get("allowed")), 2),
        "paid": round(_number(row.get("paid")), 2), "similarity_score": round(score, 2),
        "same_icd_family": same_icd, "same_cpt": same_cpt, "same_payer": same_payer,
        "same_provider": same_provider, "same_place_of_service": same_pos, "similar_units": units,
        "financial_similarity": round(financial * SIMILARITY_WEIGHTS["financial"], 2),
        "time_similarity": round(time_score * SIMILARITY_WEIGHTS["timeframe"], 2),
        "match_reason": "Matched on " + ", ".join(reasons) + "." if reasons else "Ranked by earlier-date financial and timeframe similarity.",
    }


def similar_historical_claims(database, claim, limit=10):
    ranked = [similarity(row, claim) for row in earlier_claims(database, claim)]
    return sorted(ranked, key=lambda item: (-item["similarity_score"], item["service_date"], item["claim_id"]))[:limit]


def short_timeframe_patterns(database, claim):
    cache_key = (getattr(database, "workbook_hash", id(database)), _text(claim.get("claimId")), _text(claim.get("dos")))
    if cache_key in _SHORT_CACHE:
        return _SHORT_CACHE[cache_key]
    member_rows = sorted(
        [row for row in earlier_claims(database, claim) if row.get("memberId") == claim.get("memberId")],
        key=lambda row: (row.get("dos", ""), row.get("claimId", "")),
    )
    pairs = []
    for first_index, first in enumerate(member_rows):
      for second in member_rows[first_index + 1:]:
        first_date, second_date = _date(first.get("dos")), _date(second.get("dos"))
        if not first_date or not second_date:
            continue
        days = (second_date - first_date).days
        if days > 90:
            break
        flags = {
            "same_cpt": first.get("cptCode") == second.get("cptCode"),
            "same_icd_family": icd_family(first) == icd_family(second),
            "same_provider": first.get("billingProviderNpi") == second.get("billingProviderNpi"),
            "same_payer": first.get("payerId") == second.get("payerId"),
            "same_procedure_family": procedure_family(first) == procedure_family(second),
            "same_episode": bool(first.get("episodeId") and first.get("episodeId") == second.get("episodeId")),
        }
        score = 25 * flags["same_cpt"] + 25 * flags["same_icd_family"] + 15 * flags["same_provider"] + 10 * flags["same_payer"] + 10 * flags["same_procedure_family"] + 15 * flags["same_episode"]
        if score:
            pairs.append({"claim_1": first["claimId"], "claim_2": second["claimId"], "date_1": first.get("dos"), "date_2": second.get("dos"), "days_apart": days, **flags, "relationship_score": score})
    result = sorted(pairs, key=lambda item: (item["days_apart"], -item["relationship_score"]))
    _SHORT_CACHE[cache_key] = result
    return result


def historical_patterns(database, claim):
    prior = earlier_claims(database, claim)
    member = [row for row in prior if row.get("memberId") == claim.get("memberId")]
    pairs = short_timeframe_patterns(database, claim)
    gaps = [pair["days_apart"] for pair in pairs]
    return {
        "earlier_member_claims": len(member),
        "same_cpt_claims": sum(row.get("cptCode") == claim.get("cptCode") for row in prior),
        "same_icd_family_claims": sum(icd_family(row) == icd_family(claim) for row in prior),
        "same_cpt_icd_claims": sum(row.get("cptCode") == claim.get("cptCode") and icd_family(row) == icd_family(claim) for row in prior),
        "previous_denials": sum(_denied(row) for row in member),
        **{f"within_{horizon}_days": sum(pair["days_apart"] <= horizon for pair in pairs) for horizon in (3, 7, 30, 60, 90)},
        "median_days_between_related_claims": round(median(gaps), 1) if gaps else None,
    }
