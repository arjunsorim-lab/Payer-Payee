"""Deterministic, evidence-first analytics for the local 837 claims collection."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import date, datetime
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


class ClaimsAnalytics:
    def __init__(self, database, collection_name: str = "837_claims") -> None:
        self.database = database
        self.claims = database[collection_name]

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
