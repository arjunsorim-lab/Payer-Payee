"""Deterministic provider-side claim episode forecasting.

This module is intentionally independent of the LLM. It validates canonical
claim documents, assigns every valid claim to exactly one 90-day episode, and
uses only earlier adjudicated peer claims for financial and risk estimates.
"""

from collections import Counter, defaultdict
from datetime import date, datetime
from hashlib import sha256
from statistics import median


MODEL_VERSION = "provider-forecast-v2.1"
DEFAULT_WINDOW_DAYS = 90
DEFAULT_MIN_PEERS = 5
_BATCH_CACHE = {}


def _amount(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _quantile(values, probability):
    values = sorted(float(value) for value in values)
    if not values:
        return 0.0
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _diagnosis_family(code):
    text = "".join(character for character in str(code or "").upper() if character.isalnum())
    return text[:3] or "UNKNOWN"


def _procedure_family(code):
    text = "".join(character for character in str(code or "").upper() if character.isalnum())
    return text[:3] or "UNKNOWN"


def _is_denied(claim):
    status = f"{claim.get('statusCode', '')} {claim.get('status', '')} {claim.get('denialReason', '')}".lower()
    return "denied" in status or str(claim.get("statusCode", "")) in {"4", "F"}


def validate_claims(claims):
    """Return valid canonical claims and an auditable validation report."""
    issues = Counter()
    valid = []
    seen_ids = set()
    for source in claims:
        claim = dict(source)
        claim_id = str(claim.get("claimId") or claim.get("number") or "").strip()
        member_id = str(claim.get("memberId") or "").strip()
        service_date = _parse_date(claim.get("dos"))
        if not claim_id:
            issues["missing_claim_id"] += 1
        if claim_id in seen_ids:
            issues["duplicate_claim_id"] += 1
        if not member_id:
            issues["missing_member_id"] += 1
        if not service_date:
            issues["invalid_service_date"] += 1
        if not str(claim.get("cptCode") or "").strip():
            issues["missing_cpt"] += 1
        if not str(claim.get("diagnosisCode") or "").strip():
            issues["missing_diagnosis"] += 1
        if not str(claim.get("status") or claim.get("statusCode") or "").strip():
            issues["null_claim_status"] += 1
        amounts = {field: _amount(claim.get(field)) for field in ("totalCharge", "allowed", "paid", "patientResp", "adjustment")}
        if any(value < 0 for value in amounts.values()):
            issues["negative_amount"] += 1
        if amounts["allowed"] > amounts["totalCharge"] + 0.01 or amounts["paid"] > amounts["allowed"] + amounts["patientResp"] + 0.01:
            issues["impossible_amount_relationship"] += 1
        blocking = not claim_id or claim_id in seen_ids or not member_id or not service_date
        seen_ids.add(claim_id)
        if blocking:
            continue
        claim.update(amounts)
        claim["claimId"] = claim_id
        claim["_serviceDate"] = service_date
        claim["diagnosisFamily"] = _diagnosis_family(claim.get("diagnosisCode"))
        claim["procedureFamily"] = _procedure_family(claim.get("cptCode"))
        valid.append(claim)
    return valid, {
        "inputClaims": len(claims),
        "validClaims": len(valid),
        "invalidClaims": len(claims) - len(valid),
        "issues": dict(sorted(issues.items())),
    }


def build_episodes(claims, window_days=DEFAULT_WINDOW_DAYS):
    """Group claims by member/diagnosis family and split on a configurable gap."""
    grouped = defaultdict(list)
    for claim in claims:
        grouped[(claim["memberId"], claim["diagnosisFamily"])].append(claim)
    episodes = []
    for (member_id, diagnosis_family), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: (row["_serviceDate"], row["claimId"]))
        chunks = [[]]
        for claim in rows:
            if chunks[-1] and (claim["_serviceDate"] - chunks[-1][-1]["_serviceDate"]).days > window_days:
                chunks.append([])
            chunks[-1].append(claim)
        for chunk in chunks:
            token = f"{member_id}|{diagnosis_family}|{chunk[0]['_serviceDate'].isoformat()}|{chunk[-1]['claimId']}"
            episode_id = "EPI-" + sha256(token.encode("utf-8")).hexdigest()[:12].upper()
            episodes.append({
                "episodeId": episode_id,
                "memberId": member_id,
                "diagnosisFamily": diagnosis_family,
                "start": chunk[0]["_serviceDate"],
                "end": chunk[-1]["_serviceDate"],
                "claims": chunk,
            })
    episodes.sort(key=lambda item: (item["start"], item["episodeId"]))
    return episodes


