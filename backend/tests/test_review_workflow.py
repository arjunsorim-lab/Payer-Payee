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
        save_review(path, "r1", {"version": 0, "status": "completed", "reason": "Done", "completed_date": "2026-02-01", "outcome_notes": "Follow-up assessment documented"}, "alice", plan())
    completed = save_review(path, "r1", {"version": 1, "status": "completed", "reason": "Care completed", "completed_date": "2026-02-01", "outcome_notes": "Follow-up assessment documented"}, "alice", plan())
    assert completed["version"] == 2
    assert read_reviews(path, ["r1"])["r1"]["status"] == "completed"


def test_review_requires_evidence_before_acceptance_and_valid_outcome(tmp_path):
    path = tmp_path / "review.sqlite3"
    with pytest.raises(ValueError):
        save_review(path, "r2", {"version": 0, "status": "accepted", "reason": "Review", "assignee": "team"}, "alice", plan(False))
    accepted = save_review(path, "r2", {"version": 0, "status": "accepted", "reason": "Review", "assignee": "team"}, "alice", plan())
    with pytest.raises(ValueError):
        save_review(path, "r2", {"version": accepted["version"], "status": "completed", "reason": "Done"}, "alice", plan())
    completed = save_review(path, "r2", {"version": accepted["version"], "status": "completed", "reason": "Done", "completed_date": "2026-02-01", "outcome_notes": "Follow-up assessment documented"}, "alice", plan())
    with pytest.raises(ValueError):
        save_review(path, "r2", {"outcome_notes": "", "version": completed["version"], "status": "outcome_recorded", "reason": "Observed", "outcome": "improved", "outcome_date": "2026-02-01"}, "alice", plan())


def test_assignment_revisit_and_completion_requirements_are_enforced(tmp_path):
    path = tmp_path / "review.sqlite3"
    with pytest.raises(ValueError):
        save_review(path, "r3", {"version": 0, "status": "assigned", "reason": "Need owner", "assignee": ""}, "alice", plan())
    assigned = save_review(path, "r3", {"version": 0, "status": "assigned", "reason": "Need owner", "assignee": "team-a"}, "alice", plan())
    with pytest.raises(ValueError):
        save_review(path, "r3", {"version": assigned["version"], "status": "deferred", "reason": "Needs follow-up", "assignee": "team-a"}, "alice", plan())
    deferred = save_review(path, "r3", {"version": assigned["version"], "status": "deferred", "reason": "Needs follow-up", "assignee": "team-a", "due_date": "2026-02-10"}, "alice", plan())
    assert deferred["due_date"] == "2026-02-10"
    with pytest.raises(ValueError):
        save_review(path, "r3", {"version": deferred["version"], "status": "completed", "reason": "Finished", "assignee": "team-a", "completed_date": "2026-03-01"}, "alice", plan())
