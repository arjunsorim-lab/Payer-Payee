"""Deterministic provider-side claim episode forecasting.

This module is intentionally independent of the LLM. It validates canonical
claim documents, assigns every valid claim to exactly one 90-day episode, and
uses only earlier adjudicated peer claims for financial and risk estimates.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
import os
from statistics import median


MODEL_VERSION = "provider-money-forecast-v3.3"
CALCULATION_VERSION = "provider-savings-v5"
DEFAULT_WINDOW_DAYS = 90
DEFAULT_MIN_PEERS = 5
REPEAT_MEDIUM_THRESHOLD = .34
CURRENT_OPPORTUNITY_MINIMUM = 1.0
_BATCH_CACHE = {}
_PREPARED_CACHE = {}


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


def _claim_order(claim):
    return claim.get("_serviceDate") or _parse_date(claim.get("dos")), str(claim.get("claimId") or claim.get("number") or "")


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


PEER_MATCH_LEVELS = (
    ("payer + provider + CPT + diagnosis family + place of service + units", ("payer", "provider", "cpt", "diagnosis", "pos", "units")),
    ("payer + provider + CPT + diagnosis family + place of service", ("payer", "provider", "cpt", "diagnosis", "pos")),
    ("payer + CPT + diagnosis family + place of service", ("payer", "cpt", "diagnosis", "pos")),
    ("payer + CPT + diagnosis family", ("payer", "cpt", "diagnosis")),
    ("CPT + diagnosis family", ("cpt", "diagnosis")),
    ("diagnosis family + place of service", ("diagnosis", "pos")),
    ("diagnosis family", ("diagnosis",)),
    ("global historical baseline", ()),
)


def _match_value(claim, dimension):
    return {
        "payer": claim.get("payerId") or claim.get("payer"),
        "provider": claim.get("billingProviderNpi") or claim.get("billingProvider"),
        "cpt": claim.get("cptCode"),
        "diagnosis": claim.get("diagnosisFamily") or _diagnosis_family(claim.get("diagnosisCode")),
        "pos": claim.get("placeOfServiceCode"),
        "units": claim.get("units"),
    }[dimension]


def _eligible_historical_claims(target, all_episodes, exclude_member=False):
    anchor = target["claims"][-1]
    cutoff = _claim_order(anchor)
    rows = {
        claim["claimId"]: claim
        for episode in all_episodes
        for claim in episode["claims"]
        if _claim_order(claim) < cutoff and (not exclude_member or claim.get("memberId") != target.get("memberId"))
    }
    return sorted(rows.values(), key=_claim_order)


def _matching_peers(target, all_episodes, min_peers):
    anchor = target["claims"][-1]
    eligible = _eligible_historical_claims(target, all_episodes, exclude_member=True)
    claim_to_episode = {
        claim["claimId"]: episode["episodeId"]
        for episode in all_episodes
        for claim in episode["claims"]
    }
    for level, (label, dimensions) in enumerate(PEER_MATCH_LEVELS):
        peers = [claim for claim in eligible if all(_match_value(claim, key) == _match_value(anchor, key) for key in dimensions)]
        if len(peers) >= min_peers or (level == len(PEER_MATCH_LEVELS) - 1 and peers):
            episode_count = len({claim_to_episode.get(claim["claimId"]) for claim in peers})
            return peers, episode_count, label, level
    return [], 0, "insufficient prior history", len(PEER_MATCH_LEVELS)


def _longitudinal_financial_rows(history, anchor):
    """Select the most specific usable adjudicated history for this member."""
    payer = anchor.get("payerId") or anchor.get("payer")
    exact = [row for row in history if row.get("cptCode") == anchor.get("cptCode") and (row.get("payerId") or row.get("payer")) == payer]
    if exact:
        return exact, "member payer+CPT history"
    same_cpt = [row for row in history if row.get("cptCode") == anchor.get("cptCode")]
    if same_cpt:
        return same_cpt, "member CPT history"
    same_family = [row for row in history if row.get("procedureFamily") == anchor.get("procedureFamily")]
    return (same_family, "member procedure-family history") if same_family else ([], "no matching member financial history")


def _blend_weights(member_count, external_count, prior_strength=10):
    if member_count and external_count:
        local = member_count / (member_count + prior_strength)
        return {"local": round(local, 4), "external": round(1 - local, 4)}
    if member_count:
        return {"local": 1.0, "external": 0.0}
    if external_count:
        return {"local": 0.0, "external": 1.0}
    return {"local": 0.0, "external": 0.0}


def _blended_rate(peer_rates, member_rates, prior_strength=10):
    weights = _blend_weights(len(member_rates), len(peer_rates), prior_strength)
    if not peer_rates and not member_rates:
        return None, None, None, weights
    if not peer_rates:
        return median(member_rates), _quantile(member_rates, .25), _quantile(member_rates, .75), weights
    peer_mid, peer_low, peer_high = median(peer_rates), _quantile(peer_rates, .25), _quantile(peer_rates, .75)
    if not member_rates:
        return peer_mid, peer_low, peer_high, weights
    weight = weights["local"]
    return (
        peer_mid * (1 - weight) + median(member_rates) * weight,
        peer_low * (1 - weight) + _quantile(member_rates, .25) * weight,
        peer_high * (1 - weight) + _quantile(member_rates, .75) * weight,
        weights,
    )


def _money_forecast(episode, peer_claims, hierarchy, member_history=None):
    anchor = episode["claims"][-1]
    target_charge = anchor["totalCharge"]
    rows = [claim for claim in peer_claims if claim["totalCharge"] > 0]
    member_rows, member_hierarchy = _longitudinal_financial_rows(member_history or [], anchor)
    member_rows = [claim for claim in member_rows if claim["totalCharge"] > 0]
    allowed_rates = [claim["allowed"] / claim["totalCharge"] for claim in rows]
    paid_rates = [claim["paid"] / claim["allowed"] for claim in rows if claim["allowed"] > 0]
    patient_rates = [claim["patientResp"] / claim["allowed"] for claim in rows if claim["allowed"] > 0]
    adjustment_rates = [claim["adjustment"] / claim["totalCharge"] for claim in rows]
    member_allowed_rates = [claim["allowed"] / claim["totalCharge"] for claim in member_rows]
    member_paid_rates = [claim["paid"] / claim["allowed"] for claim in member_rows if claim["allowed"] > 0]
    member_patient_rates = [claim["patientResp"] / claim["allowed"] for claim in member_rows if claim["allowed"] > 0]
    member_adjustment_rates = [claim["adjustment"] / claim["totalCharge"] for claim in member_rows]
    allowed_rate, allowed_low, allowed_high, allowed_weights = _blended_rate(allowed_rates, member_allowed_rates)
    paid_rate, paid_low, paid_high, paid_weights = _blended_rate(paid_rates, member_paid_rates)
    patient_rate, patient_low, patient_high, patient_weights = _blended_rate(patient_rates, member_patient_rates)
    adjustment_rate, adjustment_low, adjustment_high, adjustment_weights = _blended_rate(adjustment_rates, member_adjustment_rates)
    allowed_mid = round(target_charge * allowed_rate, 2) if allowed_rate is not None else 0
    allowed_range = {"low": round(target_charge * allowed_low, 2), "high": round(target_charge * allowed_high, 2)} if allowed_low is not None else {"low": None, "high": None}
    paid_mid = round(allowed_mid * paid_rate, 2) if paid_rate is not None else 0
    paid_range = {"low": round(allowed_mid * paid_low, 2), "high": round(allowed_mid * paid_high, 2)} if paid_low is not None else {"low": None, "high": None}
    patient_mid = round(allowed_mid * patient_rate, 2) if patient_rate is not None else 0
    patient_range = {"low": round(allowed_mid * patient_low, 2), "high": round(allowed_mid * patient_high, 2)} if patient_low is not None else {"low": None, "high": None}
    adjustment_mid = round(target_charge * adjustment_rate, 2) if adjustment_rate is not None else max(round(target_charge - allowed_mid, 2), 0)
    adjustment_range = {"low": round(target_charge * adjustment_low, 2), "high": round(target_charge * adjustment_high, 2)} if adjustment_low is not None else {"low": None, "high": None}
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
        "longitudinalClaimCount": len(member_rows),
        "longitudinalHierarchy": member_hierarchy,
        "metricBasis": {
            "predicted_allowed": {"local_sample_size": len(member_allowed_rates), "external_sample_size": len(allowed_rates), "blend_weights": allowed_weights},
            "predicted_paid": {"local_sample_size": len(member_paid_rates), "external_sample_size": len(paid_rates), "blend_weights": paid_weights},
            "predicted_patient_responsibility": {"local_sample_size": len(member_patient_rates), "external_sample_size": len(patient_rates), "blend_weights": patient_weights},
            "predicted_adjustment": {"local_sample_size": len(member_adjustment_rates), "external_sample_size": len(adjustment_rates), "blend_weights": adjustment_weights},
        },
        "peerStatistics": {
            "medianAllowedRate": round(median(allowed_rates), 4) if allowed_rates else None,
            "medianPaidToAllowedRate": round(median(paid_rates), 4) if paid_rates else None,
            "medianPatientToAllowedRate": round(median(patient_rates), 4) if patient_rates else None,
            "medianAdjustmentRate": round(median(adjustment_rates), 4) if adjustment_rates else None,
            "lowerQuartileAdjustmentRate": round(_quantile(adjustment_rates, .25), 4) if adjustment_rates else None,
            "allowedRateIqr": round(_quantile(allowed_rates, .75) - _quantile(allowed_rates, .25), 4) if allowed_rates else None,
        },
    }


def _prior_member_history(episode, all_episodes):
    anchor = episode["claims"][-1]
    cutoff = _claim_order(anchor)
    rows = {
        claim["claimId"]: claim
        for item in all_episodes if item["memberId"] == episode["memberId"]
        for claim in item["claims"]
        if _claim_order(claim) < cutoff
    }
    return sorted(rows.values(), key=_claim_order)


def _longitudinal_features(episode, history):
    anchor = episode["claims"][-1]
    anchor_date = anchor["_serviceDate"]
    related = [row for row in history if row.get("diagnosisFamily") == episode["diagnosisFamily"]]
    same_cpt = [row for row in history if row.get("cptCode") == anchor.get("cptCode")]
    return {
        "priorClaimCount": len(history),
        "priorDeniedCount": sum(_is_denied(row) for row in history),
        "priorRelatedClaimCount": len(related),
        "priorSameCptCount": len(same_cpt),
        "prior90DayClaimCount": sum((anchor_date - row["_serviceDate"]).days <= 90 for row in history),
        "prior365DayClaimCount": sum((anchor_date - row["_serviceDate"]).days <= 365 for row in history),
        "priorRelatedClaimIds": [row["claimId"] for row in related[-10:]],
        "priorSameCptClaimIds": [row["claimId"] for row in same_cpt[-10:]],
        "priorPaymentTrend": (
            "increasing" if len(history) >= 2 and history[-1].get("paid", 0) > history[-2].get("paid", 0)
            else "decreasing" if len(history) >= 2 and history[-1].get("paid", 0) < history[-2].get("paid", 0)
            else "stable" if len(history) >= 2 else "insufficient history"
        ),
    }


def _related_to_anchor(claim, anchor):
    return (
        (claim.get("diagnosisFamily") or _diagnosis_family(claim.get("diagnosisCode"))) == (anchor.get("diagnosisFamily") or _diagnosis_family(anchor.get("diagnosisCode")))
        and (claim.get("procedureFamily") or _procedure_family(claim.get("cptCode"))) == (anchor.get("procedureFamily") or _procedure_family(anchor.get("cptCode")))
    )


def _recurrence_observations(claims, cutoff, horizon_days, anchor=None):
    grouped = defaultdict(list)
    for claim in claims:
        cpt = claim.get("cptCode")
        service_date = claim.get("_serviceDate") or _parse_date(claim.get("dos"))
        if not cpt or not service_date or service_date >= cutoff or (anchor and not _related_to_anchor(claim, anchor)):
            continue
        grouped[(claim.get("memberId"), claim.get("diagnosisFamily") or _diagnosis_family(claim.get("diagnosisCode")), claim.get("procedureFamily") or _procedure_family(cpt))].append(service_date)
    successes = trials = 0
    mature_cutoff = cutoff - timedelta(days=horizon_days)
    for dates in grouped.values():
        dates = sorted(set(dates))
        for index, service_date in enumerate(dates):
            if service_date > mature_cutoff:
                continue
            trials += 1
            if index + 1 < len(dates) and (dates[index + 1] - service_date).days <= horizon_days:
                successes += 1
    return successes, trials


def _repeat_forecast(anchor, historical_claims, member_history):
    cutoff = anchor["_serviceDate"]
    member_eligible_claims = [claim for claim in member_history if _related_to_anchor(claim, anchor) and claim["_serviceDate"] < cutoff]
    peer_eligible_claims = [claim for claim in historical_claims if _related_to_anchor(claim, anchor) and claim["_serviceDate"] < cutoff]
    probabilities = {}
    basis = {}
    previous = 0.0
    for horizon in (30, 60, 90):
        peer_successes, peer_trials = _recurrence_observations(historical_claims, cutoff, horizon, anchor)
        match_level = "peer diagnosis + CPT family"
        member_successes, member_trials = _recurrence_observations(member_history, cutoff, horizon, anchor)
        peer_rate = peer_successes / peer_trials if peer_trials else None
        prior_strength = 10
        if peer_rate is not None and member_trials:
            probability = (member_successes + peer_rate * prior_strength) / (member_trials + prior_strength)
        elif peer_rate is not None:
            probability = peer_rate
        elif member_trials:
            probability = member_successes / member_trials
        else:
            probability = 0.0
        probability = max(previous, min(probability, .95))
        previous = probability
        probabilities[str(horizon)] = round(probability, 3)
        basis[str(horizon)] = {
            "eligible_historical_claims": len(member_eligible_claims),
            "eligible_external_claims": len(peer_eligible_claims),
            "member_successes": member_successes,
            "member_trials": member_trials,
            "peer_successes": peer_successes,
            "peer_trials": peer_trials,
            "local_rate": round(member_successes / member_trials, 4) if member_trials else None,
            "external_rate": round(peer_successes / peer_trials, 4) if peer_trials else None,
            "blend_weights": _blend_weights(member_trials, peer_trials, prior_strength),
            "eligible_prior_index_claim_count": member_trials,
            "recurring_prior_index_claim_count": member_successes,
            "external_eligible_index_claim_count": peer_trials,
            "external_recurring_index_claim_count": peer_successes,
            "local_numerator": member_successes,
            "local_denominator": member_trials,
            "external_numerator": peer_successes,
            "external_denominator": peer_trials,
            "final_blended_rate": round(probability, 3),
            "peer_match_level": match_level,
            "prediction_cutoff_date": cutoff.isoformat(),
            "filters_used": [
                "service date before prediction cutoff",
                "same member for local history",
                f"diagnosis family {_diagnosis_family(anchor.get('diagnosisCode'))}",
                f"procedure family {_procedure_family(anchor.get('cptCode'))}",
                f"eligible index claim has a complete {horizon}-day follow-up window",
                f"next related claim occurs within {horizon} days",
            ],
            "exact_filtering_rule": (
                f"Claims before {cutoff.isoformat()} with diagnosis family "
                f"{_diagnosis_family(anchor.get('diagnosisCode'))} and procedure family "
                f"{_procedure_family(anchor.get('cptCode'))}; each eligible index claim must have a complete "
                f"{horizon}-day follow-up window, and recurrence means its next related claim occurs within {horizon} days."
            ),
            "local_evidence_statement": (
                f"{member_successes} of {member_trials} eligible historical index claim(s) had a related recurrence within {horizon} days."
            ),
            "external_evidence_statement": (
                f"{peer_successes} of {peer_trials} eligible external index claim(s) had a related recurrence within {horizon} days."
            ),
            "available": bool(member_trials or peer_trials),
            "reason": None if (member_trials or peer_trials) else "No eligible related index claims had a complete follow-up window before the cutoff date.",
        }
    return probabilities, basis


def _rate(rows):
    return round(sum(_is_denied(row) for row in rows) / len(rows), 4) if rows else None


def _denial_forecast(peer_claims, historical_claims, member_history, anchor):
    peer_denials = sum(_is_denied(claim) for claim in peer_claims)
    peer_rate = (peer_denials + 1) / (len(peer_claims) + 4) if peer_claims else .25
    payer = anchor.get("payerId") or anchor.get("payer")
    provider = anchor.get("billingProviderNpi") or anchor.get("billingProvider")
    payer_rows = [row for row in historical_claims if (row.get("payerId") or row.get("payer")) == payer]
    provider_rows = [row for row in historical_claims if (row.get("billingProviderNpi") or row.get("billingProvider")) == provider]
    cpt_diagnosis_rows = [row for row in historical_claims if row.get("cptCode") == anchor.get("cptCode") and (row.get("diagnosisFamily") or _diagnosis_family(row.get("diagnosisCode"))) == (anchor.get("diagnosisFamily") or _diagnosis_family(anchor.get("diagnosisCode")))]
    member_same_cpt = [row for row in member_history if row.get("cptCode") == anchor.get("cptCode")]
    relevant = [row for row in member_history if row.get("cptCode") == anchor.get("cptCode") and (row.get("payerId") or row.get("payer")) == payer]
    if len(relevant) < 5:
        relevant = [row for row in member_history if row.get("cptCode") == anchor.get("cptCode")]
    if len(relevant) < 5:
        relevant = member_history
    member_denials = sum(_is_denied(claim) for claim in relevant)
    prior_strength = 20
    probability = (member_denials + peer_rate * prior_strength) / (len(relevant) + prior_strength)
    weights = _blend_weights(len(relevant), len(peer_claims), prior_strength)
    return round(probability, 3), {
        "peer_rate": round(peer_rate, 3),
        "external_peer_denial_rate": _rate(peer_claims),
        "member_historical_denial_rate": _rate(member_history),
        "member_same_cpt_denial_rate": _rate(member_same_cpt),
        "payer_denial_rate": _rate(payer_rows),
        "provider_denial_rate": _rate(provider_rows),
        "cpt_diagnosis_denial_rate": _rate(cpt_diagnosis_rows),
        "member_claims": len(relevant),
        "member_denials": member_denials,
        "external_sample_size": len(peer_claims),
        "member_sample_size": len(relevant),
        "payer_sample_size": len(payer_rows),
        "provider_sample_size": len(provider_rows),
        "cpt_diagnosis_sample_size": len(cpt_diagnosis_rows),
        "prior_authorization_present": bool(anchor.get("priorAuth")),
        "referral_present": bool(anchor.get("referral")),
        "blend_weights": weights,
        "prior_strength": prior_strength,
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


def _historical_validation(peer_claims, financial, denial_probability, repeat_probability, anchor, recurrence_claims=None):
    metrics = {}
    rate_specs = {
        "allowed": ("totalCharge", "allowed"),
        "paid": ("allowed", "paid"),
    }
    ordered = sorted(peer_claims, key=_claim_order)
    coverage_results = []
    for name, (base_field, actual_field) in rate_specs.items():
        prior_rates = []
        errors = []
        for row in ordered:
            base = row.get(base_field, 0)
            if base <= 0:
                continue
            actual_rate = row.get(actual_field, 0) / base
            if len(prior_rates) >= 3:
                prediction = median(prior_rates)
                errors.append(abs(base * prediction - row.get(actual_field, 0)))
                if name == "allowed":
                    coverage_results.append(_quantile(prior_rates, .25) <= actual_rate <= _quantile(prior_rates, .75))
            prior_rates.append(actual_rate)
        metrics[name] = {"mae": round(sum(errors) / len(errors), 2) if errors else None, "sample_size": len(errors)}
    coverage = sum(coverage_results) / len(coverage_results) if coverage_results else None
    prior_outcomes = []
    brier_values = []
    for row in ordered:
        if len(prior_outcomes) >= 3:
            predicted = (sum(prior_outcomes) + 1) / (len(prior_outcomes) + 4)
            brier_values.append((predicted - (1 if _is_denied(row) else 0)) ** 2)
        prior_outcomes.append(1 if _is_denied(row) else 0)
    brier = sum(brier_values) / len(brier_values) if brier_values else None
    recurrence_successes, recurrence_trials = _recurrence_observations(recurrence_claims or peer_claims, anchor["_serviceDate"], 90, anchor)
    recurrence_brier = None
    if recurrence_trials:
        observed_rate = recurrence_successes / recurrence_trials
        recurrence_brier = (repeat_probability - observed_rate) ** 2
    return {
        "financial_mae": metrics,
        "prediction_interval_coverage": round(coverage, 4) if coverage is not None else None,
        "outcome_brier_score": round(brier, 4) if brier is not None else None,
        "recurrence_brier_score": round(recurrence_brier, 4) if recurrence_brier is not None else None,
        "recurrence_validation_intervals": recurrence_trials,
    }


def _confidence_details(peer_claims, match_level, selected_claim, financial, longitudinal, validation):
    required_fields = ("payerId", "billingProviderNpi", "cptCode", "diagnosisCode", "placeOfServiceCode", "totalCharge", "dos")
    completeness = sum(selected_claim.get(field) not in (None, "") for field in required_fields) / len(required_fields)
    sample_factor = min(len(peer_claims) / 100, 1)
    specificity_factor = max(.1, 1 - min(match_level, 7) * .12)
    iqr = financial["peerStatistics"].get("allowedRateIqr")
    variance_factor = 0 if iqr is None else max(0, 1 - min(iqr / .5, 1))
    longitudinal_factor = min(longitudinal.get("priorClaimCount", 0) / 20, 1)
    allowed_mae = validation.get("financial_mae", {}).get("allowed", {}).get("mae")
    charge = max(float(selected_claim.get("totalCharge") or 0), 1)
    mae_factor = max(0, 1 - min((allowed_mae or charge) / charge, 1))
    coverage = validation.get("prediction_interval_coverage")
    coverage_factor = max(0, 1 - abs((coverage if coverage is not None else 0) - .5) / .5)
    brier = validation.get("outcome_brier_score")
    outcome_factor = max(0, 1 - (brier if brier is not None else 1))
    recurrence_brier = validation.get("recurrence_brier_score")
    recurrence_factor = max(0, 1 - (recurrence_brier if recurrence_brier is not None else 1))
    score = round((sample_factor * .16 + specificity_factor * .17 + completeness * .12 + variance_factor * .12 + longitudinal_factor * .12 + mae_factor * .12 + coverage_factor * .07 + outcome_factor * .07 + recurrence_factor * .05) * 100)
    level = "High" if score >= 80 else "Medium" if score >= 55 else "Low"
    if level == "High":
        explanation = "Large peer sample, specific matching dimensions, complete claim fields, and stable peer allowed rates."
    elif level == "Medium":
        explanation = "Adequate peer evidence, but some matching dimensions required fallback or peer amounts show wider variation."
    else:
        explanation = "A small peer group, broad fallback population, incomplete claim fields, or high peer variation reduced confidence."
    drivers = [name for name, value in {
        "adequate external sample": sample_factor,
        "specific peer match": specificity_factor,
        "complete prediction inputs": completeness,
        "stable peer financial rates": variance_factor,
        "longitudinal member evidence": longitudinal_factor,
        "low historical financial error": mae_factor,
        "outcome calibration": outcome_factor,
        "recurrence calibration": recurrence_factor,
    }.items() if value >= .7]
    penalties = [name for name, value in {
        "small external sample": sample_factor,
        "broad fallback": specificity_factor,
        "incomplete prediction inputs": completeness,
        "variable peer financial rates": variance_factor,
        "limited member history": longitudinal_factor,
        "high historical financial error": mae_factor,
        "limited outcome calibration": outcome_factor,
        "limited recurrence calibration": recurrence_factor,
    }.items() if value < .45]
    return {
        "score": score / 100,
        "percentage": score,
        "level": level,
        "explanation": explanation,
        "drivers": drivers,
        "penalties": penalties,
        "factors": {
            "peerSample": round(sample_factor, 3),
            "matchSpecificity": round(specificity_factor, 3),
            "dataCompleteness": round(completeness, 3),
            "peerAmountStability": round(variance_factor, 3),
            "longitudinalEvidence": round(longitudinal_factor, 3),
            "historicalMae": round(mae_factor, 3),
            "intervalCoverage": round(coverage_factor, 3),
            "outcomeCalibration": round(outcome_factor, 3),
            "recurrenceCalibration": round(recurrence_factor, 3),
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


def _recovery_peer_basis(anchor, historical_claims, member_history, min_peers):
    """Select close, earlier adjudicated rows for retrospective recovery checks."""
    dimensions = (
        ("same member + same CPT + same diagnosis family", ("cpt", "diagnosis"), member_history),
        ("same member + same CPT", ("cpt",), member_history),
        ("payer + CPT + diagnosis family + place of service", ("payer", "cpt", "diagnosis", "pos"), historical_claims),
        ("payer + CPT + diagnosis family", ("payer", "cpt", "diagnosis"), historical_claims),
        ("CPT + diagnosis family", ("cpt", "diagnosis"), historical_claims),
    )
    rows = []
    label = "insufficient evidence"
    level = 6
    for index, (candidate_label, keys, candidates) in enumerate(dimensions, 1):
        matched = [row for row in candidates if all(_match_value(row, key) == _match_value(anchor, key) for key in keys)]
        if len(matched) >= min_peers:
            rows, label, level = matched, candidate_label, index
            break
    allowed_rates = [row["allowed"] / row["totalCharge"] for row in rows if row.get("totalCharge", 0) > 0]
    paid_rates = [row["paid"] / row["allowed"] for row in rows if row.get("allowed", 0) > 0]
    adjustment_rates = [row["adjustment"] / row["totalCharge"] for row in rows if row.get("totalCharge", 0) > 0]
    patient_rates = [row["patientResp"] / row["allowed"] for row in rows if row.get("allowed", 0) > 0]
    return {
        "available": bool(rows),
        "match_level": label,
        "match_level_number": level,
        "sample_size": len(rows),
        "minimum_sample_size": min_peers,
        "claim_ids": [row["claimId"] for row in rows],
        "prediction_cutoff_date": anchor["_serviceDate"].isoformat(),
        "matching_dimensions": (["member"] + list(dimensions[level - 1][1])) if rows and level <= 2 else list(dimensions[level - 1][1]) if rows else [],
        "median_allowed_rate": round(median(allowed_rates), 8) if allowed_rates else None,
        "median_paid_to_allowed_rate": round(median(paid_rates), 8) if paid_rates else None,
        "median_adjustment_rate": round(median(adjustment_rates), 8) if adjustment_rates else None,
        "median_patient_share_rate": round(median(patient_rates), 8) if patient_rates else None,
        "metric_sample_sizes": {
            "allowed_rate": len(allowed_rates),
            "paid_to_allowed_rate": len(paid_rates),
            "adjustment_rate": len(adjustment_rates),
            "patient_share_rate": len(patient_rates),
        },
    }


def _avoidable_spend_assessment(episode, all_episodes, anchor, min_peers):
    """Compare a repeated current episode only with earlier lower-repeat episodes."""
    enrichment = anchor.get("syntheticEnrichment") or {}
    synthetic_episode_id = str(enrichment.get("Episode_ID") or "").strip()
    if synthetic_episode_id:
        cutoff = _claim_order(anchor)
        all_rows = [row for candidate in all_episodes for row in candidate["claims"]]
        current_rows = [
            row for row in all_rows
            if row.get("memberId") == anchor.get("memberId")
            and str((row.get("syntheticEnrichment") or {}).get("Episode_ID") or "").strip() == synthetic_episode_id
            and str((row.get("syntheticEnrichment") or {}).get("Related_Claim_Flag") or "").strip().lower() == "yes"
            and _claim_order(row) <= cutoff
        ]
        current_repeats = max(len(current_rows) - 1, 0)
        unresolved = any(
            str((row.get("syntheticEnrichment") or {}).get("Condition_Resolved") or "").strip().lower() in {"no", "false"}
            or str((row.get("syntheticEnrichment") or {}).get("Treatment_Outcome") or "").strip().lower() in {"not resolved", "unresolved", "persistent"}
            for row in current_rows
        )
        repeat_reasons = [str((row.get("syntheticEnrichment") or {}).get("Repeat_Visit_Reason") or "").strip().lower() for row in current_rows]
        repeat_reason = next((value for value in repeat_reasons if value and value not in {"planned follow-up", "not applicable", "routine follow-up"}), str(enrichment.get("Repeat_Visit_Reason") or "").strip().lower())
        non_planned_repeat = bool(repeat_reason and repeat_reason not in {"planned follow-up", "not applicable", "routine follow-up"})
        grouped = defaultdict(list)
        for row in all_rows:
            row_enrichment = row.get("syntheticEnrichment") or {}
            episode_id = str(row_enrichment.get("Episode_ID") or "").strip()
            if (
                not episode_id
                or episode_id == synthetic_episode_id
                or _claim_order(row) >= cutoff
                or (row.get("diagnosisFamily") or _diagnosis_family(row.get("diagnosisCode"))) != (anchor.get("diagnosisFamily") or _diagnosis_family(anchor.get("diagnosisCode")))
                or (row.get("procedureFamily") or _procedure_family(row.get("cptCode"))) != (anchor.get("procedureFamily") or _procedure_family(anchor.get("cptCode")))
                or _match_value(row, "payer") != _match_value(anchor, "payer")
                or str(row_enrichment.get("Related_Claim_Flag") or "").strip().lower() != "yes"
            ):
                continue
            grouped[episode_id].append(row)
        comparable = [rows for rows in grouped.values() if rows and len(rows) < len(current_rows)]
        comparable_costs = [sum(row.get("allowed", 0) for row in rows) for rows in comparable]
        comparable_ids = [episode_id for episode_id, rows in grouped.items() if rows and len(rows) < len(current_rows)]
        supported = len(current_rows) >= 2 and unresolved and non_planned_repeat and len(comparable_costs) >= min_peers
        observed_cost = round(sum(row.get("allowed", 0) for row in current_rows), 2)
        comparison_cost = round(median(comparable_costs), 2) if supported else None
        amount = round(max(observed_cost - comparison_cost, 0), 2) if supported else None
        reasons = []
        if len(current_rows) < 2:
            reasons.append("The synthetic episode does not contain at least two related claims before the cutoff date.")
        if not unresolved:
            reasons.append("The synthetic outcome fields do not indicate an unresolved episode.")
        if not non_planned_repeat:
            reasons.append("The synthetic repeat reason is planned follow-up or not applicable.")
        if len(comparable_costs) < min_peers:
            reasons.append(f"Only {len(comparable_costs)} comparable lower-repeat episode(s) were available; at least {min_peers} are required.")
        return {
            "available": supported,
            "amount": amount,
            "reason": None if supported else " ".join(reasons),
            "observed_or_predicted_episode_cost": observed_cost,
            "median_lower_repeat_episode_cost": comparison_cost,
            "comparison_episode_count": len(comparable_costs),
            "comparison_episode_ids": comparable_ids[:20],
            "related_claim_count": len(current_rows),
            "repeated_related_service_count": current_repeats,
            "prediction_cutoff_date": anchor["_serviceDate"].isoformat(),
            "synthetic_demo": True,
            "episode_id": synthetic_episode_id,
            "condition_unresolved": unresolved,
            "repeat_reason": enrichment.get("Repeat_Visit_Reason"),
        }

    current_rows = [row for row in episode["claims"] if _related_to_anchor(row, anchor)]
    current_repeats = max(len(current_rows) - len({row.get("cptCode") for row in current_rows}), 0)
    comparable_costs = []
    comparable_ids = []
    for candidate in all_episodes:
        if candidate.get("memberId") == episode.get("memberId") or candidate.get("diagnosisFamily") != episode.get("diagnosisFamily"):
            continue
        rows = [row for row in candidate["claims"] if _claim_order(row) < _claim_order(anchor) and _related_to_anchor(row, anchor)]
        repeats = max(len(rows) - len({row.get("cptCode") for row in rows}), 0)
        if rows and repeats < current_repeats:
            comparable_costs.append(sum(row.get("allowed", 0) for row in rows))
            comparable_ids.append(candidate["episodeId"])
    supported = len(current_rows) >= 2 and current_repeats > 0 and len(comparable_costs) >= min_peers
    observed_cost = round(sum(row.get("allowed", 0) for row in current_rows), 2)
    comparison_cost = round(median(comparable_costs), 2) if supported else None
    amount = round(max(observed_cost - comparison_cost, 0), 2) if supported else None
    reasons = []
    if len(current_rows) < 2:
        reasons.append("The selected episode does not contain at least two related claims.")
    if current_repeats == 0:
        reasons.append("No repeated related CPT service is confirmed in the selected episode.")
    if len(comparable_costs) < min_peers:
        reasons.append(f"Only {len(comparable_costs)} comparable lower-repeat episode(s) were available; at least {min_peers} are required.")
    return {
        "available": supported,
        "amount": amount,
        "reason": None if supported else " ".join(reasons),
        "observed_or_predicted_episode_cost": observed_cost,
        "median_lower_repeat_episode_cost": comparison_cost,
        "comparison_episode_count": len(comparable_costs),
        "comparison_episode_ids": comparable_ids[:20],
        "related_claim_count": len(current_rows),
        "repeated_related_service_count": current_repeats,
        "prediction_cutoff_date": anchor["_serviceDate"].isoformat(),
        "synthetic_demo": False,
    }


def score_episode(episode, all_episodes, min_peers=DEFAULT_MIN_PEERS):
    features = _episode_features(episode)
    member_history = _prior_member_history(episode, all_episodes)
    longitudinal = _longitudinal_features(episode, member_history)
    features.update(longitudinal)
    peer_claims, peer_episode_count, hierarchy, match_level = _matching_peers(episode, all_episodes, min_peers)
    financial = _money_forecast(episode, peer_claims, hierarchy, member_history)
    anchor = episode["claims"][-1]
    historical_claims = _eligible_historical_claims(episode, all_episodes, exclude_member=True)
    recovery_basis = _recovery_peer_basis(anchor, historical_claims, member_history, min_peers)
    denial_probability, denial_basis = _denial_forecast(peer_claims, historical_claims, member_history, anchor)
    repeat, repeat_basis = _repeat_forecast(anchor, historical_claims, member_history)
    repeat_90_basis = repeat_basis.get("90", {})
    avoidable_basis = _avoidable_spend_assessment(episode, all_episodes, anchor, min_peers)
    sufficient_avoidable_evidence = avoidable_basis["available"]
    avoidable = avoidable_basis["amount"] or 0
    historical_validation = _historical_validation(peer_claims, financial, denial_probability, repeat["90"], anchor, historical_claims)
    confidence_detail = _confidence_details(peer_claims, match_level, anchor, financial, longitudinal, historical_validation)
    confidence_score = confidence_detail["percentage"]
    confidence = confidence_detail["level"]
    priority = round(min(100, repeat["90"] * 40 + denial_probability * 25 + min(avoidable / 5000, 1) * 25 + (1 - confidence_score / 100) * 10))
    common_next_cpt = Counter(claim.get("cptCode") for claim in peer_claims if claim.get("cptCode")).most_common(1)
    common_next_pos = Counter(claim.get("placeOfServiceCode") for claim in peer_claims if claim.get("placeOfServiceCode")).most_common(1)
    peer_outcomes = Counter(_display_outcome(claim.get("status")) for claim in peer_claims)
    likely_outcome, likely_outcome_count = peer_outcomes.most_common(1)[0] if peer_outcomes else ("Outcome unavailable", 0)
    outcome_probability = round(likely_outcome_count / len(peer_claims), 3) if peer_claims else None
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
        "denialRisk": {"probability": denial_probability, "score": round(denial_probability * 100), "level": _probability_level(denial_probability), "category": "peer and longitudinal adjudication", "basis": denial_basis},
        "repeatRisk": {"probabilities": repeat, "score": round(repeat["90"] * 100), "level": _probability_level(repeat["90"])},
        "predictedOutcome": {"value": _outcome_key(likely_outcome), "displayValue": likely_outcome, "probability": outcome_probability},
        "nextService": {"cptCode": common_next_cpt[0][0] if common_next_cpt else None, "placeOfServiceCode": common_next_pos[0][0] if common_next_pos else None},
        "avoidableSpend": avoidable,
        "avoidableSpendLabel": "Potentially avoidable repeat-service spend",
        "avoidableSpendSupported": sufficient_avoidable_evidence,
        "avoidableSpendBasis": {
            **avoidable_basis,
            "predicted_repeat_rate": repeat["90"],
            "predicted_repeat_expenditure": round(financial["allowed"] * repeat["90"], 2),
            "member_interval_count": repeat_90_basis.get("member_trials", 0),
            "external_interval_count": repeat_90_basis.get("peer_trials", 0),
        },
        "bestSavingsPhase": "provider follow-up monitoring" if sufficient_avoidable_evidence else "insufficient evidence",
        "priorityScore": priority,
        "confidence": confidence,
        "confidenceScore": confidence_score,
        "confidenceDetail": confidence_detail,
        "peerCount": peer_episode_count,
        "peerClaimCount": len(peer_claims),
        "peerMatchLevel": match_level,
        "recoveryBasis": recovery_basis,
        "longitudinalBasis": {
            **longitudinal,
            "financialHistoryClaimCount": financial.get("longitudinalClaimCount", 0),
            "financialHistoryMatchLevel": financial.get("longitudinalHierarchy"),
            "denial": denial_basis,
            "repeat": repeat_basis,
            "predictionCutoffDate": anchor["_serviceDate"].isoformat(),
        },
        "historicalValidation": historical_validation,
        "forecastContext": "next_related_claim",
        "method": MODEL_VERSION,
        "anchor": _safe_claim(anchor),
        "claims": [_safe_claim(claim) for claim in episode["claims"]],
        "sourceClaimIds": [claim["claimId"] for claim in episode["claims"]],
        "evidence": evidence,
        "structuredRiskDrivers": [
            {
                "title": "Predicted denial probability",
                "value": f"{denial_probability * 100:.1f}%",
                "riskDirection": _probability_level(denial_probability).lower(),
                "sourceType": "model_feature",
                "reason": f"Calculated from {len(peer_claims)} earlier peer claim(s) and {denial_basis['member_claims']} relevant earlier member claim(s).",
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
                "title": "Prior member claims",
                "value": str(longitudinal["priorClaimCount"]),
                "riskDirection": "historical context",
                "sourceType": "longitudinal_feature",
                "reason": f"The engine used {longitudinal['priorClaimCount']} earlier member claim(s), including {longitudinal['priorSameCptCount']} with the same CPT and {longitudinal['priorDeniedCount']} denial(s).",
                "evidenceIds": [episode["episodeId"]],
            },
            {
                "title": "90-day repeat-service probability",
                "value": f"{repeat['90'] * 100:.1f}%",
                "riskDirection": _probability_level(repeat["90"]).lower(),
                "sourceType": "model_output",
                "reason": f"Calculated from mature member and peer recurrence observations for CPT {anchor.get('cptCode') or 'unavailable'}.",
                "evidenceIds": [episode["episodeId"]],
            },
        ],
        "riskDrivers": [
            f"Historical peer denial probability is {denial_probability * 100:.1f}% from {len(peer_claims)} earlier adjudicated peer claim(s).",
            f"The episode contains {features['repeatedServiceCount']} repeated CPT service(s).",
            f"The member has {longitudinal['priorClaimCount']} earlier claim(s), including {longitudinal['priorSameCptCount']} with the same CPT.",
            f"The deterministic 90-day repeat-service probability is {repeat['90'] * 100:.1f}%.",
        ],
        "structuredRecommendedActions": [],
        "recommendedActions": [],
    }


def _dataset_fingerprint(claims, window_days, min_peers):
    source_hashes = sorted({str(claim.get("sourceCsvHash")) for claim in claims if claim.get("sourceCsvHash")})
    digest = sha256(f"{MODEL_VERSION}|{CALCULATION_VERSION}|{window_days}|{min_peers}|{len(claims)}|{'|'.join(source_hashes)}".encode("utf-8"))
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
    source_csv_hashes = sorted({str(claim.get("sourceCsvHash")) for claim in claims if claim.get("sourceCsvHash")})
    if use_cache and fingerprint in _BATCH_CACHE:
        return _BATCH_CACHE[fingerprint]
    valid, validation = validate_claims(claims)
    episodes = build_episodes(valid, window_days)
    scored = [score_episode(episode, episodes, min_peers) for episode in episodes]
    for item in scored:
        item.update({"sourceDatasetHash": fingerprint, "sourceCsvHash": source_csv_hashes[0] if len(source_csv_hashes) == 1 else None, "calculationVersion": CALCULATION_VERSION})
    assigned = [claim_id for item in scored for claim_id in item["sourceClaimIds"]]
    quality = {
        "allValidClaimsAssignedOnce": len(assigned) == len(set(assigned)) == len(valid),
        "assignedClaimCount": len(assigned),
        "episodeCount": len(scored),
        "highPriorityCount": sum(item["priorityScore"] >= 70 for item in scored),
        "lowConfidenceCount": sum(item["confidence"] == "Low" for item in scored),
        "unsupportedAvoidableSpendCount": sum(not item["avoidableSpendSupported"] for item in scored),
    }
    result = scored, {"validation": validation, "quality": quality, "modelVersion": MODEL_VERSION, "calculationVersion": CALCULATION_VERSION, "sourceDatasetHash": fingerprint, "sourceCsvHash": source_csv_hashes[0] if len(source_csv_hashes) == 1 else None, "episodeWindowDays": window_days, "minimumPeerEpisodes": min_peers}
    if use_cache:
        _BATCH_CACHE.clear()
        _BATCH_CACHE[fingerprint] = result
    return result


def _prepare_dataset(claims, window_days, min_peers):
    fingerprint = _dataset_fingerprint(claims, window_days, min_peers)
    source_csv_hashes = sorted({str(claim.get("sourceCsvHash")) for claim in claims if claim.get("sourceCsvHash")})
    cached = _PREPARED_CACHE.get(fingerprint)
    if cached:
        return cached
    valid, validation = validate_claims(claims)
    raw_episodes = build_episodes(valid, window_days)
    assigned = [claim["claimId"] for episode in raw_episodes for claim in episode["claims"]]
    report = {
        "validation": validation,
        "quality": {
            "allValidClaimsAssignedOnce": len(assigned) == len(set(assigned)) == len(valid),
            "assignedClaimCount": len(assigned),
            "episodeCount": len(raw_episodes),
        },
        "modelVersion": MODEL_VERSION,
        "calculationVersion": CALCULATION_VERSION,
        "sourceDatasetHash": fingerprint,
        "sourceCsvHash": source_csv_hashes[0] if len(source_csv_hashes) == 1 else None,
        "episodeWindowDays": window_days,
        "minimumPeerClaims": min_peers,
    }
    claim_episode_index = {
        str(identifier): episode
        for episode in raw_episodes
        for claim in episode["claims"]
        for identifier in (claim.get("claimId"), claim.get("number")) if identifier
    }
    result = raw_episodes, claim_episode_index, report
    _PREPARED_CACHE.clear()
    _PREPARED_CACHE[fingerprint] = result
    return result


def find_case(claims, claim_number, window_days=DEFAULT_WINDOW_DAYS, min_peers=DEFAULT_MIN_PEERS):
    raw_episodes, claim_episode_index, report = _prepare_dataset(claims, window_days, min_peers)
    needle = str(claim_number)
    source_episode = claim_episode_index.get(needle)
    if not source_episode:
        return None, report
    selected = next(claim for claim in source_episode["claims"] if needle in {str(claim.get("claimId")), str(claim.get("number"))})
    cutoff = _claim_order(selected)
    target_episode = {
        **source_episode,
        "end": selected["_serviceDate"],
        "claims": [claim for claim in source_episode["claims"] if _claim_order(claim) <= cutoff],
    }
    case = score_episode(target_episode, raw_episodes, min_peers)
    case["selectedClaim"] = selected
    case["sourceDatasetHash"] = report["sourceDatasetHash"]
    case["sourceCsvHash"] = report["sourceCsvHash"]
    case["calculationVersion"] = report["calculationVersion"]
    return case, report


def _backtest_metric(predicted, actual, interval=None):
    if predicted is None or actual is None:
        return {"predicted": predicted, "actual": actual, "absolute_error": None, "percentage_error": None, "range": interval, "actual_in_range": None}
    absolute_error = abs(float(predicted) - float(actual))
    percentage_error = absolute_error / abs(float(actual)) * 100 if float(actual) else None
    low = (interval or {}).get("low")
    high = (interval or {}).get("high")
    return {
        "predicted": round(float(predicted), 2),
        "actual": round(float(actual), 2),
        "absolute_error": round(absolute_error, 2),
        "percentage_error": round(percentage_error, 1) if percentage_error is not None else None,
        "range": {"low": low, "high": high},
        "actual_in_range": low <= actual <= high if low is not None and high is not None else None,
    }


def _build_backtest(actual, forecast):
    outcome = forecast.get("predicted_claim_outcome", {})
    actual_status = actual.get("claim_status")
    predicted_status = outcome.get("display_value")
    return {
        "available": bool(actual.get("adjudicated")),
        "prediction_cutoff_date": actual.get("service_date"),
        "temporal_holdout": True,
        "claim_outcome": {
            "predicted": predicted_status,
            "probability": outcome.get("probability"),
            "actual": actual_status,
            "correct": predicted_status == actual_status if predicted_status and actual_status else None,
        },
        "allowed": _backtest_metric(forecast["predicted_allowed"].get("value"), actual.get("allowed_amount"), forecast["predicted_allowed"]),
        "paid": _backtest_metric(forecast["predicted_paid"].get("value"), actual.get("paid_amount"), forecast["predicted_paid"]),
        "patient_responsibility": _backtest_metric(forecast["predicted_patient_responsibility"].get("value"), actual.get("patient_responsibility"), forecast["predicted_patient_responsibility"]),
        "adjustment": _backtest_metric(forecast["predicted_adjustment"].get("value"), actual.get("adjustment_amount"), forecast["predicted_adjustment"]),
    }


def _build_reconciliation(actual, forecast):
    charge = forecast["charge_basis"]
    allowed = forecast["predicted_allowed"].get("value") or 0
    paid = forecast["predicted_paid"].get("value") or 0
    patient = forecast["predicted_patient_responsibility"].get("value") or 0
    adjustment = forecast["predicted_adjustment"].get("value") or 0
    component_difference = round(allowed - paid - patient, 2)
    expected_adjustment = round(charge - allowed, 2)
    adjustment_difference = round(adjustment - expected_adjustment, 2)
    tolerance = round(max(1, charge * .02), 2)
    warnings = []
    if paid + patient > allowed + .01:
        warnings.append("Financial consistency warning: the independently predicted payment components do not reconcile with the predicted allowed amount.")
    if abs(adjustment_difference) > tolerance:
        warnings.append("Adjustment consistency warning: predicted adjustment materially differs from charge minus predicted allowed.")
    return {
        "predicted_allowed": allowed,
        "predicted_paid_plus_patient_responsibility": round(paid + patient, 2),
        "reconciliation_difference": component_difference,
        "expected_adjustment_from_charge_less_allowed": expected_adjustment,
        "predicted_adjustment": adjustment,
        "adjustment_difference": adjustment_difference,
        "materiality_tolerance": tolerance,
        "is_reconciled": not warnings,
        "warnings": warnings,
        "actual_charge_less_allowed_difference": round((actual.get("charge_amount") or 0) - (actual.get("allowed_amount") or 0) - (actual.get("adjustment_amount") or 0), 2),
    }


def _provider_financial_metrics(scenario, forecast):
    allowed = forecast["predicted_allowed"].get("value") or 0
    paid = forecast["predicted_paid"].get("value") or 0
    adjustment = forecast["predicted_adjustment"].get("value") or 0
    patient = forecast["predicted_patient_responsibility"].get("value") or 0
    denial_probability = forecast["denial_risk"].get("probability") or 0
    denial_exposure = round(denial_probability * paid, 2)
    repeat_probability = forecast["repeat_service_risk"].get("probability_90d") or 0
    repeat_payment_exposure = round(repeat_probability * paid, 2)
    avoidable = forecast["potentially_avoidable_spend"]
    peer_stats = scenario.get("forecast", {}).get("peerStatistics", {})
    lower_adjustment_rate = peer_stats.get("lowerQuartileAdjustmentRate")
    external_adjustment_sample = scenario.get("forecast", {}).get("metricBasis", {}).get("predicted_adjustment", {}).get("external_sample_size", 0)
    preventable_adjustment = None
    if lower_adjustment_rate is not None and external_adjustment_sample >= DEFAULT_MIN_PEERS and scenario.get("peerMatchLevel", 8) <= 5:
        preventable_adjustment = round(max(adjustment - forecast["charge_basis"] * lower_adjustment_rate, 0), 2)
    opportunity_components = {
        "recoverable_denial_value": None,
        "potentially_avoidable_repeat_spend": avoidable.get("value") if avoidable.get("available") else None,
        "preventable_adjustment_exposure": preventable_adjustment,
    }
    supported_values = [value for value in opportunity_components.values() if value is not None]
    return {
        "provider_expected_net_reimbursement": round(paid, 2),
        "provider_expected_reimbursement": round(paid, 2),
        "provider_expected_writeoff": round(adjustment, 2),
        "provider_payment_gap": round(allowed - paid, 2),
        "predicted_patient_balance": round(patient, 2),
        "expected_denial_exposure": denial_exposure,
        "expected_contractual_adjustment": round(adjustment, 2),
        "expected_repeat_provider_payment_exposure": repeat_payment_exposure,
        "potentially_avoidable_repeat_spend": avoidable.get("value") if avoidable.get("available") else None,
        "validated_recovery_opportunity": None,
        "synthetic_demo_opportunity": None,
        "opportunity_components": opportunity_components,
        "opportunity_reason": None if supported_values else "No evidence-supported recovery or avoidance amount was calculated.",
        "formulas": {
            "expected_denial_exposure": "denial_probability × predicted_paid",
            "expected_repeat_provider_payment_exposure": "repeat_probability_90d × predicted_paid",
            "expected_contractual_adjustment": "predicted adjustment shown separately; it is not assumed recoverable",
        },
    }


def _rank_provider_actions(scenario, actual, forecast, financial_metrics, reconciliation):
    features = scenario.get("features", {})
    claim_ids = scenario.get("sourceClaimIds", [])
    actions = []
    def add(code, title, priority, impact, reason, owner, urgency, affected=None):
        actions.append({
            "code": code, "title": title, "priority": priority, "expected_financial_impact": impact,
            "reason": reason, "affected_claim_ids": affected or claim_ids, "operational_owner": owner,
            "urgency": urgency, "supporting_evidence": affected or claim_ids,
        })
    denied_ids = [claim.get("claimId") for claim in scenario.get("claims", []) if _is_denied(claim)]
    if actual.get("claim_status") == "Denied" or denied_ids:
        add("review_denial", "Review the recorded denial", 95, None, "A denial exists, but recoverability is not assumed without correction evidence.", "Denials team", "Immediate", denied_ids or [actual.get("claim_id")])
    duplicate_groups = Counter((claim.get("dos"), claim.get("cptCode"), claim.get("billingProviderNpi")) for claim in scenario.get("claims", []))
    duplicate = any(count > 1 for count in duplicate_groups.values())
    if duplicate:
        add("validate_duplicate", "Validate overlapping claim services", 88, financial_metrics.get("potentially_avoidable_repeat_spend"), "The episode contains overlapping date, CPT and billing-provider evidence.", "Claims integrity", "Before submission")
    elif features.get("repeatedServiceCount", 0) > 0:
        add("review_repeat", "Review repeated-service documentation", 75, financial_metrics.get("potentially_avoidable_repeat_spend"), f"The episode contains {features['repeatedServiceCount']} repeated CPT service(s).", "Coding operations", "Before next related claim")
    if not actual.get("has_prior_auth"):
        add("verify_authorization", "Verify payer authorization requirements", 68, None, "Verify whether the payer requires authorization for this service. The missing identifier does not establish whether authorization was required.", "Authorization team", "Before submission", [actual.get("claim_id")])
    if not actual.get("has_referral"):
        add("verify_referral", "Verify payer referral requirements", 62, None, "No referral number is recorded; the dataset does not establish whether a referral was required.", "Referral team", "Before submission", [actual.get("claim_id")])
    preventable_adjustment = financial_metrics.get("opportunity_components", {}).get("preventable_adjustment_exposure")
    if preventable_adjustment is not None and preventable_adjustment > 0:
        add("coding_review", "Review adjustment-driving claim inputs", 82, preventable_adjustment, "Predicted adjustment exceeds the lower-quartile adjustment level in the matched external sample.", "Revenue integrity", "Before submission")
    if forecast["repeat_service_risk"].get("probability_90d", 0) >= REPEAT_MEDIUM_THRESHOLD:
        add("monitor_follow_up", "Monitor provider-side follow-up", 70, financial_metrics.get("potentially_avoidable_repeat_spend"), "The deterministic 90-day related-service probability exceeds the configured medium-risk threshold.", "Provider operations", "Within 30 days")
    if reconciliation.get("warnings"):
        add("reconcile_components", "Review predicted payment-component reconciliation", 72, None, reconciliation["warnings"][0], "Patient financial services", "Before balance assignment")
    actions.sort(key=lambda item: (item["priority"], item["expected_financial_impact"] if item["expected_financial_impact"] is not None else -1), reverse=True)
    for rank, action in enumerate(actions, 1):
        action["rank"] = rank
    return actions


def _provider_scenario_map(scenario, actual, forecast, financial_metrics, reconciliation, savings):
    longitudinal = scenario.get("longitudinalBasis", {})
    avoidable_basis = scenario.get("avoidableSpendBasis", {})
    peer_stats = scenario.get("forecast", {}).get("peerStatistics", {})
    selected = scenario.get("selectedClaim") or scenario.get("anchor") or {}
    enrichment = selected.get("syntheticEnrichment") or {}
    best_action = savings.get("best_action", {})
    synthetic_demo = savings.get("synthetic_demo_opportunity", {})
    validated_real = savings.get("validated_real_savings", {})
    future = savings.get("future_exposure", {})
    avoidable = savings.get("avoidable_spend", {})
    lower_adjustment = None
    if peer_stats.get("lowerQuartileAdjustmentRate") is not None:
        lower_adjustment = round(forecast["charge_basis"] * peer_stats["lowerQuartileAdjustmentRate"], 2)
    comparison_supported = (
        scenario.get("peerClaimCount", 0) >= DEFAULT_MIN_PEERS
        and scenario.get("peerMatchLevel", 8) <= 5
        and lower_adjustment is not None
    )
    cutoff = _parse_date(actual.get("service_date"))
    earlier_claims = [
        claim for claim in scenario.get("claims", [])
        if _parse_date(claim.get("dos")) and (cutoff is None or _parse_date(claim.get("dos")) < cutoff)
    ]
    claim_timeline = [
        {"claim_id": claim.get("claimId"), "service_date": claim.get("dos"), "status": claim.get("status"), "cpt": claim.get("cptCode"), "paid": claim.get("paid")}
        for claim in sorted(earlier_claims, key=_claim_order)[-8:]
    ]
    opportunity_types = {item.get("type") for item in synthetic_demo.get("breakdown", [])}
    cost_leakage_risks = []
    def add_risk(code, title, evidence, data_source):
        cost_leakage_risks.append({"code": code, "title": title, "evidence": evidence, "data_source": data_source})
    if (forecast.get("denial_risk", {}).get("probability") or 0) > 0:
        add_risk("denial", "Denial risk", f"Predicted probability {forecast['denial_risk'].get('percentage', 0):.1f}%", "prediction")
    if "underpayment" in opportunity_types:
        add_risk("underpayment", "Possible underpayment", "Synthetic contract comparison met the configured tolerance.", "synthetic")
    if "adjustment" in opportunity_types:
        add_risk("adjustment", "Excessive adjustment", "Synthetic contract adjustment comparison met the configured tolerance.", "synthetic")
    if "patient_balance" in opportunity_types:
        add_risk("patient_balance", "Outstanding patient balance", "Synthetic balance, aging and collection fields are actionable for demonstration.", "synthetic")
    if "authorization" in opportunity_types:
        add_risk("authorization", "Authorization issue", "Synthetic authorization requirement and status are actionable.", "synthetic")
    if "referral" in opportunity_types:
        add_risk("referral", "Referral issue", "Synthetic referral requirement and status are actionable.", "synthetic")
    if "duplicate_or_correction" in opportunity_types:
        add_risk("duplicate", "Duplicate or corrected claim", "Synthetic duplicate or claim-correction evidence is present.", "synthetic")
    if (forecast.get("repeat_service_risk", {}).get("probability_90d") or 0) >= REPEAT_MEDIUM_THRESHOLD:
        add_risk("repeat", "Repeat-service risk", f"90-day probability {forecast['repeat_service_risk'].get('probability_90d', 0) * 100:.1f}%", "prediction")
    if reconciliation.get("warnings"):
        add_risk("reconciliation", "Payment reconciliation issue", reconciliation["warnings"][0], "prediction")
    if scenario.get("peerMatchLevel", 0) >= 4:
        add_risk("fallback", "Broad peer fallback", f"Fallback level {scenario.get('peerMatchLevel')}", "prediction")

    stage_by_category = {
        "denial": "Follow-up", "underpayment": "Financial opportunity", "adjustment": "Financial opportunity",
        "patient_balance": "Financial opportunity", "authorization": "Validation", "referral": "Validation",
        "duplicate_or_correction": "Coding", "provider_follow_up": "Follow-up", "routine_monitoring": "Validation",
    }
    selected_stage = stage_by_category.get(best_action.get("action_category"), "Validation")
    workflow = [
        {"stage": stage, "selected": stage == selected_stage}
        for stage in ("Encounter", "Coding", "Validation", "Claim submission", "Adjudication", "Payment", "Follow-up", "Financial opportunity")
    ]

    scenario_map = {
        "member_claim_history": {
            "previous_related_claim_ids": longitudinal.get("priorRelatedClaimIds", []),
            "earlier_related_claim_count": longitudinal.get("priorRelatedClaimCount", 0),
            "earlier_same_cpt_claim_count": longitudinal.get("priorSameCptCount", 0),
            "previous_denial_count": longitudinal.get("priorDeniedCount", 0),
            "payment_trend": longitudinal.get("priorPaymentTrend", "insufficient history"),
            "recurrence_evidence": forecast.get("repeat_service_risk", {}).get("basis", {}),
            "claim_timeline": claim_timeline,
        },
        "encounter_and_coding": {
            "service_date": actual.get("service_date"), "diagnosis": actual.get("diagnosis_code"), "diagnosis_family": actual.get("diagnosis_family"),
            "cpt_code": actual.get("cpt_code"), "cpt_description": actual.get("cpt_description"), "units": actual.get("units"),
            "place_of_service": f"{actual.get('place_of_service_code') or ''} — {actual.get('place_of_service_description') or ''}".strip(" —"),
            "payer": actual.get("payer"), "billing_provider": actual.get("billing_provider"),
            "authorization_status": enrichment.get("Prior_Auth_Status") or ("Identifier present" if actual.get("has_prior_auth") else "Requirement unknown"),
            "referral_status": enrichment.get("Referral_Status") or ("Identifier present" if actual.get("has_referral") else "Requirement unknown"),
            "data_source": "Claims_Original; authorization/referral status may use labelled Dummy_Enrichment",
        },
        "provider_claim_payment_prediction": {
            "charge": forecast["charge_basis"], "predicted_allowed": forecast["predicted_allowed"], "predicted_paid": forecast["predicted_paid"],
            "predicted_patient_responsibility": forecast["predicted_patient_responsibility"].get("value"), "predicted_adjustment": forecast["predicted_adjustment"].get("value"),
            "denial_probability": forecast["denial_risk"].get("probability"),
            "repeat_probability_30d": forecast["repeat_service_risk"].get("probability_30d"),
            "repeat_probability_60d": forecast["repeat_service_risk"].get("probability_60d"),
            "repeat_probability_90d": forecast["repeat_service_risk"].get("probability_90d"),
            "expected_provider_payment": financial_metrics.get("provider_expected_reimbursement"),
            "expected_contractual_adjustment": financial_metrics.get("expected_contractual_adjustment"),
            "expected_denial_revenue_exposure": financial_metrics.get("expected_denial_exposure"),
        },
        "where_provider_money_may_be_saved": {
            "validated_real_opportunity": {**validated_real, "amount_type": "validated_real_savings", "confidence": forecast.get("confidence", {}).get("score")},
            "synthetic_demonstration_opportunity": {**synthetic_demo, "amount_type": "synthetic_demo_opportunity", "confidence": forecast.get("confidence", {}).get("score")},
            "future_financial_exposure": {**future, "amount_type": "forecast_exposure", "data_source": "prediction"},
            "potentially_avoidable_spend": {**avoidable, "amount_type": "potentially_avoidable_spend", "data_source": "prediction and historical episodes"},
            "best_next_provider_action": best_action,
        },
        "cost_leakage_risks": cost_leakage_risks,
        "claim_workflow": workflow,
    }
    if comparison_supported:
        scenario_map["provider_money_comparison"] = {
            "available": comparison_supported,
            "current_predicted_pathway": {
                "expected_provider_payment": forecast["predicted_paid"].get("value"),
                "expected_adjustment": forecast["predicted_adjustment"].get("value"),
                "expected_denial_exposure": financial_metrics.get("expected_denial_exposure"),
                "expected_repeat_service_spend": avoidable_basis.get("predicted_repeat_expenditure"),
            },
            "comparable_lower_risk_pathway": {
                "expected_provider_payment": forecast["predicted_paid"].get("value"),
                "expected_adjustment": lower_adjustment,
                "expected_denial_exposure": round((scenario.get("denialRisk", {}).get("basis", {}).get("peer_rate") or 0) * (forecast["predicted_allowed"].get("value") or 0), 2),
                "expected_repeat_service_spend": avoidable_basis.get("comparable_peer_repeat_expenditure"),
            },
            "opportunity_difference": savings.get("avoidable_spend", {}).get("amount"),
        }
    return scenario_map


def _build_savings_opportunity(scenario, actual, forecast, financial_metrics, reconciliation, _scenario_map):
    """Build one evidence-gated savings view from the exact shared forecast object."""
    recovery = scenario.get("recoveryBasis", {})
    repeat_basis = forecast.get("repeat_service_risk", {}).get("basis", {})
    repeat_probability = forecast.get("repeat_service_risk", {}).get("probability_90d") or 0
    predicted_allowed = forecast.get("predicted_allowed", {}).get("value") or 0
    predicted_paid = forecast.get("predicted_paid", {}).get("value") or 0
    denial_probability = forecast.get("denial_risk", {}).get("probability") or 0
    forecast_label = "Forecast exposure — not confirmed savings"
    minimum_amount = float(os.getenv("PROVIDER_CURRENT_OPPORTUNITY_MINIMUM", CURRENT_OPPORTUNITY_MINIMUM))

    match_supported = bool(recovery.get("available") and recovery.get("sample_size", 0) >= recovery.get("minimum_sample_size", DEFAULT_MIN_PEERS))
    actual_allowed = actual.get("allowed_amount") or 0
    actual_charge = actual.get("charge_amount") or 0
    actual_paid = actual.get("paid_amount") or 0
    actual_adjustment = actual.get("adjustment_amount") or 0
    actual_patient = actual.get("patient_responsibility") or 0

    actual_rates = {
        "allowed": round(actual_allowed / actual_charge, 4) if actual_charge else None,
        "paid_to_allowed": round(actual_paid / actual_allowed, 4) if actual_allowed else None,
        "adjustment": round(actual_adjustment / actual_charge, 4) if actual_charge else None,
        "patient_share": round(actual_patient / actual_allowed, 4) if actual_allowed else None,
    }
    peer_rates = {
        "allowed": recovery.get("median_allowed_rate") if match_supported else None,
        "paid_to_allowed": recovery.get("median_paid_to_allowed_rate") if match_supported else None,
        "adjustment": recovery.get("median_adjustment_rate") if match_supported else None,
        "patient_share": recovery.get("median_patient_share_rate") if match_supported else None,
    }
    expected_amounts = {
        "allowed": round(actual_charge * peer_rates["allowed"], 2) if peer_rates["allowed"] is not None else None,
        "paid_to_allowed": round(actual_allowed * peer_rates["paid_to_allowed"], 2) if peer_rates["paid_to_allowed"] is not None else None,
        "adjustment": round(actual_charge * peer_rates["adjustment"], 2) if peer_rates["adjustment"] is not None else None,
        "patient_share": round(actual_allowed * peer_rates["patient_share"], 2) if peer_rates["patient_share"] is not None else None,
    }
    actual_amounts = {
        "allowed": actual_allowed,
        "paid_to_allowed": actual_paid,
        "adjustment": actual_adjustment,
        "patient_share": actual_patient,
    }

    performance_metrics = {}
    for key, label, higher_is_favourable in (
        ("allowed", "Allowed", True),
        ("paid_to_allowed", "Provider payment", True),
        ("adjustment", "Adjustment", False),
        ("patient_share", "Patient responsibility", False),
    ):
        actual_rate = actual_rates[key]
        historical_rate = peer_rates[key]
        expected_amount = expected_amounts[key]
        actual_amount = actual_amounts[key]
        raw_dollar_difference = round(actual_amount - expected_amount, 2) if expected_amount is not None else None
        related_dollar_variance = (
            raw_dollar_difference if higher_is_favourable else round(-raw_dollar_difference, 2)
        ) if raw_dollar_difference is not None else None
        if related_dollar_variance is None:
            indicator = "unavailable"
        elif related_dollar_variance > .01:
            indicator = "favourable"
        elif related_dollar_variance < -.01:
            indicator = "unfavourable"
        else:
            indicator = "aligned"
        performance_metrics[key] = {
            "label": label,
            "actual_rate": actual_rate,
            "historical_median_rate": historical_rate,
            "rate_variance_percentage_points": round((actual_rate - historical_rate) * 100, 2) if actual_rate is not None and historical_rate is not None else None,
            "actual_amount": round(actual_amount, 2),
            "historical_expected_amount": expected_amount,
            "related_dollar_variance": related_dollar_variance,
            "variance_status": indicator,
            "variance_label": f"{indicator} variance versus history" if indicator in {"favourable", "unfavourable"} else "aligned with history" if indicator == "aligned" else "historical comparison unavailable",
            "higher_is_favourable": higher_is_favourable,
        }

    available_metrics = [item for item in performance_metrics.values() if item["variance_status"] != "unavailable"]
    favourable_count = sum(item["variance_status"] == "favourable" for item in available_metrics)
    unfavourable_count = sum(item["variance_status"] == "unfavourable" for item in available_metrics)
    aligned_count = sum(item["variance_status"] == "aligned" for item in available_metrics)
    if not match_supported:
        performance_conclusion = "No historical group met the configured matching and sample-size requirements for this claim."
    else:
        performance_conclusion = (
            f"Compared with {recovery.get('sample_size', 0)} matched earlier claim(s), "
            f"{favourable_count} metric(s) were favourable, {unfavourable_count} were unfavourable and {aligned_count} were aligned."
        )
    current_performance = {
        "available": match_supported,
        "adjudicated": bool(actual.get("adjudicated")),
        "metrics": performance_metrics,
        "matched_claim_count": recovery.get("sample_size", 0),
        "peer_count": recovery.get("sample_size", 0),
        "match_level": recovery.get("match_level", "insufficient evidence"),
        "matching_dimensions": recovery.get("matching_dimensions", []),
        "prediction_cutoff_date": recovery.get("prediction_cutoff_date"),
        "matched_claim_ids": recovery.get("claim_ids", []),
        "affected_claim_ids": recovery.get("claim_ids", []),
        "conclusion": performance_conclusion,
        "actual_allowed_rate": actual_rates["allowed"],
        "peer_allowed_rate": peer_rates["allowed"],
        "actual_paid_to_allowed_rate": actual_rates["paid_to_allowed"],
        "peer_paid_to_allowed_rate": peer_rates["paid_to_allowed"],
        "actual_adjustment_rate": actual_rates["adjustment"],
        "peer_adjustment_rate": peer_rates["adjustment"],
        "actual_patient_share_rate": actual_rates["patient_share"],
        "peer_patient_share_rate": peer_rates["patient_share"],
        "indicators": {
            "allowed_rate": performance_metrics["allowed"]["variance_status"],
            "paid_to_allowed_rate": performance_metrics["paid_to_allowed"]["variance_status"],
            "adjustment_rate": performance_metrics["adjustment"]["variance_status"],
            "patient_share_rate": performance_metrics["patient_share"]["variance_status"],
        },
    }

    source_claim = scenario.get("selectedClaim") or scenario.get("anchor") or {}
    enrichment = source_claim.get("syntheticEnrichment") or {}
    synthetic_active = str(enrichment.get("Dummy_Data_Flag") or "").strip().lower() in {"yes", "true", "1"}
    synthetic_fields_used = set()

    def synthetic(field, default=None):
        value = enrichment.get(field, default)
        if field in enrichment and str(value or "").strip() and str(value).strip().lower() not in {"n/a", "not applicable"}:
            synthetic_fields_used.add(field)
        return value

    def normalized(value):
        return str(value or "").strip().lower()

    def is_yes(value):
        return normalized(value) in {"yes", "true", "1", "y"}

    status = normalized(actual.get("claim_status"))
    denied = "denied" in status or "rejected" in status
    tolerance = _amount(synthetic("Payment_Tolerance")) if synthetic_active else 0
    recovered_amount = _amount(synthetic("Recovered_Amount")) if synthetic_active else 0
    contract_allowed = _amount(synthetic("Contract_Allowed_Amount")) if synthetic_active else 0
    stated_expected_reimbursement = _amount(synthetic("Expected_Reimbursement")) if synthetic_active else 0
    expected_peer_paid = expected_amounts["paid_to_allowed"]
    expected_peer_adjustment = expected_amounts["adjustment"]
    historical_underpayment = round(max(expected_peer_paid - actual_paid, 0), 2) if expected_peer_paid is not None else None
    historical_excessive_adjustment = round(max(actual_adjustment - expected_peer_adjustment, 0), 2) if expected_peer_adjustment is not None else None

    opportunities = []
    if synthetic_active and match_supported and not denied and (stated_expected_reimbursement > 0 or contract_allowed > 0):
        contract_expected_payment = stated_expected_reimbursement or round(contract_allowed * (peer_rates["paid_to_allowed"] or 0), 2)
        underpayment = round(max(contract_expected_payment - actual_paid - recovered_amount, 0), 2)
        if underpayment > max(tolerance, minimum_amount):
            opportunities.append({
                "type": "underpayment", "stage": "Payment underpayment review", "amount": underpayment,
                "owner": "billing", "confidence": forecast.get("confidence", {}).get("score"),
                "reason": "Actual provider payment is below the synthetic contract expectation by more than the payment tolerance.",
                "calculation": "max(0, expected reimbursement − actual paid − recovered amount)",
                "synthetic_fields_used": ["Expected_Reimbursement", "Contract_Allowed_Amount", "Payment_Tolerance", "Recovered_Amount"],
            })
    else:
        contract_expected_payment = stated_expected_reimbursement or None
        underpayment = None

    expected_contract_adjustment = round(max(actual_charge - contract_allowed, 0), 2) if synthetic_active and contract_allowed > 0 else None
    excessive_adjustment = round(max(actual_adjustment - expected_contract_adjustment, 0), 2) if expected_contract_adjustment is not None else None
    if excessive_adjustment is not None and excessive_adjustment > max(tolerance, minimum_amount):
        opportunities.append({
            "type": "adjustment", "stage": "Excessive adjustment review", "amount": excessive_adjustment,
            "owner": "billing", "confidence": forecast.get("confidence", {}).get("score"),
            "reason": "Actual adjustment exceeds the synthetic contract-derived adjustment by more than the payment tolerance.",
            "calculation": "max(0, actual adjustment − (actual charge − contract allowed))",
            "synthetic_fields_used": ["Contract_Allowed_Amount", "Payment_Tolerance"],
        })

    denial_correctable = is_yes(synthetic("Denial_Correctable_Flag")) if synthetic_active else False
    appeal_status = normalized(synthetic("Appeal_Status"))
    resubmission_status = normalized(synthetic("Resubmission_Status"))
    denial_actionable = appeal_status in {"not appealed", "appeal submitted", "pending", "open"} or resubmission_status in {"pending review", "corrected and resubmitted"}
    denial_basis_amount = stated_expected_reimbursement or contract_expected_payment or 0
    correctable_denial_value = round(max(denial_basis_amount - recovered_amount, 0), 2) if denied and denial_correctable and denial_actionable and denial_basis_amount else None
    if correctable_denial_value is not None and correctable_denial_value > max(tolerance, minimum_amount):
        opportunities.append({
            "type": "denial", "stage": "Denial correction", "amount": correctable_denial_value,
            "owner": "denial management", "confidence": forecast.get("confidence", {}).get("score"),
            "reason": "The claim is denied or rejected, the synthetic correctable flag is active and appeal or resubmission status is actionable.",
            "calculation": "max(0, expected reimbursement − already recovered amount)",
            "recovered_amount": recovered_amount,
            "synthetic_fields_used": ["Denial_Correctable_Flag", "Appeal_Status", "Resubmission_Status", "Recovered_Amount", "Expected_Reimbursement"],
        })

    outstanding_balance = _amount(synthetic("Outstanding_Patient_Balance")) if synthetic_active else 0
    balance_status = normalized(synthetic("Balance_Status"))
    collection_status = normalized(synthetic("Collection_Status"))
    aging_bucket = normalized(synthetic("Aging_Bucket"))
    patient_balance_available = outstanding_balance > 0 and balance_status == "outstanding" and (collection_status in {"in collection", "in collections", "collections"} or aging_bucket in {"61-90", "91+"})
    patient_balance_reason = (
        "Synthetic demonstration data shows an actionable outstanding patient balance."
        if patient_balance_available
        else "No actionable outstanding, aged or collection-stage patient balance is supported for this claim."
    )
    if patient_balance_available:
        opportunities.append({
            "type": "patient_balance", "stage": "DEMO PATIENT-BALANCE OPPORTUNITY", "amount": outstanding_balance,
            "owner": "patient financial services", "confidence": forecast.get("confidence", {}).get("score"),
            "reason": "Synthetic demonstration data marks a balance as outstanding and aged or in collections; this is not verified patient-accounting data.",
            "calculation": "outstanding patient balance",
            "data_source": "Dummy_Enrichment",
            "synthetic_outstanding_balance": outstanding_balance,
            "synthetic_aging_bucket": synthetic("Aging_Bucket"),
            "synthetic_collection_status": synthetic("Collection_Status"),
            "warning": "Synthetic demonstration data. This opportunity is for UI and workflow testing only and is not a verified billing recommendation.",
            "synthetic_fields_used": ["Outstanding_Patient_Balance", "Balance_Status", "Collection_Status", "Aging_Bucket"],
        })

    prior_auth_required = is_yes(synthetic("Prior_Auth_Required")) if synthetic_active else False
    prior_auth_status = normalized(synthetic("Prior_Auth_Status"))
    auth_actionable = prior_auth_required and prior_auth_status in {"missing", "expired", "denied", "insufficient units"}
    referral_required = is_yes(synthetic("Referral_Required")) if synthetic_active else False
    referral_status = normalized(synthetic("Referral_Status"))
    referral_actionable = referral_required and referral_status in {"missing", "invalid", "expired"}
    duplicate_actionable = is_yes(synthetic("Duplicate_Claim_Flag")) or normalized(synthetic("Claim_Frequency_Code")) not in {"", "1", "original"} or is_yes(synthetic("Corrected_Claim_Indicator"))
    if auth_actionable:
        opportunities.append({"type": "authorization", "stage": "Authorization verification", "amount": None, "owner": "authorization team", "confidence": forecast.get("confidence", {}).get("score"), "reason": f"Synthetic demonstration data marks authorization as required and its status as {synthetic('Prior_Auth_Status')}.", "synthetic_fields_used": ["Prior_Auth_Required", "Prior_Auth_Status"]})
    if referral_actionable:
        opportunities.append({"type": "referral", "stage": "Referral verification", "amount": None, "owner": "authorization team", "confidence": forecast.get("confidence", {}).get("score"), "reason": f"Synthetic demonstration data marks a referral as required and its status as {synthetic('Referral_Status')}.", "synthetic_fields_used": ["Referral_Required", "Referral_Status"]})
    if duplicate_actionable:
        opportunities.append({"type": "duplicate_or_correction", "stage": "Duplicate-service review", "amount": None, "owner": "coding", "confidence": forecast.get("confidence", {}).get("score"), "reason": "Synthetic duplicate, correction or claim-frequency evidence supports claim validation.", "synthetic_fields_used": ["Duplicate_Claim_Flag", "Claim_Frequency_Code", "Corrected_Claim_Indicator"]})

    for opportunity in opportunities:
        opportunity.setdefault("data_source", "Dummy_Enrichment")
        opportunity.setdefault("amount_type", "synthetic_demo_opportunity" if opportunity.get("amount") is not None else "no_amount")
        opportunity.setdefault("evidence", [actual.get("claim_id")])

    monetary_opportunities = [item for item in opportunities if item.get("amount") is not None and item["amount"] > 0]
    total_demo_opportunity = None
    opportunity_status = "identified" if opportunities else "none_identified" if synthetic_active else "insufficient"
    current_limitations = []
    if not synthetic_active:
        current_limitations.append("No matching synthetic enrichment row was available for this claim and member.")
    if not match_supported:
        current_limitations.append("No historical group met the configured matching and sample-size requirements.")

    future_exposure = {
        "denial_revenue_exposure": round(denial_probability * predicted_paid, 2),
        "repeat_allowed_exposure": round(repeat_probability * predicted_allowed, 2),
        "repeat_provider_payment_exposure": round(repeat_probability * predicted_paid, 2),
        "expected_denial_revenue_exposure": round(denial_probability * predicted_paid, 2),
        "expected_repeat_allowed_exposure": round(repeat_probability * predicted_allowed, 2),
        "expected_repeat_provider_payment_exposure": round(repeat_probability * predicted_paid, 2),
        "denial_probability": denial_probability,
        "repeat_probability_90d": repeat_probability,
        "label": forecast_label,
    }
    avoidable_basis = scenario.get("avoidableSpendBasis", {})
    avoidable_spend = {
        "available": bool(avoidable_basis.get("available")),
        "amount": avoidable_basis.get("amount") if avoidable_basis.get("available") else None,
        "reason": avoidable_basis.get("reason") or "The repeated-related-claim and lower-repeat comparison requirements were not met.",
        "comparison_episode_count": avoidable_basis.get("comparison_episode_count", 0),
        "related_claim_count": avoidable_basis.get("related_claim_count", 0),
        "repeated_related_service_count": avoidable_basis.get("repeated_related_service_count", 0),
        "prediction_cutoff_date": avoidable_basis.get("prediction_cutoff_date"),
        "synthetic_demo": bool(avoidable_basis.get("synthetic_demo")),
        "display_label": "Synthetic demonstration estimate" if avoidable_basis.get("available") and avoidable_basis.get("synthetic_demo") else None,
    }

    source_columns = {"".join(character for character in str(name).lower() if character.isalnum()) for name in source_claim.get("sourceCsvColumns", [])}
    def has_original_column(*names):
        return any("".join(character for character in str(name).lower() if character.isalnum()) in source_columns for name in names)
    def has_synthetic_value(*names):
        return any(str(enrichment.get(name) or "").strip() and normalized(enrichment.get(name)) not in {"n/a", "not applicable"} for name in names)
    def availability(key, label, status_value, detail, fields=None):
        for field in fields or []:
            if has_synthetic_value(field):
                synthetic_fields_used.add(field)
        return {"key": key, "label": label, "status": status_value, "detail": detail}

    related_count = avoidable_spend["related_claim_count"]
    repeated_count = avoidable_spend["repeated_related_service_count"]
    comparison_count = avoidable_spend["comparison_episode_count"]
    minimum_peers = recovery.get("minimum_sample_size", DEFAULT_MIN_PEERS)
    synthetic_status = "Available from synthetic enrichment"
    data_availability = [
        availability("contract", "Contract and fee schedule", synthetic_status if has_synthetic_value("Contract_Allowed_Amount", "Expected_Reimbursement") else "Missing", "Synthetic contract expectation is available for demonstration." if has_synthetic_value("Contract_Allowed_Amount", "Expected_Reimbursement") else "No contract or fee-schedule amount is available.", ["Contract_Allowed_Amount", "Expected_Reimbursement", "Payment_Tolerance"]),
        availability("remittance", "CARC/RARC and remittance details", synthetic_status if denied and has_synthetic_value("CARC_Code", "RARC_Code") else "Not applicable" if not denied else "Missing", "The selected claim is not denied." if not denied else "Synthetic remittance reason codes are available." if has_synthetic_value("CARC_Code", "RARC_Code") else "No remittance reason code is available.", ["CARC_Code", "RARC_Code"]),
        availability("appeal", "Appeal and recovery status", synthetic_status if denied and has_synthetic_value("Appeal_Status", "Denial_Resolution_Status") else "Not applicable" if not denied else "Missing", "The selected claim is not denied or rejected." if not denied else "Synthetic appeal and recovery status is available." if has_synthetic_value("Appeal_Status", "Denial_Resolution_Status") else "No appeal or recovery status is available.", ["Appeal_Status", "Resubmission_Status", "Denial_Resolution_Status", "Recovered_Amount"]),
        availability("patient_balance", "Patient balance and collections", synthetic_status if has_synthetic_value("Outstanding_Patient_Balance", "Balance_Status") else "Missing", "Synthetic balance, aging and collection fields are available." if has_synthetic_value("Outstanding_Patient_Balance", "Balance_Status") else "No actionable balance status is available.", ["Outstanding_Patient_Balance", "Balance_Status", "Aging_Bucket", "Collection_Status"]),
        availability("authorization", "Authorization requirement", synthetic_status if has_synthetic_value("Prior_Auth_Required") else "Missing", "Synthetic authorization requirement and status are available." if has_synthetic_value("Prior_Auth_Required") else "No authorization requirement is available.", ["Prior_Auth_Required", "Prior_Auth_Status"]),
        availability("referral", "Referral requirement", synthetic_status if has_synthetic_value("Referral_Required") else "Missing", "Synthetic referral requirement and status are available." if has_synthetic_value("Referral_Required") else "No referral requirement is available.", ["Referral_Required", "Referral_Status"]),
        availability("duplicate", "Duplicate and correction information", synthetic_status if has_synthetic_value("Duplicate_Claim_Flag", "Claim_Frequency_Code", "Corrected_Claim_Indicator") else "Missing", "Synthetic duplicate and correction indicators are available." if has_synthetic_value("Duplicate_Claim_Flag", "Claim_Frequency_Code", "Corrected_Claim_Indicator") else "No duplicate or correction indicator is available.", ["Duplicate_Claim_Flag", "Claim_Frequency_Code", "Corrected_Claim_Indicator"]),
        availability("episode", "Episode and outcome information", synthetic_status if has_synthetic_value("Episode_ID", "Treatment_Outcome") else "Missing", "Synthetic episode and outcome fields are available." if has_synthetic_value("Episode_ID", "Treatment_Outcome") else "No episode outcome information is available.", ["Episode_ID", "Related_Claim_Flag", "Condition_Resolved", "Treatment_Outcome", "Follow_Up_Completed", "Repeat_Visit_Reason"]),
        availability("patient_cost", "Patient cost components", "Available from original claims" if has_original_column("Patient_Responsibility") else "Missing", "Patient responsibility is read from the original claim and is not replaced by enrichment."),
        availability("lower_repeat", "Comparable lower-repeat episodes", synthetic_status if comparison_count >= minimum_peers else "Insufficient matched history", f"{comparison_count} comparable lower-repeat episode(s) found; minimum required is {minimum_peers}."),
    ]
    # Backward-compatible audit keys remain non-display metadata for existing API consumers.
    data_availability.extend([
        {"key": "denial_correction_status", "label": "Denial correction status", "status": "Not applicable to this claim" if not denied else synthetic_status if has_synthetic_value("Appeal_Status", "Resubmission_Status") else "Missing from dataset", "detail": "Compatibility audit field.", "display": False},
        {"key": "denial_recoverability_status", "label": "Denial recoverability status", "status": "Not applicable to this claim" if not denied else synthetic_status if has_synthetic_value("Denial_Correctable_Flag") else "Missing from dataset", "detail": "Compatibility audit field.", "display": False},
        {"key": "collection_status", "label": "Patient collection status", "status": synthetic_status if has_synthetic_value("Collection_Status") else "Missing from dataset", "detail": "Compatibility audit field.", "display": False},
        {"key": "authorization_requirement", "label": "Payer authorization requirement", "status": synthetic_status if has_synthetic_value("Prior_Auth_Required") else "Missing from dataset", "detail": "Compatibility audit field.", "display": False},
    ])

    action_priority = {
        "denial": 1, "underpayment": 2, "adjustment": 3, "patient_balance": 5,
        "authorization": 6, "referral": 7, "duplicate_or_correction": 8,
    }
    ranked_opportunities = sorted(opportunities, key=lambda item: (action_priority.get(item.get("type"), 99), -(item.get("amount") or 0)))
    ranked_monetary = sorted(monetary_opportunities, key=lambda item: (action_priority.get(item.get("type"), 99), -(item.get("amount") or 0)))
    # Opportunity types may overlap, so expose only the highest-priority supported
    # amount instead of summing them into a misleading total.
    total_demo_opportunity = ranked_monetary[0]["amount"] if ranked_monetary else None
    if ranked_opportunities:
        chosen = ranked_opportunities[0]
        is_patient_balance = chosen.get("type") == "patient_balance"
        action_text = (
            "Review the synthetic patient-balance workflow fields for demonstration purposes only."
            if is_patient_balance
            else f"Perform {chosen['stage'].lower()} using the supporting synthetic demonstration fields."
        )
        best_action = {
            "stage": chosen["stage"], "action": action_text, "action_category": chosen["type"],
            "owner": chosen["owner"], "amount": chosen.get("amount"), "amount_addressed": chosen.get("amount"),
            "amount_type": "synthetic_demo_opportunity" if chosen.get("amount") is not None else "no_amount",
            "reason": chosen["reason"], "evidence": [actual.get("claim_id")], "data_source": "synthetic",
        }
    elif repeat_probability >= REPEAT_MEDIUM_THRESHOLD:
        best_action = {"stage": "Provider follow-up monitoring", "action": "Monitor for a related service within 90 days and use pre-submission validation if one is initiated.", "action_category": "provider_follow_up", "owner": "provider operations", "amount": future_exposure["repeat_provider_payment_exposure"], "amount_addressed": future_exposure["repeat_provider_payment_exposure"], "amount_type": "forecast_exposure", "reason": "The shared 90-day repeat probability meets the configured follow-up threshold.", "evidence": [actual.get("claim_id")], "data_source": "prediction"}
    else:
        best_action = {"stage": "Routine monitoring", "action": "Continue routine monitoring and validate CPT, diagnosis, place of service and payer requirements before a future related claim.", "action_category": "routine_monitoring", "owner": "provider operations", "amount": None, "amount_addressed": None, "amount_type": "no_amount", "reason": performance_conclusion, "evidence": [actual.get("claim_id")], "data_source": "prediction"}
    best_action["confidence"] = forecast.get("confidence", {}).get("score")
    best_action["affected_claim_ids"] = [actual.get("claim_id")]
    best_action["financial_exposures_addressed"] = [
        {"type": "denial_revenue", "amount": future_exposure["expected_denial_revenue_exposure"], "amount_type": "forecast_exposure"},
        {"type": "repeat_provider_payment", "amount": future_exposure["expected_repeat_provider_payment_exposure"], "amount_type": "forecast_exposure"},
    ]

    best_action["synthetic_data_used"] = best_action.get("data_source") == "synthetic"
    missing_data = [item["label"] for item in data_availability if item["status"] in {"Missing", "Insufficient matched history"}]
    # Member identity is used only inside the workbook join and never included in the Groq payload.
    original_fields_used = ["Claim_ID", "Service_Date", "Claim_Status", "CPT", "ICD_10", "Payer", "Provider", "Charge", "Allowed", "Paid", "Patient_Responsibility", "Adjustment"]
    data_provenance = {
        "data_mode": "synthetic_demo" if synthetic_active else "original_only",
        "original_fields_used": original_fields_used,
        "synthetic_fields_used": sorted(synthetic_fields_used),
        "source_workbook": source_claim.get("sourceWorkbook") or "claims_with_dummy_savings_fields.xlsx",
        "synthetic_warning": "Synthetic enrichment data is active. Savings and recovery recommendations are for demonstration only and must not be used for real billing decisions." if synthetic_active else None,
    }
    validated_real_savings = {
        "amount": None,
        "available": False,
        "breakdown": [],
        "reason": "The original claims data does not contain verified contract, appeal-recovery, or patient-accounting evidence for a current recovery amount.",
        "data_source": "Claims_Original",
    }
    synthetic_demo_opportunity = {
        "amount": total_demo_opportunity,
        "available": bool(monetary_opportunities),
        "breakdown": opportunities,
        "warning": "Synthetic demonstration data. This opportunity is for UI and workflow testing only and is not a verified billing recommendation." if synthetic_active else None,
        "data_source": "Dummy_Enrichment" if synthetic_active else None,
    }
    return {
        "forecast_reference": {
            "denial_probability": forecast["denial_risk"].get("probability"),
            "repeat_probability_30d": forecast["repeat_service_risk"].get("probability_30d"),
            "repeat_probability_60d": forecast["repeat_service_risk"].get("probability_60d"),
            "repeat_probability_90d": forecast["repeat_service_risk"].get("probability_90d"),
            "predicted_allowed": forecast["predicted_allowed"].get("value"),
            "predicted_paid": forecast["predicted_paid"].get("value"),
            "confidence": forecast["confidence"].get("score"),
            "peer_sample_size": forecast["confidence"].get("peer_sample_size"),
            "prediction_cutoff_date": recovery.get("prediction_cutoff_date"),
            "model_version": forecast["confidence"].get("model_version"),
            "calculation_version": scenario.get("calculationVersion", CALCULATION_VERSION),
            "source_dataset_hash": scenario.get("sourceDatasetHash"),
            "source_csv_hash": scenario.get("sourceCsvHash"),
        },
        "current_claim_performance": current_performance,
        "validated_real_savings": validated_real_savings,
        "synthetic_demo_opportunity": synthetic_demo_opportunity,
        "current_claim_opportunity": {
            "status": opportunity_status,
            "opportunities": opportunities,
            "total_demo_opportunity": total_demo_opportunity,
            "type": ranked_monetary[0]["type"] if ranked_monetary else opportunities[0]["type"] if opportunities else "none",
            "amount": ranked_monetary[0]["amount"] if ranked_monetary else None,
            "calculation_basis": [
                {"metric": "expected_peer_paid", "value": expected_peer_paid, "formula": "actual allowed × matched-peer median paid-to-allowed rate"},
                {"metric": "potential_underpayment", "value": underpayment if synthetic_active else historical_underpayment, "formula": "max(0, contract expected payment − actual paid − recovered amount)" if synthetic_active else "historical comparison only; not a validated recovery amount"},
                {"metric": "expected_contract_adjustment", "value": expected_contract_adjustment, "formula": "max(0, actual charge − synthetic contract allowed)"},
                {"metric": "excessive_adjustment", "value": excessive_adjustment if synthetic_active else historical_excessive_adjustment, "formula": "max(0, actual adjustment − expected contract adjustment)" if synthetic_active else "historical comparison only; not a validated recovery amount"},
            ],
            "peer_match_level": recovery.get("match_level", "insufficient evidence"),
            "sample_size": recovery.get("sample_size", 0),
            "minimum_sample_size": recovery.get("minimum_sample_size", DEFAULT_MIN_PEERS),
            "limitations": current_limitations,
            "correctable_denial_value": correctable_denial_value,
            "recovered_amount": recovered_amount,
            "patient_balance_opportunity_available": patient_balance_available,
            "patient_balance_reason": patient_balance_reason,
            "data_source": "synthetic" if synthetic_active else "original",
        },
        "future_exposure": future_exposure,
        "avoidable_spend": avoidable_spend,
        "avoidable_repeat_spend": avoidable_spend,
        "best_action": best_action,
        "historical_comparison": current_performance,
        "recurrence_evidence": repeat_basis,
        "forecast_reconciliation_difference": {
            "value": reconciliation.get("reconciliation_difference"),
            "label": "Forecast reconciliation difference",
            "is_savings": False,
        },
        "data_availability": data_availability,
        "data_required_for_stronger_estimate": missing_data,
        "data_provenance": data_provenance,
        "confidence": forecast.get("confidence", {}),
    }


def build_provider_prediction_payload(scenario):
    """Create the UI/API contract with actual facts separated from estimates."""
    selected = scenario.get("selectedClaim") or scenario.get("anchor") or {}
    financial = scenario.get("forecast", {})
    repeat = scenario.get("repeatRisk", {}).get("probabilities", {})
    denial = scenario.get("denialRisk", {})
    confidence = scenario.get("confidenceDetail", {})
    features = scenario.get("features", {})
    avoidable_available = bool(scenario.get("avoidableSpendSupported"))
    avoidable_basis_reason = scenario.get("avoidableSpendBasis", {}).get("reason")
    if scenario.get("peerClaimCount", 0) < DEFAULT_MIN_PEERS:
        unavailable_reason = "The episode does not meet the minimum peer threshold."
    elif scenario.get("longitudinalBasis", {}).get("repeat", {}).get("90", {}).get("member_trials", 0) < 3:
        unavailable_reason = "Not enough mature member recurrence intervals were available."
    else:
        unavailable_reason = "The available episode evidence is insufficient for a reliable estimate."

    actual_facts = {
        "claim_id": selected.get("claimId"),
        "claim_number": selected.get("number"),
        "member_safe_reference": scenario.get("memberReference"),
        "claim_status": selected.get("status"),
        "service_date": selected.get("dos"),
        "payer": selected.get("payer"),
        "billing_provider": selected.get("billingProvider"),
        "billing_provider_npi": selected.get("billingProviderNpi"),
        "rendering_provider": selected.get("renderingProvider"),
        "rendering_provider_npi": selected.get("renderingProviderNpi"),
        "cpt_code": selected.get("cptCode"),
        "cpt_description": selected.get("cptDescription"),
        "diagnosis_code": selected.get("diagnosisCode"),
        "diagnosis_family": scenario.get("diagnosisFamily"),
        "diagnosis_description": selected.get("diagnosisDescription"),
        "place_of_service_code": selected.get("placeOfServiceCode"),
        "place_of_service_description": selected.get("placeOfService"),
        "units": selected.get("units"),
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
        "forecast_context": "next_related_claim",
        "forecast_label": "Forecast for next related claim",
        "charge_basis": financial.get("charge"),
        "predicted_claim_outcome": {
            "value": scenario.get("predictedOutcome", {}).get("value"),
            "display_value": scenario.get("predictedOutcome", {}).get("displayValue"),
            "probability": scenario.get("predictedOutcome", {}).get("probability"),
        },
        "denial_risk": {
            "probability": denial.get("probability"),
            "percentage": round((denial.get("probability") or 0) * 100, 1),
            "level": str(denial.get("level") or "unknown").lower(),
            "basis": denial.get("basis", {}),
        },
        "repeat_service_risk": {
            "probability_30d": repeat.get("30"),
            "probability_60d": repeat.get("60"),
            "probability_90d": repeat.get("90"),
            "level": str(scenario.get("repeatRisk", {}).get("level") or "unknown").lower(),
            "basis": scenario.get("longitudinalBasis", {}).get("repeat", {}),
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
            "reason": None if avoidable_available else (avoidable_basis_reason or unavailable_reason),
            "savings_phase": scenario.get("bestSavingsPhase") if avoidable_available else None,
            "synthetic_demo": bool(scenario.get("avoidableSpendBasis", {}).get("synthetic_demo")),
        },
        "confidence": {
            "score": confidence.get("score", 0),
            "percentage": confidence.get("percentage", 0),
            "level": str(confidence.get("level") or "low").lower(),
            "explanation": confidence.get("explanation"),
            "factors": confidence.get("factors", {}),
            "drivers": confidence.get("drivers", []),
            "penalties": confidence.get("penalties", []),
            "historical_validation": scenario.get("historicalValidation", {}),
            "peer_sample_size": scenario.get("peerClaimCount", 0),
            "peer_episode_count": scenario.get("peerCount", 0),
            "prediction_method": "hierarchical_longitudinal_peer_forecast",
            "model_version": scenario.get("method", MODEL_VERSION),
        },
    }
    peer_statistics = financial.get("peerStatistics", {})
    longitudinal = scenario.get("longitudinalBasis", {})
    denial_basis = denial.get("basis", {})
    prediction_basis = {
        "peer_claims_used": scenario.get("peerClaimCount", 0),
        "peer_episodes_used": scenario.get("peerCount", 0),
        "matching_level": financial.get("peerHierarchy"),
        "fallback_level": scenario.get("peerMatchLevel", 5),
        "fallback_explanation": "Broader historical matching was required." if scenario.get("peerMatchLevel", 5) > 1 else "No broad fallback was required.",
        "prediction_cutoff_date": longitudinal.get("predictionCutoffDate"),
        "model_version": scenario.get("method", MODEL_VERSION),
        "calculation_version": scenario.get("calculationVersion", CALCULATION_VERSION),
        "source_dataset_hash": scenario.get("sourceDatasetHash"),
        "source_csv_hash": scenario.get("sourceCsvHash"),
        "metric_basis": financial.get("metricBasis", {}),
        "historical_peer_denial_rate": denial_basis.get("peer_rate"),
        "member_prior_claims_used": longitudinal.get("priorClaimCount", 0),
        "member_prior_denials": longitudinal.get("priorDeniedCount", 0),
        "member_prior_related_claims": longitudinal.get("priorRelatedClaimCount", 0),
        "member_prior_same_cpt_claims": longitudinal.get("priorSameCptCount", 0),
        "member_financial_claims_used": longitudinal.get("financialHistoryClaimCount", 0),
        "member_financial_match_level": longitudinal.get("financialHistoryMatchLevel"),
        "repeat_observations": longitudinal.get("repeat", {}),
        "median_allowed_rate": peer_statistics.get("medianAllowedRate"),
        "median_paid_to_allowed_rate": peer_statistics.get("medianPaidToAllowedRate"),
        "median_patient_to_allowed_rate": peer_statistics.get("medianPatientToAllowedRate"),
        "median_adjustment_rate": peer_statistics.get("medianAdjustmentRate"),
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
    provider_financials = _provider_financial_metrics(scenario, forecast)
    reconciliation = _build_reconciliation(actual_facts, forecast)
    backtest = _build_backtest(actual_facts, forecast)
    actions = _rank_provider_actions(scenario, actual_facts, forecast, provider_financials, reconciliation)
    savings_opportunity = _build_savings_opportunity(scenario, actual_facts, forecast, provider_financials, reconciliation, None)
    provider_financials["synthetic_demo_opportunity"] = savings_opportunity["synthetic_demo_opportunity"].get("amount")
    provider_financials["validated_recovery_opportunity"] = savings_opportunity["validated_real_savings"].get("amount")
    scenario_map = _provider_scenario_map(scenario, actual_facts, forecast, provider_financials, reconciliation, savings_opportunity)
    supported_actions = actions or [{
        "rank": 1, "code": "insufficient_evidence", "title": "No supported financial intervention", "priority": 0,
        "expected_financial_impact": None, "reason": "No evidence-supported recovery action is available; continue routine monitoring and future pre-submission validation.",
        "affected_claim_ids": [selected.get("claimId")], "operational_owner": "Provider operations", "urgency": "Routine", "supporting_evidence": [selected.get("claimId")],
    }]
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
        "member_prior_claim_count": prediction_basis["member_prior_claims_used"],
        "member_prior_same_cpt_claim_count": prediction_basis["member_prior_same_cpt_claims"],
        "member_financial_claims_used": prediction_basis["member_financial_claims_used"],
        "repeat_observations": prediction_basis["repeat_observations"],
        "provider_financial_metrics": provider_financials,
        "financial_reconciliation": reconciliation,
        "where_provider_money_can_be_saved": savings_opportunity,
    }
    risk_drivers = [{
        "title": item.get("title"), "value": item.get("value"), "risk_direction": item.get("riskDirection"),
        "source_type": item.get("sourceType"), "reason": item.get("reason"), "evidence_ids": item.get("evidenceIds", []),
    } for item in scenario.get("structuredRiskDrivers", [])]
    risk_drivers.extend([
        {
            "title": "Provider payment gap", "value": f"${provider_financials['provider_payment_gap']:,.2f}", "risk_direction": "revenue exposure",
            "source_type": "financial_model_output", "reason": "Predicted allowed minus predicted provider payment.", "evidence_ids": [selected.get("claimId")],
        },
        {
            "title": "Predicted adjustment exposure", "value": f"${forecast['predicted_adjustment']['value']:,.2f}", "risk_direction": "writeoff exposure",
            "source_type": "financial_model_output", "reason": "Robust local and external historical adjustment rates applied to the selected charge basis.", "evidence_ids": [selected.get("claimId")],
        },
    ])
    if reconciliation["warnings"]:
        risk_drivers.append({
            "title": "Financial reconciliation warning", "value": f"${reconciliation['reconciliation_difference']:,.2f}", "risk_direction": "requires review",
            "source_type": "financial_reconciliation", "reason": reconciliation["warnings"][0], "evidence_ids": [selected.get("claimId")],
        })
    return {
        "claim_id": selected.get("claimId"),
        "episode_id": scenario.get("episodeId"),
        "member_reference": scenario.get("memberReference"),
        "actual_claim_facts": actual_facts,
        "forecast": forecast,
        "provider_financial_opportunity_summary": {
            "expected_provider_payment": provider_financials["provider_expected_reimbursement"],
            "expected_contractual_adjustment": provider_financials["expected_contractual_adjustment"],
            "expected_denial_revenue_exposure": provider_financials["expected_denial_exposure"],
            "expected_repeat_provider_payment_exposure": provider_financials["expected_repeat_provider_payment_exposure"],
            "validated_real_savings": savings_opportunity["validated_real_savings"],
            "synthetic_demo_opportunity": savings_opportunity["synthetic_demo_opportunity"],
            "potentially_avoidable_spend": savings_opportunity["avoidable_spend"],
            "opportunity_reason": savings_opportunity["best_action"]["reason"],
            "best_savings_phase": savings_opportunity["best_action"]["stage"],
            "supporting_reason": savings_opportunity["best_action"]["reason"],
            "affected_claim_ids": savings_opportunity["best_action"]["affected_claim_ids"],
            "confidence": forecast["confidence"],
            "responsible_operational_team": savings_opportunity["best_action"]["owner"],
        },
        "provider_financial_metrics": provider_financials,
        "where_provider_money_can_be_saved": savings_opportunity,
        "data_provenance": savings_opportunity["data_provenance"],
        "financial_reconciliation": reconciliation,
        "backtest_against_actual": backtest,
        "provider_money_scenario_map": scenario_map,
        "prediction_basis": prediction_basis,
        "risk_drivers": risk_drivers,
        "recommended_actions": supported_actions,
        "evidence_used": evidence_used,
        "limitations": [
            "This is provider administrative decision support and does not determine medical necessity.",
            "The next-related-claim forecast uses the selected claim's submitted charge as its standardized financial basis and does not use the selected claim's adjudication outcomes.",
            "Only claims earlier than the selected claim are used as member history; later claims are excluded to prevent temporal leakage.",
            savings_opportunity["data_provenance"].get("synthetic_warning") or "Payer authorization and referral requirements must be verified from authoritative payer sources.",
        ],
        "exact_model_output": exact_model_output,
    }