def _episode_features(episode):
    claims = episode["claims"]
    cpts = Counter(claim.get("cptCode") for claim in claims if claim.get("cptCode"))
    providers = {claim.get("billingProviderNpi") or claim.get("billingProvider") for claim in claims}
    places = {claim.get("placeOfServiceCode") for claim in claims if claim.get("placeOfServiceCode")}
    denied = sum(_is_denied(claim) for claim in claims)
    return {
        "claimCount": len(claims),
        "uniqueProcedureCount": len(cpts),
        "repeatedServiceCount": sum(max(count - 1, 0) for count in cpts.values()),
        "providerCount": len({item for item in providers if item}),
        "placeOfServiceCount": len(places),
        "deniedCount": denied,
        "priorAuthMissingCount": sum(not str(claim.get("priorAuth") or "").strip() for claim in claims),
        "submissionLagDaysMedian": median([
            (_parse_date(claim.get("submissionDate")) - claim["_serviceDate"]).days
            for claim in claims
            if _parse_date(claim.get("submissionDate")) and _parse_date(claim.get("submissionDate")) >= claim["_serviceDate"]
        ]) if any(_parse_date(claim.get("submissionDate")) and _parse_date(claim.get("submissionDate")) >= claim["_serviceDate"] for claim in claims) else None,
        "charges": round(sum(claim["totalCharge"] for claim in claims), 2),
    }


def _peer_key(episode, level):
    anchor = episode["claims"][-1]
    keys = [
        (episode["diagnosisFamily"], anchor.get("payerId") or anchor.get("payer"), anchor.get("billingProviderNpi"), anchor.get("cptCode"), anchor.get("placeOfServiceCode")),
        (episode["diagnosisFamily"], anchor.get("payerId") or anchor.get("payer"), None, anchor.get("cptCode"), anchor.get("placeOfServiceCode")),
        (episode["diagnosisFamily"], anchor.get("payerId") or anchor.get("payer"), None, anchor.get("procedureFamily"), None),
        (episode["diagnosisFamily"], None, None, None, None),
        (None, None, None, None, None),
    ]
    return keys[level]


def _matching_peers(target, all_episodes, min_peers):
    eligible = [episode for episode in all_episodes if episode["end"] < target["start"] and episode["memberId"] != target["memberId"]]
    for level in range(5):
        key = _peer_key(target, level)
        peers = [episode for episode in eligible if _peer_key(episode, level) == key]
        if len(peers) >= min_peers or (level == 4 and peers):
            return peers, ["payer+provider+CPT+POS", "payer+CPT+POS", "payer+procedure family", "diagnosis family", "all prior episodes"][level], level
    return [], "insufficient prior history", 5


