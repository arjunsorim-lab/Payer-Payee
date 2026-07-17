"""Database-driven patient episode predictions for the provider dashboard.

The model is deliberately explainable: it groups claims into member/diagnosis
episodes, uses peer adjudication rates for the financial forecast, and scores
repeat utilisation from fields available in the imported 837 CSV.
"""

from collections import Counter, defaultdict
from datetime import date
from statistics import median


CATEGORY_RULES = [
    (("I",), "cardiac", "Cardiovascular"),
    (("G",), "neurologic", "Neurology"),
    (("E",), "endocrine", "Endocrine / diabetes"),
    (("J",), "respiratory", "Respiratory"),
    (("K",), "digestive", "Digestive health"),
    (("F",), "behavioral", "Behavioral health"),
    (("M",), "musculoskeletal", "Musculoskeletal"),
    (("N",), "renal", "Renal / urinary"),
    (("Z",), "preventive", "Preventive care"),
]


def _money(value):
    return round(float(value or 0), 2)


def _category(code):
    for prefixes, category, label in CATEGORY_RULES:
        if str(code or "").upper().startswith(prefixes):
            return category, label
    return "general", "General clinical"


def _risk_level(score):
    return "High" if score >= 70 else "Medium" if score >= 40 else "Low"


def _condition(claims):
    descriptions = [claim.get("diagnosisDescription", "").strip() for claim in claims]
    descriptions = [description for description in descriptions if description]
    if not descriptions:
        return claims[-1].get("diagnosisCode") or "Unspecified condition"
    # Prefer the shortest stable description; CSV descriptions often append a procedure.
    return min(descriptions, key=lambda value: (len(value), value))


def _peer_rates(claims):
    groups = defaultdict(list)
    for claim in claims:
        keys = [
            (claim.get("diagnosisCode"), claim.get("payer"), claim.get("cptCode"), claim.get("placeOfServiceCode")),
            (claim.get("diagnosisCode"), claim.get("payer"), None, None),
            (claim.get("diagnosisCode"), None, None, None),
        ]
        for key in keys:
            groups[key].append(claim)
    return groups


def _financial_forecast(episode, peer_groups):
    charge = sum(_money(claim.get("totalCharge")) for claim in episode)
    key_options = [
        (episode[-1].get("diagnosisCode"), episode[-1].get("payer"), episode[-1].get("cptCode"), episode[-1].get("placeOfServiceCode")),
        (episode[-1].get("diagnosisCode"), episode[-1].get("payer"), None, None),
        (episode[-1].get("diagnosisCode"), None, None, None),
    ]
    peers = next((peer_groups[key] for key in key_options if len(peer_groups.get(key, [])) >= 3), episode)

    allowed_rates = [_money(row.get("allowed")) / _money(row.get("totalCharge")) for row in peers if _money(row.get("totalCharge")) > 0]
    paid_rates = [_money(row.get("paid")) / _money(row.get("allowed")) for row in peers if _money(row.get("allowed")) > 0]
    patient_rates = [_money(row.get("patientResp")) / _money(row.get("allowed")) for row in peers if _money(row.get("allowed")) > 0]
    allowed = charge * (median(allowed_rates) if allowed_rates else 0)
    paid = allowed * (median(paid_rates) if paid_rates else 0)
    patient_resp = allowed * (median(patient_rates) if patient_rates else 0)
    adjustment = max(charge - allowed, 0)
    return {
        "charge": _money(charge),
        "allowed": _money(allowed),
        "paid": _money(paid),
        "patientResp": _money(patient_resp),
        "adjustment": _money(adjustment),
    }, len(peers)


def _episode_explanations(episode, condition, repeated_services, denied, settings):
    cpt_counts = Counter(claim.get("cptCode") for claim in episode if claim.get("cptCode"))
    repeated_cpt = cpt_counts.most_common(1)[0][0] if cpt_counts else "the recorded service"
    repeated_claim = next((claim for claim in reversed(episode) if claim.get("cptCode") == repeated_cpt), episode[-1])
    repeated_label = repeated_claim.get("cptDescription") or repeated_cpt
    provider = episode[-1].get("billingProvider") or "the recorded provider"
    payer = episode[-1].get("payer") or "the recorded payer"
    latest_date = episode[-1].get("dos") or "the latest service date"
    unique_cpts = len(cpt_counts)

    doctor_steps = [
        (f"Review {condition}", f"Compare all {len(episode)} related claims from {episode[0].get('dos') or 'the first visit'} through {latest_date}."),
        (f"Check {repeated_cpt} history", f"Review {repeated_label} results before another service; this episode contains {unique_cpts} unique CPT code(s)."),
        ("Resolve claim and care signals", f"Assess {denied} denied claim(s) and services delivered across {settings or 1} recorded care setting(s)."),
        ("Coordinate the next follow-up", f"Align the next documented step between {provider} and {payer}."),
    ]
    savings_actions = [
        f"Review the existing {repeated_cpt} {repeated_label} record before ordering the same service again.",
        f"Coordinate the next visit with {provider}; {repeated_services} service(s) repeat a CPT in this episode.",
        f"Check {denied} denial(s) and the {payer} adjudication history before the next claim is submitted.",
    ]
    return doctor_steps, savings_actions


