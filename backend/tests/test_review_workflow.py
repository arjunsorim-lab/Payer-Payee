from datetime import date, timedelta

import pytest

from backend.review_store import save_review, read_reviews


def plan(available=True):
    return {"available": available, "anchor_service_date": "2026-01-01", "source_data_type": "recorded_claim"}


def test_review_lifecycle_rejects_missing_reason_and_tracks_versions(tmp_path):
    path = tmp_path / "review.sqlite3"
    with pytest.raises(ValueError):
        save_review(path, "r1", {"version": 0, "status": "accepted", "reason": ""}, "alice", plan())
    accepted = save_review(path, "r1", {"version": 0, "status": "accepted", "reason": "Evidence reviewed", "assignee": "care-team"}, "alice", plan())
    assert accepted["version"] == 1 and accepted["financial_status"] == "unverified"
    with pytest.raises(RuntimeError):
        save_review(path, "r1", {"version": 0, "status": "completed", "reason": "Done", "completed_date": "2026-02-01"}, "alice", plan())
    completed = save_review(path, "r1", {"version": 1, "status": "completed", "reason": "Care completed", "completed_date": "2026-02-01"}, "alice", plan())
    assert completed["version"] == 2
    assert read_reviews(path, ["r1"])["r1"]["status"] == "completed"


def test_review_requires_evidence_before_acceptance_and_valid_outcome(tmp_path):
    path = tmp_path / "review.sqlite3"
    with pytest.raises(ValueError):
        save_review(path, "r2", {"version": 0, "status": "accepted", "reason": "Review", "assignee": "team"}, "alice", plan(False))
    accepted = save_review(path, "r2", {"version": 0, "status": "accepted", "reason": "Review", "assignee": "team"}, "alice", plan())
    with pytest.raises(ValueError):
        save_review(path, "r2", {"version": accepted["version"], "status": "completed", "reason": "Done"}, "alice", plan())
    completed = save_review(path, "r2", {"version": accepted["version"], "status": "completed", "reason": "Done", "completed_date": "2026-02-01"}, "alice", plan())
    with pytest.raises(ValueError):
        save_review(path, "r2", {"version": completed["version"], "status": "outcome_recorded", "reason": "Observed", "outcome": "improved", "outcome_date": "2026-02-01"}, "alice", plan())