def _money_forecast(episode, peers, hierarchy):
    target_charge = sum(claim["totalCharge"] for claim in episode["claims"])
    rows = [claim for peer in peers for claim in peer["claims"] if claim["totalCharge"] > 0]
    allowed_rates = [claim["allowed"] / claim["totalCharge"] for claim in rows]
    paid_rates = [claim["paid"] / claim["allowed"] for claim in rows if claim["allowed"] > 0]
    patient_rates = [claim["patientResp"] / claim["allowed"] for claim in rows if claim["allowed"] > 0]
    adjustment_rates = [claim["adjustment"] / claim["totalCharge"] for claim in rows]
    def interval(rates):
        return {"low": round(target_charge * _quantile(rates, .25), 2), "high": round(target_charge * _quantile(rates, .75), 2)}
    allowed_range = interval(allowed_rates)
    allowed_mid = round(target_charge * median(allowed_rates), 2) if allowed_rates else 0
    paid_range = {"low": round(allowed_mid * _quantile(paid_rates, .25), 2), "high": round(allowed_mid * _quantile(paid_rates, .75), 2)}
    paid_mid = round(allowed_mid * median(paid_rates), 2) if paid_rates else 0
    patient_mid = round(allowed_mid * median(patient_rates), 2) if patient_rates else 0
    adjustment_mid = round(target_charge * median(adjustment_rates), 2) if adjustment_rates else max(round(target_charge - allowed_mid, 2), 0)
    patient_range = {"low": round(allowed_mid * _quantile(patient_rates, .25), 2), "high": round(allowed_mid * _quantile(patient_rates, .75), 2)} if patient_rates else {"low": None, "high": None}
    adjustment_range = interval(adjustment_rates) if adjustment_rates else {"low": None, "high": None}
    return {
        "charge": round(target_charge, 2),
        "allowed": allowed_mid,
        "allowedRange": allowed_range,
        "paid": paid_mid,
        "paidRange": paid_range,
        "patientResp": patient_mid,
        "patientRespRange": patient_range,
        "adjustment": adjustment_mid,
        "adjustmentRange": adjustment_range,
        "peerHierarchy": hierarchy,
        "peerStatistics": {
            "medianAllowedRate": round(median(allowed_rates), 4) if allowed_rates else None,
            "medianPaidToAllowedRate": round(median(paid_rates), 4) if paid_rates else None,
            "medianPatientToAllowedRate": round(median(patient_rates), 4) if patient_rates else None,
            "allowedRateIqr": round(_quantile(allowed_rates, .75) - _quantile(allowed_rates, .25), 4) if allowed_rates else None,
        },
    }


def _probability_level(probability):
    return "High" if probability >= .67 else "Medium" if probability >= .34 else "Low"


def _safe_claim(claim):
    fields = ("claimId", "number", "dos", "serviceEnd", "payer", "payerId", "billingProvider", "billingProviderNpi", "renderingProvider", "renderingProviderNpi", "placeOfServiceCode", "placeOfService", "cptCode", "cptDescription", "diagnosisCode", "diagnosisDescription", "units", "totalCharge", "allowed", "paid", "patientResp", "adjustment", "status", "statusCode", "denialReason", "priorAuth", "referral", "submissionDate")
    return {field: claim.get(field) for field in fields}


def _display_outcome(status):
    text = str(status or "").strip()
    return text or "Outcome unavailable"


def _outcome_key(status):
    return "_".join(str(status or "unknown").lower().replace("(s)", "s").split())


def _confidence_details(peer_claims, match_level, selected_claim, financial):
    required_fields = ("payerId", "billingProviderNpi", "cptCode", "diagnosisCode", "placeOfServiceCode", "totalCharge", "dos")
    completeness = sum(selected_claim.get(field) not in (None, "") for field in required_fields) / len(required_fields)
    sample_factor = min(len(peer_claims) / 100, 1)
    specificity_factor = (1.0, .9, .75, .55, .35, .1)[min(match_level, 5)]
    iqr = financial["peerStatistics"].get("allowedRateIqr")
    variance_factor = 0 if iqr is None else max(0, 1 - min(iqr / .5, 1))
    score = round((sample_factor * .35 + specificity_factor * .25 + completeness * .2 + variance_factor * .2) * 100)
    level = "High" if score >= 80 else "Medium" if score >= 55 else "Low"
    if level == "High":
        explanation = "Large peer sample, specific matching dimensions, complete claim fields, and stable peer allowed rates."
    elif level == "Medium":
        explanation = "Adequate peer evidence, but some matching dimensions required fallback or peer amounts show wider variation."
    else:
        explanation = "A small peer group, broad fallback population, incomplete claim fields, or high peer variation reduced confidence."
    return {
        "score": score / 100,
        "percentage": score,
        "level": level,
        "explanation": explanation,
        "factors": {
            "peerSample": round(sample_factor, 3),
            "matchSpecificity": round(specificity_factor, 3),
            "dataCompleteness": round(completeness, 3),
            "peerAmountStability": round(variance_factor, 3),
        },
    }