def build_prediction_scenarios(claims, min_claims=2):
    claims = [dict(claim) for claim in claims if claim.get("memberId") and claim.get("diagnosisCode")]
    peer_groups = _peer_rates(claims)
    episodes = defaultdict(list)
    for claim in claims:
        episodes[(claim["memberId"], claim["diagnosisCode"])].append(claim)

    scenarios = []
    for (member_id, diagnosis_code), episode in episodes.items():
        if len(episode) < min_claims:
            continue
        episode.sort(key=lambda row: (row.get("dos", ""), row.get("claimId", "")))
        anchor = episode[-1]
        category, pathway_label = _category(diagnosis_code)
        forecast, peer_count = _financial_forecast(episode, peer_groups)
        unique_cpts = len({claim.get("cptCode") for claim in episode if claim.get("cptCode")})
        repeated_services = len(episode) - unique_cpts
        denied = sum(1 for claim in episode if "denied" in str(claim.get("status", "")).lower())
        settings = len({claim.get("placeOfServiceCode") for claim in episode if claim.get("placeOfServiceCode")})
        try:
            span_days = max(1, (date.fromisoformat(episode[-1]["dos"]) - date.fromisoformat(episode[0]["dos"])).days)
        except (KeyError, TypeError, ValueError):
            span_days = 365
        risk_score = min(96, round(25 + min(len(episode), 12) * 3.2 + min(repeated_services, 8) * 2.5 + denied * 5 + settings * 2 + (8 if span_days <= 90 else 0)))
        risk = {"score": risk_score, "level": _risk_level(risk_score)}
        savings_rate = min(0.42, 0.08 + repeated_services * 0.018 + max(len(episode) - 3, 0) * 0.008 + denied * 0.015)
        avoidable_spend = _money(forecast["allowed"] * savings_rate)
        confidence = "High" if peer_count >= 25 and len(episode) >= 5 else "Medium" if peer_count >= 8 else "Low"
        pathway, savings_actions = _episode_explanations(
            episode, _condition(episode), repeated_services, denied, settings,
        )
        visits = []
        for index, claim in enumerate(episode[-5:], 1):
            visits.append({
                "number": index,
                "title": claim.get("cptDescription") or claim.get("placeOfService") or "Clinical service",
                "detail": f"{claim.get('cptCode') or 'Service'} · billed ${_money(claim.get('totalCharge')):,.2f}, paid ${_money(claim.get('paid')):,.2f}",
                "claim": claim,
            })

        scenarios.append({
            "id": f"{member_id}-{diagnosis_code}",
            "anchor": anchor,
            "patient": anchor.get("patient") or "Unknown patient",
            "memberId": member_id,
            "payer": anchor.get("payer") or "Unknown payer",
            "provider": anchor.get("billingProvider") or "Unknown provider",
            "diagnosisCode": diagnosis_code,
            "condition": _condition(episode),
            "category": category,
            "pathway": {"label": pathway_label},
            "episodeStart": episode[0].get("dos", ""),
            "episodeEnd": episode[-1].get("dos", ""),
            "totalVisitCount": len(episode),
            "peerCount": peer_count,
            "confidence": confidence,
            "risk": risk,
            "forecast": forecast,
            "avoidableSpend": avoidable_spend,
            "likelyOutcome": "Escalation likely without review" if risk_score >= 70 else "Repeat service likely" if risk_score >= 40 else "Routine follow-up likely",
            "bestSavingsPhase": "Before the next repeat service" if repeated_services else "At scheduled follow-up",
            "visits": visits,
            "doctorSteps": pathway,
            "patientImpact": [
                f"{len(episode)} related claims are visible in the current episode.",
                f"{repeated_services} service(s) repeat one of {unique_cpts} CPT codes recorded for this diagnosis.",
                f"The database records ${_money(sum(claim.get('patientResp') or 0 for claim in episode)):,.2f} in total patient responsibility for this episode.",
            ],
            "savingsActions": savings_actions,
            "riskReasons": [
                f"{len(episode)} claims share this member and diagnosis code.",
                f"{repeated_services} services repeat a CPT already present in the episode.",
                f"The episode spans {settings or 1} recorded care setting(s) with {denied} denied claim(s).",
            ],
            "sourceClaimIds": [claim.get("claimId") for claim in episode],
        })

    return sorted(scenarios, key=lambda item: (item["risk"]["score"], item["avoidableSpend"]), reverse=True)


def summarize_scenarios(scenarios):
    return {
        "totalScenarios": len(scenarios),
        "highRiskCount": sum(1 for item in scenarios if item["risk"]["level"] == "High"),
        "predictedPaid": _money(sum(item["forecast"]["paid"] for item in scenarios)),
        "avoidableSpend": _money(sum(item["avoidableSpend"] for item in scenarios)),
    }
