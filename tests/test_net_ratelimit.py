"""Client IP (backend/net.py), the rate limiter, and the routes' 429 bodies."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from backend import net, ratelimit
from backend.config import settings


def make_request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


def test_header_ignored_without_trusted_proxies(caplog):
    req = make_request("10.0.0.5", {"CF-Connecting-IP": "203.0.113.7"})
    assert net.client_ip(req) == "10.0.0.5"
    assert "TRUSTED_PROXY_IPS" in caplog.text
    # Warned once, not per request.
    caplog.clear()
    net.client_ip(req)
    assert "TRUSTED_PROXY_IPS" not in caplog.text


@pytest.mark.parametrize("trusted", ["10.0.0.5", "10.0.0.0/24", " 192.168.1.1 , 10.0.0.0/8 "])
def test_header_trusted_from_proxy(monkeypatch, trusted):
    monkeypatch.setattr(settings, "trusted_proxy_ips", trusted)
    req = make_request("10.0.0.5", {"CF-Connecting-IP": "203.0.113.7"})
    assert net.client_ip(req) == "203.0.113.7"


def test_ipv6_header(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.5")
    req = make_request("10.0.0.5", {"CF-Connecting-IP": "2001:db8::1"})
    assert net.client_ip(req) == "2001:db8::1"


def test_unparseable_header_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.5")
    req = make_request("10.0.0.5", {"CF-Connecting-IP": "not-an-ip, 1.2.3.4"})
    assert net.client_ip(req) == "10.0.0.5"


def test_untrusted_peer_cannot_spoof(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.5")
    req = make_request("10.0.0.99", {"CF-Connecting-IP": "203.0.113.7"})
    assert net.client_ip(req) == "10.0.0.99"


def test_no_client(monkeypatch):
    assert net.client_ip(make_request(None), "unknown") == "unknown"
    assert net.client_ip(make_request(None)) == ""


def test_invalid_trusted_entries_are_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "nonsense,10.0.0.5")
    req = make_request("10.0.0.5", {"CF-Connecting-IP": "203.0.113.7"})
    assert net.client_ip(req) == "203.0.113.7"


def test_rate_limiter_window(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
    limiter = ratelimit.RateLimiter(max_calls=2, window_seconds=60)
    assert not limiter.limited("a")
    assert not limiter.limited("a")
    assert limiter.limited("a")
    assert not limiter.limited("b")  # buckets are per key
    clock["t"] += 61
    assert not limiter.limited("a")


def test_rate_limiter_prunes_idle_buckets(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock["t"])
    limiter = ratelimit.RateLimiter(max_calls=5, window_seconds=60)
    for i in range(50):
        limiter.limited(f"ip{i}")
    assert len(limiter) == 50
    clock["t"] += 120
    limiter.limited("fresh")
    assert len(limiter) == 1


def test_chat_route_429_body(client):
    body = {"session_id": "s", "message": "x" * 5000, "turnstile_token": ""}
    # Over-long messages are rejected after the rate-limit check, without a model call.
    for _ in range(10):
        assert client.post("/api/chat", json=body).status_code == 413
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 429
    assert resp.json()["reason"] == "rate_limited"


def test_feedback_route_429(client):
    for _ in range(5):
        assert client.post("/api/feedback", json={"message": "hi", "email": "bad"}).status_code == 400
    resp = client.post("/api/feedback", json={"message": "hi"})
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate-limited"}