def _conditional_actions(features, denial_probability, repeat, selected_claim, avoidable_supported):
    actions = []
    if features["deniedCount"] > 0:
        actions.append({"code": "review_denial", "title": "Review the recorded denial", "reason": f"This episode contains {features['deniedCount']} denied claim(s)."})
    if features["repeatedServiceCount"] > 0:
        actions.append({"code": "review_repeat", "title": "Validate repeated-service documentation", "reason": f"The episode contains {features['repeatedServiceCount']} repeated CPT service(s)."})
    if not str(selected_claim.get("priorAuth") or "").strip():
        actions.append({"code": "verify_authorization", "title": "Verify payer authorization requirements", "reason": "No prior-authorization number is recorded; the data does not establish whether authorization was required."})
    if not str(selected_claim.get("referral") or "").strip():
        actions.append({"code": "verify_referral", "title": "Verify payer referral requirements", "reason": "No referral number is recorded; the data does not establish whether a referral was required."})
    if repeat["90"] >= .34:
        actions.append({"code": "monitor_follow_up", "title": "Monitor provider-side follow-up", "reason": f"The deterministic 90-day repeat-service probability is {repeat['90'] * 100:.1f}%."})
    if avoidable_supported:
        actions.append({"code": "review_savings", "title": "Review the potentially avoidable repeat-service opportunity", "reason": "Repeated-service evidence and the minimum peer threshold are both present."})
    if not actions:
        actions.append({"code": "no_immediate_action", "title": "No immediate exception action", "reason": "No denial, repeat-service, authorization, referral, or elevated repeat-risk exception is supported by the episode data."})
    return actions


