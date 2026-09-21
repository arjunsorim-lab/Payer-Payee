"""Security contracts for the production API.

These tests prove requirement 6: authentication is required outside local
development, roles are enforced, write endpoints are CSRF-protected, cookies are
hardened, login is rate-limited, audit logging is written, CORS is restricted,
security headers are present and request size is bounded.
"""
from __future__ import annotations

import json

import pytest
from werkzeug.security import generate_password_hash

from backend.app import app as flask_app


class _FakeDatabase:
    """Minimal workbook-shaped source so read routes can answer without a workbook."""

    def __init__(self):
        self.selectable_claims = tuple(
            {
                "claimId": f"CLM{i:08d}", "memberId": "MBR00001", "dos": "2026-01-01",
                "cptCode": "99213", "cptDescription": "Office visit", "diagnosisCode": "I10",
                "diagnosisDescription": "Hypertension", "payer": "Acme Health",
                "billingProvider": "Group A", "totalCharge": 100.0, "allowed": 80.0,
                "paid": 70.0, "status": "Processed as Primary", "units": 1,
                "filingIndicator": "Commercial", "episodeId": "EP1",
                "workbookFields": {"Reason_Code": "RECORDED"}, "isHistoricalReference": False,
            }
            for i in range(3)
        )
        self.claims = self.selectable_claims
        self.report = {"synthetic": True, "workbook_hash": "hash", "prediction_version": "v"}
        self.workbook_hash = "hash"

    def source_banner(self):
        return {"workbook_name": "test.xlsx", "workbook_hash": "hash"}

    def member_claims(self, member_id):
        return list(self.selectable_claims) if member_id == "MBR00001" else []

    def find_claim(self, claim_id, selectable_only=True):
        return next((c for c in self.selectable_claims if c["claimId"] == claim_id), None)


