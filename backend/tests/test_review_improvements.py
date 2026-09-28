from threading import Event

import pytest

from backend.app import page_options
from backend.summary_jobs import SummaryJobs
from backend.patient_comparison import _database_cache_identity


@pytest.mark.parametrize("args", [{"page": "abc", "limit": "NaN"}, {"page": "-2", "limit": "0"}, {"page": None, "limit": None}])
def test_invalid_pagination_is_safe(args):
    assert page_options(args) == (1, 25, 0)


def test_pagination_is_bounded():
    assert page_options({"page": "999999999", "limit": "999999"}) == (1_000_000, 2000, 1_999_998_000)


def test_comparison_cache_identity_tracks_source_content():
    from types import SimpleNamespace
    first = SimpleNamespace(claims=[{"claimId": "same", "totalCharge": 100}])
    second = SimpleNamespace(claims=[{"claimId": "same", "totalCharge": 200}])
    assert _database_cache_identity(first) != _database_cache_identity(second)
    before = _database_cache_identity(first)
    first.claims[0]["totalCharge"] = 300
    assert _database_cache_identity(first) != before


def test_member_audit_history_does_not_score_reference_rows(monkeypatch):
    from types import SimpleNamespace
    from backend.app import app
    row = {"claimId": "REFERENCE", "memberId": "MEMBER", "isHistoricalReference": True,
           "dos": "2026-01-01", "workbookFields": {}}
    database = SimpleNamespace(claims=[row], report={}, member_claims=lambda member: [row],
                               source_banner=lambda: {"workbook_hash": "audit"})
    monkeypatch.setattr("backend.app.configured_workbook_database", lambda: database)

    def forbidden(*args, **kwargs):
        pytest.fail("An audit reference was passed to the financial prediction engine")

    monkeypatch.setattr("backend.app.build_financial_result", forbidden)
    response = app.test_client().get("/api/members/MEMBER/claims")
    assert response.status_code == 200
    assert response.get_json()["items"][0]["isHistoricalReference"] is True


def test_summary_work_is_nonblocking_deduplicated_and_bounded():
    jobs = SummaryJobs(max_entries=1, workers=1)
    release = Event()
    calls = []

    def calculate():
        calls.append(True)
        release.wait(5)
        return {"amount": 123}

    try:
        assert jobs.request("member", calculate)[1] == 202
        assert jobs.request("member", calculate)[1] == 202
        assert jobs.request("other", calculate)[0]["status"] == "busy"
        release.set()
        jobs.jobs["member"].result(timeout=5)
        payload, status = jobs.request("member", calculate)
        assert status == 200
        assert payload["supportedMoneySummary"] == {"amount": 123}
        assert len(calls) == 1
        assert len(jobs.jobs) == 1
    finally:
        release.set()
        jobs.executor.shutdown(wait=True)
