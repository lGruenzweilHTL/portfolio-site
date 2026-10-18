"""Shared fixtures. No test touches the network: Turnstile is bypassed via
settings and the chat provider is mocked with respx.

The app is used without TestClient's context manager, so the startup hooks
(background PDF render, retention task) never run; the `db` fixture does the
part of startup the tests need.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import chat, chat_models, net
from backend.config import settings
from backend.content import load_content
from backend.db.models import init_db

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
MODELS = ["model-a", "model-b", "model-c"]


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    """A known baseline, whatever the local .env says."""
    values = {
        "database_path": str(tmp_path / "test.db"),
        "openrouter_api_key": "test-key",
        "openrouter_base_url": GEMINI_URL,
        "openrouter_models": ",".join(MODELS),
        "turnstile_dev_bypass": True,
        "admin_dev_bypass": False,
        "deploy_webhook_secret": "",
        "deploy_branch": "master",
        "ntfy_enabled": False,
        "trusted_proxy_ips": "",
        "base_url": "https://gruenzweil.cc",
        "chat_model_cooldown_seconds": 60.0,
        "chat_model_unavailable_cooldown_seconds": 3600.0,
        "chat_request_timeout_seconds": 5.0,
        "chat_total_timeout_seconds": 60.0,
        "data_retention_days": 90,
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    return settings


@pytest.fixture(autouse=True)
def clean_state():
    """Reset every piece of in-process state between tests."""
    from backend.routes import chat as chat_route, deploy, feedback

    def reset():
        chat._cooldown_until.clear()
        chat._notified_unavailable.clear()
        chat._last_auth_notify = 0.0
        chat_models.invalidate()
        net._warned_untrusted = False
        for limiter in (chat_route._LIMITER, feedback._LIMITER, deploy._LIMITER):
            limiter.clear()

    reset()
    yield
    reset()


@pytest.fixture
def db(test_settings):
    init_db()
    load_content()
    return test_settings.database_path


@pytest.fixture
def client(db):
    from backend.main import app
    return TestClient(app)


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the backoff sleep in the chat loop; records the requested delays."""
    delays: list[float] = []
    real_sleep = chat.asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(chat.asyncio, "sleep", fake_sleep)
    return delays