def score_episode(episode, all_episodes, min_peers=DEFAULT_MIN_PEERS):
    features = _episode_features(episode)
    peers, hierarchy, match_level = _matching_peers(episode, all_episodes, min_peers)
    financial = _money_forecast(episode, peers, hierarchy)
    peer_claims = [claim for peer in peers for claim in peer["claims"]]
    peer_denials = sum(_is_denied(claim) for claim in peer_claims)
    denial_probability = round((peer_denials + 1) / (len(peer_claims) + 4), 3) if peer_claims else .25
    repeat_base = min(.92, .08 + features["claimCount"] * .07 + features["repeatedServiceCount"] * .12 + features["placeOfServiceCount"] * .025)
    repeat = {"30": round(repeat_base * .62, 3), "60": round(repeat_base * .82, 3), "90": round(repeat_base, 3)}
    repeated_allowed = [claim["allowed"] for claim in episode["claims"] if Counter(row.get("cptCode") for row in episode["claims"])[claim.get("cptCode")] > 1]
    sufficient_avoidable_evidence = features["repeatedServiceCount"] > 0 and len(peers) >= min_peers
    avoidable = round(median(repeated_allowed) * min(features["repeatedServiceCount"], 3), 2) if sufficient_avoidable_evidence and repeated_allowed else 0
    anchor = episode["claims"][-1]
    confidence_detail = _confidence_details(peer_claims, match_level, anchor, financial)
    confidence_score = confidence_detail["percentage"]
    confidence = confidence_detail["level"]
    priority = round(min(100, repeat["90"] * 40 + denial_probability * 25 + min(avoidable / 5000, 1) * 25 + (1 - confidence_score / 100) * 10))
    common_next_cpt = Counter(claim.get("cptCode") for claim in peer_claims if claim.get("cptCode")).most_common(1)
    common_next_pos = Counter(claim.get("placeOfServiceCode") for claim in peer_claims if claim.get("placeOfServiceCode")).most_common(1)
    peer_outcomes = Counter(_display_outcome(claim.get("status")) for claim in peer_claims)
    likely_outcome, likely_outcome_count = peer_outcomes.most_common(1)[0] if peer_outcomes else ("Outcome unavailable", 0)
    outcome_probability = round(likely_outcome_count / len(peer_claims), 3) if peer_claims else None
    actions = _conditional_actions(features, denial_probability, repeat, anchor, sufficient_avoidable_evidence)
    evidence = [
        {"claimId": claim["claimId"], "fields": ["dos", "diagnosisCode", "cptCode", "placeOfServiceCode", "status", "allowed", "paid"]}
        for claim in episode["claims"]
    ]
    return {
        "episodeId": episode["episodeId"],
        "memberReference": "MBR-" + sha256(episode["memberId"].encode("utf-8")).hexdigest()[:10].upper(),
        "episodeStart": episode["start"].isoformat(),
        "episodeEnd": episode["end"].isoformat(),
        "diagnosisFamily": episode["diagnosisFamily"],
        "features": features,
        "forecast": financial,
        "denialRisk": {"probability": denial_probability, "score": round(denial_probability * 100), "level": _probability_level(denial_probability), "category": "historical peer adjudication"},
        "repeatRisk": {"probabilities": repeat, "score": round(repeat["90"] * 100), "level": _probability_level(repeat["90"])},
        "predictedOutcome": {"value": _outcome_key(likely_outcome), "displayValue": likely_outcome, "probability": outcome_probability},
        "nextService": {"cptCode": common_next_cpt[0][0] if common_next_cpt else None, "placeOfServiceCode": common_next_pos[0][0] if common_next_pos else None},
        "avoidableSpend": avoidable,
        "avoidableSpendLabel": "Potentially avoidable repeat-service spend",
        "avoidableSpendSupported": sufficient_avoidable_evidence,
        "bestSavingsPhase": "Before the next repeat service" if sufficient_avoidable_evidence else "At pre-submission review",
        "priorityScore": priority,
        "confidence": confidence,
        "confidenceScore": confidence_score,
        "confidenceDetail": confidence_detail,
        "peerCount": len(peers),
        "peerClaimCount": len(peer_claims),
        "peerMatchLevel": match_level,
        "forecastContext": "retrospective_current_claim",
        "method": MODEL_VERSION,
        "anchor": _safe_claim(anchor),
        "claims": [_safe_claim(claim) for claim in episode["claims"]],
        "sourceClaimIds": [claim["claimId"] for claim in episode["claims"]],
        "evidence": evidence,
        "structuredRiskDrivers": [
            {
                "title": "Historical peer denial rate",
                "value": f"{denial_probability * 100:.1f}%",
                "riskDirection": _probability_level(denial_probability).lower(),
                "sourceType": "model_feature",
                "reason": f"Calculated with smoothing from {len(peer_claims)} earlier adjudicated peer claim(s).",
                "evidenceIds": [f"PEER-{episode['episodeId']}"],
            },
            {
                "title": "Repeated CPT services",
                "value": str(features["repeatedServiceCount"]),
                "riskDirection": "increases" if features["repeatedServiceCount"] else "none",
                "sourceType": "claim_fact",
                "reason": f"{features['claimCount']} claim(s) and {features['repeatedServiceCount']} repeated CPT service(s) were found in this episode.",
                "evidenceIds": [claim["claimId"] for claim in episode["claims"]],
            },
            {
                "title": "90-day repeat-service probability",
                "value": f"{repeat['90'] * 100:.1f}%",
                "riskDirection": _probability_level(repeat["90"]).lower(),
                "sourceType": "model_output",
                "reason": "Calculated from episode utilisation, repeated-service and care-setting features.",
                "evidenceIds": [episode["episodeId"]],
            },
        ],
        "riskDrivers": [
            f"Historical peer denial probability is {denial_probability * 100:.1f}% from {len(peer_claims)} earlier adjudicated peer claim(s).",
            f"The episode contains {features['repeatedServiceCount']} repeated CPT service(s).",
            f"The deterministic 90-day repeat-service probability is {repeat['90'] * 100:.1f}%.",
        ],
        "structuredRecommendedActions": actions,
        "recommendedActions": [f"{action['title']}: {action['reason']}" for action in actions],
    }


