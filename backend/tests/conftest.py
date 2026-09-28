"""Keep ordinary API tests independent of workstation login configuration.

Security contract tests explicitly enable authentication and their own users.
No fixture changes the application outside pytest.
"""
import pytest


@pytest.fixture(autouse=True)
def isolated_api_configuration(monkeypatch, tmp_path):
    from backend.app import app
    monkeypatch.setitem(app.config, "REVIEW_AUTH_REQUIRED", False)
    monkeypatch.setitem(app.config, "LOGIN_RATE_LIMITER", None)
    monkeypatch.setitem(app.config, "SHARED_LOGIN_LIMIT_REQUIRED", False)
    monkeypatch.setitem(app.config, "REVIEW_DB", str(tmp_path / "reviews.sqlite3"))