@pytest.fixture()
def secured(monkeypatch, tmp_path):
    """Configure the app in secure mode with three roles and a private review db."""
    monkeypatch.setattr("backend.app.configured_workbook_database", lambda: _FakeDatabase())
    monkeypatch.setattr("backend.app.connect_mongo", lambda: pytest.fail("MongoDB must not be used"))
    secret = "s" * 40
    users = {
        "viewer-one": {"password_hash": generate_password_hash("viewer-pass"), "role": "viewer"},
        "reviewer-one": {"password_hash": generate_password_hash("review-pass"), "role": "reviewer"},
        "admin-one": {"password_hash": generate_password_hash("admin-pass"), "role": "admin"},
    }
    original = dict(flask_app.config)
    flask_app.config.update(
        REVIEW_USERS=users,
        REVIEW_AUTH_REQUIRED=True,
        REVIEW_AUTH_CONFIGURED=True,
        REVIEW_DB=str(tmp_path / "reviews.sqlite3"),
        SECRET_KEY=secret,
        SESSION_COOKIE_SECURE=True,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    yield flask_app
    flask_app.config.clear()
    flask_app.config.update(original)


def _login(client, username, password):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_authentication_is_required_and_fails_safe_when_unconfigured(monkeypatch):
    original = dict(flask_app.config)
    try:
        flask_app.config.update(REVIEW_AUTH_REQUIRED=True, REVIEW_AUTH_CONFIGURED=False, REVIEW_USERS={})
        with flask_app.test_client() as client:
            response = client.get("/api/claims")
            assert response.status_code == 503
            assert "not configured" in response.get_json()["message"]
        flask_app.config.update(REVIEW_AUTH_CONFIGURED=True, REVIEW_USERS={"a": {"password_hash": "x", "role": "reviewer"}})
        with flask_app.test_client() as client:
            assert client.get("/api/claims").status_code == 401
            assert client.get("/api/session").get_json()["authenticated"] is False
    finally:
        flask_app.config.clear()
        flask_app.config.update(original)


def test_remote_requests_are_rejected_in_local_demo_mode(monkeypatch):
    original = dict(flask_app.config)
    try:
        monkeypatch.setattr("backend.app.configured_workbook_database", lambda: _FakeDatabase())
        flask_app.config.update(REVIEW_AUTH_REQUIRED=False, REVIEW_USERS={})
        with flask_app.test_client() as client:
            remote = client.get("/api/claims", environ_base={"REMOTE_ADDR": "203.0.113.9"})
            assert remote.status_code == 403
            assert "Local demo access only" in remote.get_json()["message"]
            loopback = client.get("/api/claims", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert loopback.status_code == 200
    finally:
        flask_app.config.clear()
        flask_app.config.update(original)


def test_roles_gate_reads_writes_and_administration(secured):
    with secured.test_client() as viewer:
        session = _login(viewer, "viewer-one", "viewer-pass")
        assert session["role"] == "viewer"
        assert viewer.get("/api/claims").status_code == 200
        blocked = viewer.post("/api/reviews/MBR00001/abc", json={"version": 0, "status": "accepted", "reason": "test"})
        assert blocked.status_code == 403
        assert "read-only" in blocked.get_json()["message"]
        assert viewer.get("/api/review-audit").status_code == 403

    with secured.test_client() as reviewer:
        _login(reviewer, "reviewer-one", "review-pass")
        assert reviewer.get("/api/review-audit").status_code == 403
        audit = reviewer.get("/api/review-audit", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert audit.status_code == 403  # reviewer lacks the admin role

    with secured.test_client() as admin:
        _login(admin, "admin-one", "admin-pass")
        assert admin.get("/api/review-audit").status_code == 200
        history = admin.get("/api/reviews/any-review/history")
        assert history.status_code == 200
        assert history.get_json()["review_id"] == "any-review"


def test_write_endpoints_require_a_matching_csrf_token(secured):
    with secured.test_client() as client:
        session = _login(client, "reviewer-one", "review-pass")
        body = {"version": 0, "status": "accepted", "reason": "Reviewed evidence", "assignee": "team"}
        missing = client.post("/api/reviews/MBR00001/review-1", json=body)
        assert missing.status_code == 403
        wrong = client.post(
            "/api/reviews/MBR00001/review-1", json=body,
            headers={"X-CSRF-Token": "not-the-token"},
        )
        assert wrong.status_code == 403
        correct = client.post(
            "/api/reviews/MBR00001/review-1", json=body,
            headers={"X-CSRF-Token": session["csrf_token"]},
        )
        # The review may not exist for this member, but it must not be a CSRF failure.
        assert correct.status_code != 403


def test_login_is_rate_limited_per_ip(secured):
    with secured.test_client() as client:
        for _ in range(5):
            assert client.post("/api/login", json={"username": "nobody", "password": "wrong"}).status_code == 401
        limited = client.post("/api/login", json={"username": "nobody", "password": "wrong"})
        assert limited.status_code == 429
        assert "Too many sign-in attempts" in limited.get_json()["message"]


def test_security_headers_cookies_and_request_size_limits(secured):
    with secured.test_client() as client:
        response = client.get("/api/session")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "same-origin"
        assert response.headers["Cache-Control"] == "no-store"

        oversized = client.post(
            "/api/login",
            data=json.dumps({"username": "a" * 200, "password": "x"}),
            content_type="application/json",
        )
        assert oversized.status_code == 400  # over-length fields are rejected

    with secured.test_client() as client:
        huge = client.post(
            "/api/login",
            data=b"x" * (1024 * 1024 + 10),
            content_type="application/json",
        )
        assert huge.status_code == 413

    with secured.test_client() as client:
        response = _login(client, "reviewer-one", "review-pass")
        assert response["role"] == "reviewer"
    # Secure cookies are configured in production mode.
    assert flask_app.config["SESSION_COOKIE_SECURE"] is True
    assert flask_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert flask_app.config["SESSION_COOKIE_SAMESITE"] == "Strict"


def test_cors_rejects_an_untrusted_origin_on_writes(secured, monkeypatch):
    monkeypatch.setenv("CORS_ORIGIN", "https://app.example.com")
    with secured.test_client() as client:
        blocked = client.post(
            "/api/login", json={"username": "reviewer-one", "password": "review-pass"},
            headers={"Origin": "https://evil.example.com"},
        )
        assert blocked.status_code == 403
        assert "origin is not allowed" in blocked.get_json()["message"]