def _dataset_fingerprint(claims, window_days, min_peers):
    digest = sha256(f"{window_days}|{min_peers}|{len(claims)}".encode("utf-8"))
    for claim in sorted(claims, key=lambda item: str(item.get("claimId") or item.get("number") or "")):
        digest.update("|".join(str(claim.get(field) or "") for field in (
            "claimId", "memberId", "dos", "diagnosisCode", "cptCode", "payerId",
            "billingProviderNpi", "placeOfServiceCode", "totalCharge", "allowed",
            "paid", "patientResp", "adjustment", "status", "denialReason",
            "priorAuth", "referral", "submissionDate",
        )).encode("utf-8"))
    return digest.hexdigest()


def build_provider_batch(claims, window_days=DEFAULT_WINDOW_DAYS, min_peers=DEFAULT_MIN_PEERS, use_cache=True):
    fingerprint = _dataset_fingerprint(claims, window_days, min_peers)
    if use_cache and fingerprint in _BATCH_CACHE:
        return _BATCH_CACHE[fingerprint]
    valid, validation = validate_claims(claims)
    episodes = build_episodes(valid, window_days)
    scored = [score_episode(episode, episodes, min_peers) for episode in episodes]
    assigned = [claim_id for item in scored for claim_id in item["sourceClaimIds"]]
    quality = {
        "allValidClaimsAssignedOnce": len(assigned) == len(set(assigned)) == len(valid),
        "assignedClaimCount": len(assigned),
        "episodeCount": len(scored),
        "highPriorityCount": sum(item["priorityScore"] >= 70 for item in scored),
        "lowConfidenceCount": sum(item["confidence"] == "Low" for item in scored),
        "unsupportedAvoidableSpendCount": sum(not item["avoidableSpendSupported"] for item in scored),
    }
    result = scored, {"validation": validation, "quality": quality, "modelVersion": MODEL_VERSION, "episodeWindowDays": window_days, "minimumPeerEpisodes": min_peers}
    if use_cache:
        _BATCH_CACHE.clear()
        _BATCH_CACHE[fingerprint] = result
    return result


def find_case(claims, claim_number, window_days=DEFAULT_WINDOW_DAYS, min_peers=DEFAULT_MIN_PEERS):
    episodes, report = build_provider_batch(claims, window_days, min_peers)
    needle = str(claim_number)
    case = next((item for item in episodes if any(needle in {str(claim.get("claimId")), str(claim.get("number"))} for claim in item["claims"])), None)
    if not case:
        return None, report
    selected = next(claim for claim in case["claims"] if needle in {str(claim.get("claimId")), str(claim.get("number"))})
    case["selectedClaim"] = selected
    return case, report


