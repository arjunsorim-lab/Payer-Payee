"""Scale readiness at 100,000 claims.

Requirement 6 asks for performance tests using at least 100,000 claims and
multiple concurrent requests, measuring API latency, memory use, cache size and
initial page-load time, with pagination and bounded caches.

The test builds a lightweight in-memory claims source shaped like the workbook
repository, patches it into the Flask app, then exercises the real HTTP handlers
with the test client. Set ``SCALE_CLAIM_COUNT`` to change the volume.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import tracemalloc

import pytest

from backend import bounded_cache
from backend.app import app as flask_app

CLAIM_COUNT = int(os.getenv("SCALE_CLAIM_COUNT", "100000"))
MEMBER_COUNT = 500
# Budgets are deliberately generous so the test detects real regressions rather
# than machine-to-machine variance.
P95_LATENCY_BUDGET_SECONDS = 5.0
# The measured initial page-load time is printed for every run. The budget is
# configurable because the full-list preload is a known scaling limit: the
# browser currently pages through every claim, so a production target requires
# lazy, on-demand paging in the UI rather than a hard-coded raised ceiling.
PAGE_LOAD_BUDGET_SECONDS = float(os.getenv("SCALE_PAGE_LOAD_BUDGET_SECONDS", "5"))
CONCURRENCY_BUDGET_SECONDS = 120.0
PAGE_SIZE = 2000

_SHARED_FIELDS = {"Reason_Code": "RECORDED", "Synthetic_Flag": "N"}


class _ScaleDatabase:
    """Minimal repository with the surface the claims endpoints use."""

    def __init__(self, count):
        diagnoses = ["I10", "E11.9", "J44.9", "N39.0", "M54.5"]
        self.selectable_claims = tuple(
            {
                "claimId": f"CLM{i:08d}",
                "number": f"CLM{i:08d}",
                "patient": f"Test Member {i % MEMBER_COUNT}",
                "patientFirstName": "Test", "patientLastName": f"Member {i % MEMBER_COUNT}",
                "patientResp": 10.0, "adjustment": 20.0,
                "placeOfServiceCode": "11", "placeOfService": "Office",
                "memberId": f"MBR{i % MEMBER_COUNT:05d}",
                "dos": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "cptCode": ["99213", "99214", "87086", "80053", "97110"][i % 5],
                "cptDescription": ["Office visit", "Extended visit", "Urine culture", "Metabolic panel", "Therapy"][i % 5],
                "diagnosisCode": diagnoses[i % 5],
                "diagnosisDescription": f"Condition {i % 5}",
                "payer": ["Acme Health", "Beta Plan", "Gamma Care"][i % 3],
                "billingProvider": f"Group {i % 7}",
                "totalCharge": 100.0 + (i % 50),
                "allowed": 80.0 + (i % 40),
                "paid": 70.0 + (i % 30),
                "status": "Processed as Primary",
                "units": 1 + (i % 3),
                "filingIndicator": "Commercial",
                "episodeId": f"EP{i % 1000:05d}",
                "workbookFields": _SHARED_FIELDS,
                "isHistoricalReference": False,
            }
            for i in range(count)
        )
        self.claims = self.selectable_claims
        self.directory_claims = self.directory_selectable_claims = tuple(sorted(self.claims, key=lambda row: (row["dos"], row["claimId"]), reverse=True))
        self._members = {}
        for row in self.selectable_claims:
            self._members.setdefault(row["memberId"], []).append(row)
        self.report = {
            "synthetic": True,
            "workbook_hash": "scale-hash",
            "prediction_version": "scale",
            "selectable_claim_count": count,
        }
        self.workbook_hash = "scale-hash"

    def source_banner(self):
        return {"workbook_name": "scale.xlsx", "workbook_hash": "scale-hash",
                "selectable_claim_count": len(self.selectable_claims)}

    def member_claims(self, member_id):
        return self._members.get(member_id, [])

    def find_claim(self, claim_id, selectable_only=True):
        try:
            return self.selectable_claims[int(claim_id[3:])]
        except (ValueError, TypeError, IndexError):
            return None


@pytest.fixture(scope="module")
def scale_database():
    return _ScaleDatabase(CLAIM_COUNT)


@pytest.fixture()
def client(scale_database, monkeypatch):
    monkeypatch.setattr("backend.app.configured_workbook_database", lambda: scale_database)
    monkeypatch.setattr("backend.app.connect_mongo", lambda: pytest.fail("MongoDB must not be used"))
    original = dict(flask_app.config)
    flask_app.config.update(REVIEW_AUTH_REQUIRED=False, REVIEW_USERS={}, MAX_CONTENT_LENGTH=1024 * 1024)
    with flask_app.test_client() as test_client:
        yield test_client
    flask_app.config.clear()
    flask_app.config.update(original)


def _percentile(values, percentile):
    ordered = sorted(values)
    index = min(int(round((percentile / 100) * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def _fetch_page(client, page, limit=PAGE_SIZE):
    response = client.get(
        f"/api/claims?page={page}&limit={limit}&compact=true&selectableOnly=true"
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_json()


def test_recommendations_require_clinical_history_and_label_their_evidence(scale_database):
    from backend.intervention_plans import build_intervention_plan
    from backend.evidence import RECOMMENDATION
    claim = scale_database.selectable_claims[0]
    plan = build_intervention_plan(claim)
    assert plan["status"] == "insufficient_evidence"
    assert not plan["available"]
    assert plan["evidence_type"] == RECOMMENDATION
    assert "treatment_history" in plan["missing_evidence"]


def test_pagination_bounds_the_response_size(client):
    first = _fetch_page(client, 1, limit=10)
    assert first["limit"] == 10
    assert len(first["items"]) == 10
    assert first["total"] == CLAIM_COUNT

    last = _fetch_page(client, CLAIM_COUNT // 10, limit=10)
    assert isinstance(last["items"], list) and len(last["items"]) <= 10

    # An oversized limit is capped rather than honoured.
    capped = client.get("/api/claims?page=1&limit=999999&compact=true&selectableOnly=true")
    assert capped.status_code == 200
    assert capped.get_json()["limit"] <= PAGE_SIZE

    # Every claim carries its provenance label, so the UI can never present demo
    # data as recorded care.
    labelled = first["items"][0].get("evidence_source") or first["items"][0].get("evidence")
    assert labelled and labelled["evidence_type"] in {
        "recorded_claim_fact", "synthetic_demonstration",
    }
    assert labelled["evidence_label"]
    assert first["evidence_legend"]


def test_api_latency_memory_and_cache_size(client):
    # Warm one page so first-call import costs are excluded from the measurement.
    _fetch_page(client, 1, limit=100)
    bounded_cache.registry_stats(_cache_registry())

    tracemalloc.start()
    latencies = []
    for page in range(1, 11):
        started = time.perf_counter()
        _fetch_page(client, page, limit=100)
        latencies.append(time.perf_counter() - started)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    print(json.dumps({
        "measured": "single-request latency",
        "claims": CLAIM_COUNT,
        "p50_seconds": round(p50, 4),
        "p95_seconds": round(p95, 4),
        "peak_traced_memory_bytes": peak_bytes,
    }))
    assert p95 < P95_LATENCY_BUDGET_SECONDS, f"p95 latency {p95:.2f}s exceeds budget"

    stats = bounded_cache.registry_stats(_cache_registry())
    for name, summary in stats.items():
        if summary.get("max_size"):
            assert summary["size"] <= summary["max_size"], f"{name} exceeded its bound"
    print(json.dumps({"measured": "bounded cache sizes", "caches": stats}))
    assert stats, "expected at least one bounded cache"


def test_concurrent_requests_stay_within_budget(scale_database, monkeypatch):
    # Flask's test client shares a context stack, so each worker gets its own.
    monkeypatch.setattr("backend.app.configured_workbook_database", lambda: scale_database)
    original = dict(flask_app.config)
    flask_app.config.update(REVIEW_AUTH_REQUIRED=False, REVIEW_USERS={})

    def worker(index):
        with flask_app.test_client() as worker_client:
            started = time.perf_counter()
            page = _fetch_page(worker_client, (index % 5) + 1, limit=200)
            return time.perf_counter() - started, page["total"]

    try:
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(32)))
    finally:
        flask_app.config.clear()
        flask_app.config.update(original)
    elapsed = time.perf_counter() - started
    print(json.dumps({
        "measured": "concurrent requests",
        "requests": len(results),
        "workers": 8,
        "wall_seconds": round(elapsed, 4),
        "max_request_seconds": round(max(duration for duration, _ in results), 4),
    }))
    assert all(total == CLAIM_COUNT for _, total in results)
    assert elapsed < CONCURRENCY_BUDGET_SECONDS, f"concurrency wall time {elapsed:.2f}s exceeds budget"


def test_first_page_response_time(client):
    """Measure first-page API latency, separately from full browser rendering."""
    started = time.perf_counter()
    first = _fetch_page(client, 1)
    elapsed = time.perf_counter() - started
    item_count = len(first["items"])
    print(json.dumps({
        "measured": "first claims page API response",
        "claims": CLAIM_COUNT,
        "requests": 1,
        "items_returned": item_count,
        "total_seconds": round(elapsed, 4),
    }))
    assert item_count == PAGE_SIZE
    assert first["total"] == CLAIM_COUNT
    assert elapsed < PAGE_LOAD_BUDGET_SECONDS, f"page load {elapsed:.2f}s exceeds budget"


def _cache_registry():
    """Return the bounded caches the engines expose, without importing lazily."""
    registry = {}
    from backend import (
        avoidable_prediction,
        claim_patterns,
        financial_engine,
        payer_prediction,
        prediction_validation,
        workbook_llm,
        workbook_rag,
    )
    registry.update({
        "financial_engine._RESULT_CACHE": getattr(financial_engine, "_RESULT_CACHE", {}),
        "avoidable_prediction._OBSERVATION_CACHE": avoidable_prediction._OBSERVATION_CACHE,
        "avoidable_prediction._CACHE": getattr(avoidable_prediction, "_CACHE", {}),
        "payer_prediction._COHORT_EPISODE_CACHE": getattr(payer_prediction, "_COHORT_EPISODE_CACHE", {}),
        "payer_prediction._MEMBER_PAYER_SUMMARY_CACHE": getattr(payer_prediction, "_MEMBER_PAYER_SUMMARY_CACHE", {}),
        "claim_patterns._PEER_CACHE": getattr(claim_patterns, "_PEER_CACHE", {}),
        "claim_patterns._EARLIER_CACHE": getattr(claim_patterns, "_EARLIER_CACHE", {}),
        "prediction_validation._VALIDATION_CACHE": getattr(prediction_validation, "_VALIDATION_CACHE", {}),
        "workbook_llm._ANALYSIS_CACHE": getattr(workbook_llm, "_ANALYSIS_CACHE", {}),
        "workbook_rag._CACHE": getattr(workbook_rag, "_CACHE", {}),
    })
    return registry


def test_complete_initial_directory_transfer(client):
    """Measure all pages required by the current UI; first-page latency is separate."""
    started = time.perf_counter()
    received = 0
    for page in range(1, (CLAIM_COUNT + PAGE_SIZE - 1) // PAGE_SIZE + 1):
        received += len(_fetch_page(client, page)['items'])
    elapsed = time.perf_counter() - started
    import resource
    import sys
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = rss if sys.platform == 'darwin' else rss * 1024
    print(json.dumps({'measured': 'complete initial directory API transfer', 'claims': CLAIM_COUNT,
        'received': received, 'seconds': round(elapsed, 4), 'process_peak_rss_bytes': rss_bytes,
        'limitation': 'Flask HTTP handler transfer; browser render is measured separately'}))
    assert received == CLAIM_COUNT
    assert elapsed < 30


def test_cache_eviction_under_concurrent_writes():
    cache = bounded_cache.BoundedCache(64)
    def put(index):
        cache[index] = {'claim_id': index, 'payload': 'x' * 1024}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(put, range(10000)))
    assert len(cache) == 64
    assert cache.evictions == 9936
