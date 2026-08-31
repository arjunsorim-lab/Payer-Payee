"""Validation and retrospective quality metrics for canonical predictions."""

from __future__ import annotations

from datetime import date
from collections import Counter, defaultdict
from statistics import median
from typing import Any

_VALIDATION_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}


def clear_validation_cache():
    _VALIDATION_CACHE.clear()


class PredictionConsistencyError(RuntimeError):
    code = "PREDICTION_CONSISTENCY_ERROR"

    def __init__(self, details: list[str]):
        super().__init__(self.code)
        self.details = details


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _close(left: Any, right: Any, tolerance: float = 0.01) -> bool:
    return abs(float(left or 0) - float(right or 0)) <= tolerance


def claim_backtest(result: dict[str, Any]) -> dict[str, Any]:
    actual = result["actual_claim_facts"]
    snapshot = result["financial_prediction_snapshot"]
    metrics = {
        "allowed": (
            snapshot["predicted_allowed"],
            _money(actual["allowed"]),
        ),
        "paid": (
            snapshot["predicted_provider_payment"],
            _money(actual["paid"]),
        ),
        "patient_responsibility": (
            snapshot["predicted_patient_responsibility"],
            _money(actual["patient_responsibility"]),
        ),
        "adjustment": (
            snapshot["predicted_contractual_adjustment"],
            _money(actual["adjustment"]),
        ),
    }
    comparison = {}
    for name, (prediction, actual_value) in metrics.items():
        predicted = _money(prediction["value"])
        absolute_error = _money(abs(predicted - actual_value))
        percentage_error = (
            round(absolute_error / abs(actual_value), 6)
            if actual_value
            else None
        )
        comparison[name] = {
            "predicted": predicted,
            "actual": actual_value,
            "absolute_error": absolute_error,
            "percentage_error": percentage_error,
            "prediction_interval": {
                "low": _money(prediction["low"]),
                "high": _money(prediction["high"]),
            },
            "actual_inside_interval": (
                _money(prediction["low"])
                <= actual_value
                <= _money(prediction["high"])
            ),
        }
    return comparison


