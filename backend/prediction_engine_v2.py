"""Deprecated compatibility wrappers for the authoritative payer rule engine.

The Provider Financial Prediction flow now uses ``payer_prediction`` directly.
These wrappers keep old internal imports from creating a second savings model.
"""

from __future__ import annotations

try:
    from .payer_prediction import (
        build_member_payer_cohort_summary,
        build_payer_prediction_for_claim,
        run_payer_temporal_backtest,
    )
except ImportError:
    from payer_prediction import (
        build_member_payer_cohort_summary,
        build_payer_prediction_for_claim,
        run_payer_temporal_backtest,
    )


def build_payer_prediction_for_claim_v2(database, claim_number, observation_days=None):
    """Return the one canonical selected-claim payer prediction.

    ``observation_days`` is accepted only for backward-compatible callers.  The
    authoritative engine always uses the configured 90-day disease episode.
    """
    return build_payer_prediction_for_claim(database, claim_number)


def build_member_payer_prediction(database, member_id, observation_days=None):
    """Return the canonical deduplicated member summary."""
    return build_member_payer_cohort_summary(database, member_id)