def build_provider_prediction_payload(scenario):
    """Create the UI/API contract with actual facts separated from estimates."""
    selected = scenario.get("selectedClaim") or scenario.get("anchor") or {}
    financial = scenario.get("forecast", {})
    repeat = scenario.get("repeatRisk", {}).get("probabilities", {})
    denial = scenario.get("denialRisk", {})
    confidence = scenario.get("confidenceDetail", {})
    features = scenario.get("features", {})
    avoidable_available = bool(scenario.get("avoidableSpendSupported"))
    if features.get("repeatedServiceCount", 0) == 0:
        unavailable_reason = "No repeated related service was found in the episode."
    elif scenario.get("peerCount", 0) < DEFAULT_MIN_PEERS:
        unavailable_reason = "The episode does not meet the minimum peer threshold."
    else:
        unavailable_reason = "The available episode evidence is insufficient for a reliable estimate."

    actual_facts = {
        "claim_id": selected.get("claimId"),
        "claim_number": selected.get("number"),
        "claim_status": selected.get("status"),
        "service_date": selected.get("dos"),
        "cpt_code": selected.get("cptCode"),
        "cpt_description": selected.get("cptDescription"),
        "diagnosis_code": selected.get("diagnosisCode"),
        "diagnosis_family": scenario.get("diagnosisFamily"),
        "diagnosis_description": selected.get("diagnosisDescription"),
        "place_of_service_code": selected.get("placeOfServiceCode"),
        "place_of_service_description": selected.get("placeOfService"),
        "charge_amount": selected.get("totalCharge"),
        "allowed_amount": selected.get("allowed"),
        "paid_amount": selected.get("paid"),
        "patient_responsibility": selected.get("patientResp"),
        "adjustment_amount": selected.get("adjustment"),
        "denial_reason": selected.get("denialReason") or None,
        "has_prior_auth": bool(selected.get("priorAuth")),
        "has_referral": bool(selected.get("referral")),
        "adjudicated": bool(selected.get("status") or selected.get("statusCode")),
    }
    forecast = {
        "forecast_context": scenario.get("forecastContext", "retrospective_current_claim"),
        "forecast_label": "Retrospective estimate for current claim",
        "predicted_claim_outcome": {
            "value": scenario.get("predictedOutcome", {}).get("value"),
            "display_value": scenario.get("predictedOutcome", {}).get("displayValue"),
            "probability": scenario.get("predictedOutcome", {}).get("probability"),
        },
        "denial_risk": {
            "probability": denial.get("probability"),
            "percentage": round((denial.get("probability") or 0) * 100, 1),
            "level": str(denial.get("level") or "unknown").lower(),
        },
        "repeat_service_risk": {
            "probability_30d": repeat.get("30"),
            "probability_60d": repeat.get("60"),
            "probability_90d": repeat.get("90"),
            "level": str(scenario.get("repeatRisk", {}).get("level") or "unknown").lower(),
        },
        "predicted_allowed": {
            "value": financial.get("allowed"),
            "low": financial.get("allowedRange", {}).get("low"),
            "high": financial.get("allowedRange", {}).get("high"),
        },
        "predicted_paid": {
            "value": financial.get("paid"),
            "low": financial.get("paidRange", {}).get("low"),
            "high": financial.get("paidRange", {}).get("high"),
        },
        "predicted_patient_responsibility": {
            "value": financial.get("patientResp"),
            "low": financial.get("patientRespRange", {}).get("low"),
            "high": financial.get("patientRespRange", {}).get("high"),
        },
        "predicted_adjustment": {
            "value": financial.get("adjustment"),
            "low": financial.get("adjustmentRange", {}).get("low"),
            "high": financial.get("adjustmentRange", {}).get("high"),
        },
        "potentially_avoidable_spend": {
            "value": scenario.get("avoidableSpend") if avoidable_available else None,
            "available": avoidable_available,
            "reason": None if avoidable_available else unavailable_reason,
            "savings_phase": scenario.get("bestSavingsPhase") if avoidable_available else None,
        },
        "confidence": {
            "score": confidence.get("score", 0),
            "percentage": confidence.get("percentage", 0),
            "level": str(confidence.get("level") or "low").lower(),
            "explanation": confidence.get("explanation"),
            "factors": confidence.get("factors", {}),
            "peer_sample_size": scenario.get("peerClaimCount", 0),
            "peer_episode_count": scenario.get("peerCount", 0),
            "prediction_method": "hierarchical_deterministic_peer_forecast",
            "model_version": scenario.get("method", MODEL_VERSION),
        },
    }
    peer_statistics = financial.get("peerStatistics", {})
    prediction_basis = {
        "peer_claims_used": scenario.get("peerClaimCount", 0),
        "peer_episodes_used": scenario.get("peerCount", 0),
        "matching_level": financial.get("peerHierarchy"),
        "fallback_level": scenario.get("peerMatchLevel", 5),
        "fallback_explanation": "Broader historical matching was required." if scenario.get("peerMatchLevel", 5) > 1 else "No broad fallback was required.",
        "historical_peer_denial_rate": denial.get("probability"),
        "median_allowed_rate": peer_statistics.get("medianAllowedRate"),
        "median_paid_to_allowed_rate": peer_statistics.get("medianPaidToAllowedRate"),
        "median_patient_to_allowed_rate": peer_statistics.get("medianPatientToAllowedRate"),
    }
    evidence_used = [{
        "evidence_type": "claim",
        "claim_id": claim.get("claimId"),
        "service_date": claim.get("dos"),
        "cpt_code": claim.get("cptCode"),
        "cpt_description": claim.get("cptDescription"),
        "diagnosis_family": scenario.get("diagnosisFamily"),
        "diagnosis_description": claim.get("diagnosisDescription"),
        "place_of_service_code": claim.get("placeOfServiceCode"),
        "place_of_service_description": claim.get("placeOfService"),
        "claim_status": claim.get("status"),
        "actual_allowed": claim.get("allowed"),
        "actual_paid": claim.get("paid"),
        "actual_patient_responsibility": claim.get("patientResp"),
        "actual_adjustment": claim.get("adjustment"),
        "prediction_fields_used": ["Payer", "Billing provider", "CPT", "Diagnosis family", "Place of service", "Earlier peer outcomes"],
    } for claim in scenario.get("claims", [])]
    exact_model_output = {
        "predicted_claim_outcome": forecast["predicted_claim_outcome"]["value"],
        "predicted_claim_outcome_probability": forecast["predicted_claim_outcome"]["probability"],
        "denial_probability": forecast["denial_risk"]["probability"],
        "repeat_probability_30d": forecast["repeat_service_risk"]["probability_30d"],
        "repeat_probability_60d": forecast["repeat_service_risk"]["probability_60d"],
        "repeat_probability_90d": forecast["repeat_service_risk"]["probability_90d"],
        "predicted_allowed": forecast["predicted_allowed"]["value"],
        "predicted_allowed_low": forecast["predicted_allowed"]["low"],
        "predicted_allowed_high": forecast["predicted_allowed"]["high"],
        "predicted_paid": forecast["predicted_paid"]["value"],
        "predicted_paid_low": forecast["predicted_paid"]["low"],
        "predicted_paid_high": forecast["predicted_paid"]["high"],
        "predicted_patient_responsibility": forecast["predicted_patient_responsibility"]["value"],
        "predicted_patient_responsibility_low": forecast["predicted_patient_responsibility"]["low"],
        "predicted_patient_responsibility_high": forecast["predicted_patient_responsibility"]["high"],
        "predicted_adjustment": forecast["predicted_adjustment"]["value"],
        "predicted_adjustment_low": forecast["predicted_adjustment"]["low"],
        "predicted_adjustment_high": forecast["predicted_adjustment"]["high"],
        "potentially_avoidable_spend": forecast["potentially_avoidable_spend"]["value"],
        "confidence": forecast["confidence"]["score"],
        "peer_sample_size": forecast["confidence"]["peer_sample_size"],
        "prediction_method": forecast["confidence"]["prediction_method"],
        "model_version": forecast["confidence"]["model_version"],
    }
    return {
        "claim_id": selected.get("claimId"),
        "episode_id": scenario.get("episodeId"),
        "member_reference": scenario.get("memberReference"),
        "actual_claim_facts": actual_facts,
        "forecast": forecast,
        "prediction_basis": prediction_basis,
        "risk_drivers": [{
            "title": item.get("title"),
            "value": item.get("value"),
            "risk_direction": item.get("riskDirection"),
            "source_type": item.get("sourceType"),
            "reason": item.get("reason"),
            "evidence_ids": item.get("evidenceIds", []),
        } for item in scenario.get("structuredRiskDrivers", [])],
        "recommended_actions": scenario.get("structuredRecommendedActions", []),
        "evidence_used": evidence_used,
        "limitations": [
            "This is provider administrative decision support and does not determine medical necessity.",
            "Financial estimates are retrospective estimates for the selected claim because its submitted charge is the forecast basis.",
            "Payer authorization and referral requirements are not present in the claims dataset and must be verified separately.",
        ],
        "exact_model_output": exact_model_output,
    }