def validate_prediction_result(
    result: dict[str, Any], *, raise_on_error: bool = True
) -> dict[str, Any]:
    errors = []
    snapshot = result["financial_prediction_snapshot"]
    actual = result["actual_claim_facts"]
    summary = result["supported_money_summary"]
    predicted_allowed = snapshot["predicted_allowed"]["value"]
    predicted_paid = snapshot["predicted_provider_payment"]["value"]
    predicted_avoidable = snapshot["predicted_avoidable_spend"]
    predicted_avoidable_paid = snapshot[
        "predicted_avoidable_provider_payment"
    ]
    probabilities = [
        ("denial_probability", snapshot["denial_probability"]),
        ("repeat_probability_30d", snapshot["repeat_probability_30d"]),
        ("repeat_probability_60d", snapshot["repeat_probability_60d"]),
        ("repeat_probability_90d", snapshot["repeat_probability_90d"]),
        (
            "avoidable_given_repeat_probability",
            predicted_avoidable["avoidable_given_repeat_probability"],
        ),
    ]
    if predicted_allowed < 0 or predicted_allowed > actual["charge"]:
        errors.append("predicted_allowed must be between zero and charge")
    if predicted_paid < 0:
        errors.append("predicted_paid must be non-negative")
    for name, probability in probabilities:
        if probability < 0 or probability > 1:
            errors.append(f"{name} must be between zero and one")
    if not (
        snapshot["repeat_probability_30d"]
        <= snapshot["repeat_probability_60d"]
        <= snapshot["repeat_probability_90d"]
    ):
        errors.append("repeat probabilities must be monotonic")
    denial_exposure = _money(snapshot["denial_probability"] * predicted_paid)
    repeat_allowed = _money(
        snapshot["repeat_probability_90d"] * predicted_allowed
    )
    repeat_paid = _money(
        snapshot["repeat_probability_90d"] * predicted_paid
    )
    checks = [
        (
            "denial exposure",
            snapshot["predicted_denial_revenue_exposure"],
            denial_exposure,
        ),
        (
            "repeat allowed exposure",
            snapshot["predicted_repeat_allowed_exposure"],
            repeat_allowed,
        ),
        (
            "repeat payment exposure",
            snapshot["predicted_repeat_payment_exposure"],
            repeat_paid,
        ),
        (
            "summary denial exposure",
            summary["future_denial_exposure"],
            denial_exposure,
        ),
        (
            "structured future denial exposure",
            snapshot["future_denial_exposure"]["value"],
            denial_exposure,
        ),
        (
            "summary repeat exposure",
            summary["future_repeat_payment_exposure"],
            repeat_paid,
        ),
    ]
    for name, actual_value, expected in checks:
        if not _close(actual_value, expected):
            errors.append(f"{name} does not match the canonical formula")
    if summary["recoverable_now"] < 0:
        errors.append("recoverable money must be non-negative")
    if not _close(
        snapshot["future_denial_exposure"]["denial_probability"],
        snapshot["denial_probability"],
        tolerance=0.000001,
    ):
        errors.append(
            "future denial exposure probability is inconsistent"
        )
    if not _close(
        snapshot["future_denial_exposure"]["predicted_paid"],
        predicted_paid,
    ):
        errors.append("future denial exposure predicted paid is inconsistent")
    if predicted_avoidable["value"] < 0:
        errors.append("predicted avoidable spend must be non-negative")
    if predicted_avoidable_paid["value"] < 0:
        errors.append(
            "predicted avoidable provider payment must be non-negative"
        )
    if predicted_avoidable["value"] > repeat_allowed + 0.01:
        errors.append(
            "predicted avoidable spend exceeds repeat allowed exposure"
        )
    if predicted_avoidable_paid["value"] > repeat_paid + 0.01:
        errors.append(
            "predicted avoidable provider payment exceeds repeat payment exposure"
        )
    for name, prediction in (
        ("predicted avoidable spend", predicted_avoidable),
        ("predicted avoidable provider payment", predicted_avoidable_paid),
    ):
        if not prediction["low"] <= prediction["value"] <= prediction["high"]:
            errors.append(f"{name} interval does not contain its point value")
    formula = _money(
        predicted_avoidable["repeat_probability_90d"]
        * predicted_avoidable["avoidable_given_repeat_probability"]
        * predicted_avoidable["expected_extra_repeat_allowed_cost"]
    )
    if not _close(predicted_avoidable["value"], formula):
        errors.append(
            "predicted avoidable spend does not match the canonical formula"
        )
    paid_formula = _money(
        predicted_avoidable_paid["repeat_probability_90d"]
        * predicted_avoidable_paid["avoidable_given_repeat_probability"]
        * predicted_avoidable_paid["expected_extra_repeat_paid_cost"]
    )
    if not _close(predicted_avoidable_paid["value"], paid_formula):
        errors.append(
            "predicted avoidable provider payment does not match the canonical formula"
        )
    if not _close(
        summary.get("predicted_avoidable_spend"),
        predicted_avoidable["value"],
    ):
        errors.append("summary predicted avoidable spend is inconsistent")
    for opportunity in result["supported_financial_opportunities"]:
        if opportunity["amount"] < 0:
            errors.append(
                f"{opportunity['type']} supported amount must be non-negative"
            )
    scenario_prediction = result.get("scenario_map", {}).get("sections", [])
    model_step = next(
        (section for section in scenario_prediction if section.get("title") == "Financial Prediction"),
        None,
    )
    if model_step:
        model_items = model_step["items"]
        scenario_checks = {
            "predicted_allowed": snapshot["predicted_allowed"]["value"],
            "predicted_provider_payment": predicted_paid,
            "predicted_patient_responsibility": snapshot[
                "predicted_patient_responsibility"
            ]["value"],
            "predicted_contractual_adjustment": snapshot[
                "predicted_contractual_adjustment"
            ]["value"],
            "denial_probability": snapshot["denial_probability"],
            "repeat_probability_90d": snapshot["repeat_probability_90d"],
            "predicted_avoidable_spend": predicted_avoidable["value"],
            "predicted_avoidable_provider_payment": predicted_avoidable_paid[
                "value"
            ],
        }
        for name, expected in scenario_checks.items():
            if not _close(model_items.get(name), expected, tolerance=0.0001):
                errors.append(f"scenario map {name} is inconsistent")
    avoidable_step = next(
        (
            section
            for section in scenario_prediction
            if section.get("step") == "4A"
        ),
        None,
    )
    if avoidable_step:
        items = avoidable_step["items"]
        for name, expected in (
            ("repeat_probability_90d", predicted_avoidable["repeat_probability_90d"]),
            (
                "avoidable_given_repeat_probability",
                predicted_avoidable["avoidable_given_repeat_probability"],
            ),
            (
                "expected_extra_repeat_allowed_cost",
                predicted_avoidable["expected_extra_repeat_allowed_cost"],
            ),
            ("predicted_avoidable_spend", predicted_avoidable["value"]),
        ):
            if not _close(items.get(name), expected, tolerance=0.0001):
                errors.append(
                    f"avoidable scenario pathway {name} is inconsistent"
                )
    money_step = next((section for section in scenario_prediction if section.get("title") == "Financial Opportunity"), None)
    if money_step and isinstance(money_step.get("items"), dict) and not _close(
        money_step["items"].get("recoverable_now"),
        summary["recoverable_now"],
    ):
        errors.append("scenario map recoverable_now is inconsistent")
    action_step = next((section for section in scenario_prediction if section.get("title") == "Best Provider Action"), None)
    if action_step and not _close(
        action_step["items"].get("amount_addressed"),
        summary["best_action"]["amount_addressed"],
    ):
        errors.append("scenario map best-action amount is inconsistent")
    validation = {
        "passed": not errors,
        "fields_checked": [
            "predicted_allowed",
            "predicted_paid",
            "predicted_adjustment",
            "denial_probability",
            "repeat_probability_30d",
            "repeat_probability_60d",
            "repeat_probability_90d",
            "denial_exposure",
            "future_denial_exposure",
            "repeat_allowed_exposure",
            "repeat_payment_exposure",
            "predicted_avoidable_spend",
            "predicted_avoidable_provider_payment",
            "recoverable_now",
            "scenario_map",
        ],
        "details": errors,
    }
    if errors and raise_on_error:
        raise PredictionConsistencyError(errors)
    return validation


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _avoidable_spend_backtest(database) -> dict[str, Any]:
    try:
        from .avoidable_prediction import (
            _avoidable_evidence,
            build_predicted_avoidable_spend,
        )
    except ImportError:
        from avoidable_prediction import (
            _avoidable_evidence,
            build_predicted_avoidable_spend,
        )

    historical = sorted(
        database.historical_claims,
        key=lambda claim: (claim.get("dos", ""), claim.get("claimId", "")),
    )
    dated = [
        (claim, _date(claim.get("dos")))
        for claim in historical
        if _date(claim.get("dos"))
    ]
    latest = max((service_date for _, service_date in dated), default=None)
    grouped = defaultdict(list)
    for claim, service_date in dated:
        grouped[claim.get("episodeId") or claim.get("claimId")].append(
            (claim, service_date)
        )
    rows = []
    zero_reasons = Counter()
    peer_levels = Counter()
    for episode_rows in grouped.values():
        episode_rows.sort(key=lambda item: (item[1], item[0].get("claimId", "")))
        anchor, anchor_date = episode_rows[0]
        if not latest or (latest - anchor_date).days < 90:
            continue
        earlier = [
            claim
            for claim, service_date in dated
            if service_date < anchor_date
        ]
        earlier_allowed = [
            float(claim.get("allowed") or 0)
            for claim in earlier
            if float(claim.get("allowed") or 0) > 0
        ]
        earlier_paid = [
            float(claim.get("paid") or 0)
            for claim in earlier
            if float(claim.get("paid") or 0) > 0
        ]
        if not earlier_allowed or not earlier_paid:
            continue
        forecast = build_predicted_avoidable_spend(
            database,
            anchor,
            median(earlier_allowed),
            median(earlier_paid),
        )
        predicted = forecast["predicted_avoidable_spend"]
        repeats = [
            claim
            for claim, service_date in episode_rows[1:]
            if 0 < (service_date - anchor_date).days <= 90
            and str(
                claim["workbookFields"].get("Related_Claim_Flag") or ""
            ).upper()
            == "Y"
        ]
        avoidable_repeats = [
            claim for claim in repeats if _avoidable_evidence(claim)
        ]
        observed = round(
            sum(float(claim.get("allowed") or 0) for claim in avoidable_repeats),
            2,
        )
        rows.append(
            {
                "predicted": predicted["value"],
                "observed": observed,
                "confidence": predicted["confidence"],
            }
        )
        peer_levels[predicted["peer_level"]] += 1
        if predicted["value"] == 0:
            for reason in predicted.get("zero_reasons") or [
                "Canonical formula mathematically evaluated to zero."
            ]:
                zero_reasons[reason] += 1
    predicted_values = [row["predicted"] for row in rows]
    observed_values = [row["observed"] for row in rows]
    return {
        "evaluated_anchors": len(rows),
        "mean_predicted_avoidable_spend": _mean(predicted_values),
        "median_predicted_avoidable_spend": round(
            median(predicted_values), 6
        )
        if predicted_values
        else 0.0,
        "mae": _mean(
            [
                abs(row["predicted"] - row["observed"])
                for row in rows
            ]
        ),
        "zero_prediction_percentage": round(
            100
            * sum(value == 0 for value in predicted_values)
            / len(predicted_values),
            4,
        )
        if predicted_values
        else 0.0,
        "zero_reasons": dict(zero_reasons),
        "peer_level_distribution": dict(peer_levels),
        "average_confidence": _mean(
            [row["confidence"] for row in rows]
        ),
        "predicted_total": round(sum(predicted_values), 2),
        "observed_total": round(sum(observed_values), 2),
    }


