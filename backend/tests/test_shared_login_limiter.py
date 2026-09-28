from concurrent.futures import ThreadPoolExecutor

import fakeredis
import pytest

from backend.login_limiter import RedisLoginLimiter


def test_workers_share_atomic_limit_and_expiring_keys():
    server = fakeredis.FakeServer()
    clients = [fakeredis.FakeRedis(server=server) for _ in range(2)]
    workers = [RedisLoginLimiter(client) for client in clients]
    with ThreadPoolExecutor(max_workers=10) as pool:
        allowed = list(pool.map(lambda i: workers[i % 2].allow("192.0.2.1"), range(20)))
    assert sum(allowed) == 5
    keys = clients[0].keys("payerpayee:login:*")
    assert len(keys) == 1
    assert 0 < clients[0].ttl(keys[0]) <= 900
    assert b"192.0.2.1" not in keys[0]
    assert workers[1].allow("192.0.2.2")
    clients[0].expire(keys[0], -1)
    assert workers[0].allow("192.0.2.1")


@pytest.mark.parametrize("missing", [True, False])
def test_production_login_fails_closed_without_shared_protection(monkeypatch, missing):
    from backend.app import app
    monkeypatch.setitem(app.config, "SHARED_LOGIN_LIMIT_REQUIRED", True)

    class BrokenLimiter:
        def allow(self, address):
            raise ConnectionError("service unavailable")

    monkeypatch.setitem(app.config, "LOGIN_RATE_LIMITER", None if missing else BrokenLimiter())
    response = app.test_client().post("/api/login", json={"username": "admin", "password": "test"})
    assert response.status_code == 503
    assert "service unavailable" not in response.get_data(as_text=True)


def test_login_rejects_attempt_after_shared_limit(monkeypatch):
    from backend.app import app
    limiter = RedisLoginLimiter(fakeredis.FakeRedis())
    for _ in range(5):
        assert limiter.allow("127.0.0.1")
    monkeypatch.setitem(app.config, "LOGIN_RATE_LIMITER", limiter)
    response = app.test_client().post("/api/login", json={"username": "admin", "password": "test"})
    assert response.status_code == 429