def build_validation_report(database) -> dict[str, Any]:
    # Imported here to avoid a module cycle during canonical result construction.
    try:
        from .financial_engine import build_financial_result
        from .workbook_enrichment import CALCULATION_VERSION, PREDICTION_VERSION
    except ImportError:
        from financial_engine import build_financial_result
        from workbook_enrichment import CALCULATION_VERSION, PREDICTION_VERSION

    cache_key = (
        database.workbook_hash,
        PREDICTION_VERSION,
        CALCULATION_VERSION,
    )
    if cache_key in _VALIDATION_CACHE:
        return _VALIDATION_CACHE[cache_key]
    results = [
        build_financial_result(database, claim["claimId"])
        for claim in database.selectable_claims
    ]
    comparisons = [claim_backtest(result) for result in results]
    metric_names = ["allowed", "paid", "patient_responsibility", "adjustment"]
    financial = {}
    for metric in metric_names:
        rows = [comparison[metric] for comparison in comparisons]
        financial[f"{metric}_mae"] = _mean(
            [row["absolute_error"] for row in rows]
        )
        if metric in {"allowed", "paid"}:
            financial[f"{metric}_mape"] = _mean(
                [
                    row["percentage_error"]
                    for row in rows
                    if row["percentage_error"] is not None
                ]
            )

    actual_denial = [
        1
        if "denied" in result["actual_claim_facts"]["claim_status"].lower()
        or "reject" in result["actual_claim_facts"]["claim_status"].lower()
        else 0
        for result in results
    ]
    denial_probabilities = [
        result["financial_prediction_snapshot"]["denial_probability"]
        for result in results
    ]
    denial_predictions = [
        1 if probability >= 0.5 else 0
        for probability in denial_probabilities
    ]
    true_positive = sum(
        prediction == 1 and actual == 1
        for prediction, actual in zip(denial_predictions, actual_denial)
    )
    false_positive = sum(
        prediction == 1 and actual == 0
        for prediction, actual in zip(denial_predictions, actual_denial)
    )
    false_negative = sum(
        prediction == 0 and actual == 1
        for prediction, actual in zip(denial_predictions, actual_denial)
    )
    denial = {
        "accuracy": _mean(
            [
                float(prediction == actual)
                for prediction, actual in zip(
                    denial_predictions, actual_denial
                )
            ]
        ),
        "precision": round(
            true_positive / (true_positive + false_positive), 6
        )
        if true_positive + false_positive
        else 0.0,
        "recall": round(
            true_positive / (true_positive + false_negative), 6
        )
        if true_positive + false_negative
        else 0.0,
        "brier_score": _mean(
            [
                (probability - actual) ** 2
                for probability, actual in zip(
                    denial_probabilities, actual_denial
                )
            ]
        ),
    }

    repeat_rows = []
    all_claims = list(database.selectable_claims)
    service_dates = [
        parsed
        for parsed in (_date(claim.get("dos")) for claim in all_claims)
        if parsed
    ]
    latest_service_date = max(service_dates, default=None)
    for claim, result in zip(database.selectable_claims, results):
        claim_date = _date(claim.get("dos"))
        if (
            not claim_date
            or not latest_service_date
            or (latest_service_date - claim_date).days < 90
        ):
            continue
        later = [
            candidate
            for candidate in all_claims
            if candidate["memberId"] == claim["memberId"]
            and _date(candidate.get("dos"))
            and 0
            < (_date(candidate.get("dos")) - claim_date).days
            <= 90
            and str(
                candidate["workbookFields"].get("Related_Claim_Flag") or ""
            ).upper()
            == "Y"
        ]
        actual = 1.0 if later else 0.0
        probability = result["financial_prediction_snapshot"][
            "repeat_probability_90d"
        ]
        repeat_rows.append((probability, actual))

    interval_coverage = {
        metric: _mean(
            [
                float(comparison[metric]["actual_inside_interval"])
                for comparison in comparisons
            ]
        )
        for metric in metric_names
    }
    report = {
        "evaluated_claims": len(results),
        "financial": financial,
        "denial": denial,
        "repeat_risk": {
            "evaluated_claims": len(repeat_rows),
            "brier_score_90d": _mean(
                [
                    (probability - actual) ** 2
                    for probability, actual in repeat_rows
                ]
            ),
        },
        "interval_coverage": interval_coverage,
        "avoidable_spend": _avoidable_spend_backtest(database),
        "model_version": PREDICTION_VERSION,
        "calculation_version": CALCULATION_VERSION,
    }
    _VALIDATION_CACHE[cache_key] = report
    return report
