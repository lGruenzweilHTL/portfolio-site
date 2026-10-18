"""The fallback loop in backend/chat.py against a mocked provider."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
import respx

from backend import chat
from backend.config import settings

from .conftest import GEMINI_URL

URL = f"{GEMINI_URL}/chat/completions"


# --- helpers -----------------------------------------------------------------

def sse(*chunks: dict, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def reply(*pieces: str, finish: str = "stop") -> httpx.Response:
    chunks = [{"choices": [{"delta": {"content": p}, "finish_reason": None}]} for p in pieces]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return httpx.Response(200, content=sse(*chunks), headers={"content-type": "text/event-stream"})


def empty(finish: str = "stop", reasoning: bool = False) -> httpx.Response:
    chunks = []
    if reasoning:
        chunks.append({"choices": [{"delta": {"reasoning": "hmm"}, "finish_reason": None}]})
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return httpx.Response(200, content=sse(*chunks), headers={"content-type": "text/event-stream"})


def gemini_error(status: int, code_name: str, message: str = "err") -> httpx.Response:
    return httpx.Response(status, json=[{"error": {"code": status, "message": message, "status": code_name}}])


class Provider:
    """respx side effect: a queue of responses per model; records calls."""

    def __init__(self, script: dict[str, list]):
        self.script = {m: list(r) for m, r in script.items()}
        self.calls: list[str] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request):
        model = json.loads(request.content)["model"]
        self.calls.append(model)
        self.headers.append(request.headers)
        queue = self.script.get(model) or []
        if not queue:
            raise AssertionError(f"unexpected call for {model}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def run(message: str = "hello", session: str = "s1") -> list[dict | str]:
    events: list[dict | str] = []
    async for raw in chat.stream_chat_response(session, message, "pytest-ua"):
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
        else:
            events.append(raw.strip())
    return events


def final(events) -> dict | None:
    for e in events:
        if isinstance(e, dict) and e["type"] in ("fallback", "error"):
            return e
    return None


def text_of(events) -> str:
    return "".join(e["text"] for e in events if isinstance(e, dict) and e["type"] == "token")


def rows(db_path: str, sql: str, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@pytest.fixture
def router(db):
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def provider(router):
    def make(script):
        p = Provider(script)
        router.post(URL).mock(side_effect=p)
        return p
    return make


# --- loop behaviour ----------------------------------------------------------

async def test_first_model_ok(provider):
    p = provider({"model-a": [reply("Hi ", "there")]})
    events = await run()
    assert p.calls == ["model-a"]
    assert text_of(events) == "Hi there"
    assert final(events) is None
    assert events[-1] == {"type": "done"}


async def test_429_then_ok(provider):
    p = provider({"model-a": [gemini_error(429, "RESOURCE_EXHAUSTED")], "model-b": [reply("ok")]})
    events = await run()
    assert p.calls == ["model-a", "model-b"]
    assert text_of(events) == "ok"
    assert chat.cooldown_remaining("model-a") > 50
    assert chat.cooldown_remaining("model-b") == 0


async def test_404_then_ok_sets_long_cooldown(provider, monkeypatch):
    sent = []
    monkeypatch.setattr(chat, "_notify_config_problem", lambda o: sent.append(o.kind))
    p = provider({"model-a": [gemini_error(404, "NOT_FOUND", "models/model-a is not found")], "model-b": [reply("ok")]})
    events = await run()
    assert p.calls == ["model-a", "model-b"]
    assert text_of(events) == "ok"
    assert chat.cooldown_remaining("model-a") > 3000
    assert sent == ["model_unavailable"]


async def test_503_then_ok(provider):
    p = provider({"model-a": [gemini_error(503, "UNAVAILABLE", "The model is overloaded.")], "model-b": [reply("ok")]})
    await run()
    assert p.calls == ["model-a", "model-b"]
    assert 50 < chat.cooldown_remaining("model-a") <= 60


async def test_timeout_then_ok_without_cooldown(provider):
    p = provider({"model-a": [httpx.ReadTimeout("slow")], "model-b": [reply("ok")]})
    events = await run()
    assert p.calls == ["model-a", "model-b"]
    assert text_of(events) == "ok"
    assert chat.cooldown_remaining("model-a") == 0


async def test_bad_request_and_empty_reply_cause_no_cooldown(provider):
    p = provider({
        "model-a": [httpx.Response(400, json={"error": {"code": 400, "message": "Invalid argument"}})],
        "model-b": [empty(finish="length", reasoning=True)],
        "model-c": [reply("ok")],
    })
    await run()
    assert p.calls == ["model-a", "model-b", "model-c"]
    assert chat._cooldown_until == {}


async def test_401_stops_immediately(provider, monkeypatch):
    sent = []
    monkeypatch.setattr(chat, "_notify_config_problem", lambda o: sent.append(o.kind))
    p = provider({"model-a": [gemini_error(401, "UNAUTHENTICATED", "API key not valid")]})
    events = await run()
    assert p.calls == ["model-a"]
    assert final(events)["reason"] == "server_misconfigured"
    assert "API key" not in final(events)["message"]
    assert sent == ["provider_auth"]
    assert chat._cooldown_until == {}


async def test_moderation_on_first_model_stops_after_one_call(provider):
    p = provider({"model-a": [empty(finish="SAFETY")]})
    events = await run()
    assert p.calls == ["model-a"]
    fb = final(events)
    assert fb == {"type": "fallback", "reason": "moderated", "message": chat.MODERATED_MESSAGE}
    assert chat._cooldown_until == {}


async def test_moderation_error_body_stops(provider):
    p = provider({"model-a": [httpx.Response(400, json=[{"error": {"code": 400, "message": "Request blocked for safety reasons", "status": "INVALID_ARGUMENT"}}])]})
    events = await run()
    assert p.calls == ["model-a"]
    assert final(events)["reason"] == "moderated"


async def test_two_empty_replies_trigger_moderation_heuristic(provider):
    p = provider({"model-a": [empty()], "model-b": [empty()], "model-c": [reply("never")]})
    events = await run()
    assert p.calls == ["model-a", "model-b"]
    assert final(events)["reason"] == "moderated"
    assert chat._cooldown_until == {}


async def test_empty_with_finish_length_is_not_moderation(provider):
    p = provider({"model-a": [empty(finish="length")], "model-b": [empty(finish="length")], "model-c": [reply("ok")]})
    events = await run()
    assert p.calls == ["model-a", "model-b", "model-c"]
    assert text_of(events) == "ok"


async def test_mid_stream_failure_does_not_switch_models(provider):
    broken = sse({"choices": [{"delta": {"content": "Half an "}, "finish_reason": None}]},
                 {"error": {"code": 503, "message": "overloaded"}}, done=False)
    p = provider({"model-a": [httpx.Response(200, content=broken)], "model-b": [reply("other")]})
    events = await run()
    assert p.calls == ["model-a"]
    assert text_of(events) == "Half an "
    assert final(events)["reason"] == "upstream_error"


async def test_block_after_tokens_keeps_partial_out_of_history(provider, db):
    p = provider({"model-a": [reply("Some ", "text", finish="content_filter")]})
    events = await run(session="partial")
    assert p.calls == ["model-a"]
    assert text_of(events) == "Some text"
    assert final(events)["reason"] == "moderated"
    msgs = rows(db, "SELECT role, content, error, model FROM chat_messages WHERE session_id = 'partial' ORDER BY id")
    assert [m["error"] for m in msgs] == ["moderated", "moderated"]
    assert msgs[1]["content"] == chat.MODERATED_MESSAGE
    assert msgs[1]["model"] == "model-a"


async def test_exhausted_list_retries_rate_limited_after_backoff(provider, no_sleep):
    p = provider({
        "model-a": [gemini_error(429, "RESOURCE_EXHAUSTED"), reply("second try")],
        "model-b": [gemini_error(503, "UNAVAILABLE")],
        "model-c": [gemini_error(429, "RESOURCE_EXHAUSTED"), gemini_error(429, "RESOURCE_EXHAUSTED")],
    })
    events = await run()
    assert p.calls == ["model-a", "model-b", "model-c", "model-a"]
    assert no_sleep == [chat._BACKOFF_S]
    assert text_of(events) == "second try"


async def test_exhausted_list_reports_rate_limit(provider, no_sleep):
    p = provider({
        "model-a": [gemini_error(429, "RESOURCE_EXHAUSTED"), gemini_error(429, "RESOURCE_EXHAUSTED")],
        "model-b": [gemini_error(503, "UNAVAILABLE")],
        "model-c": [httpx.ReadTimeout("slow")],
    })
    events = await run()
    assert p.calls == ["model-a", "model-b", "model-c", "model-a"]
    assert final(events)["reason"] == "rate_limit"


async def test_exhausted_list_without_rate_limit_reports_last_reason(provider):
    p = provider({
        "model-a": [gemini_error(503, "UNAVAILABLE")],
        "model-b": [gemini_error(503, "UNAVAILABLE")],
        "model-c": [httpx.ReadTimeout("slow")],
    })
    events = await run()
    assert p.calls == ["model-a", "model-b", "model-c"]
    assert final(events)["reason"] == "timeout"


async def test_total_time_budget_stops_the_loop(router, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(chat.time, "monotonic", lambda: clock["t"])

    def slow_fail(request):
        clock["t"] += 61  # each attempt "takes" longer than the whole budget
        return gemini_error(503, "UNAVAILABLE")

    calls = []
    def side_effect(request):
        calls.append(json.loads(request.content)["model"])
        return slow_fail(request)

    router.post(URL).mock(side_effect=side_effect)
    events = await run()
    assert calls == ["model-a"]
    assert final(events)["reason"] == "upstream_error"


async def test_cooled_down_models_are_skipped(provider):
    chat._cool_down("model-a", 60)
    p = provider({"model-b": [reply("ok")]})
    await run()
    assert p.calls == ["model-b"]


async def test_no_key(provider, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    events = await run()
    assert final(events)["reason"] == "no_key"


async def test_no_enabled_models(db, monkeypatch):
    from backend import chat_models
    for m in chat_models.list_models():
        chat_models.delete_model(m["id"])
    events = await run()
    assert final(events)["reason"] == "no_key"
    assert chat.active_model_label() is None


async def test_openrouter_headers_only_for_openrouter(provider, router, monkeypatch):
    p = provider({"model-a": [reply("ok"), reply("ok")]})
    await run()
    assert "http-referer" not in p.headers[0]
    monkeypatch.setattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1")
    router.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=p)
    await run(session="s2")
    assert p.headers[1]["http-referer"] == "https://gruenzweil.cc"


# --- history and storage -----------------------------------------------------

async def test_moderated_turn_is_excluded_from_next_history(router):
    seen: list[list[dict]] = []

    def side_effect(request):
        body = json.loads(request.content)
        seen.append(body["messages"])
        return empty(finish="SAFETY") if len(seen) == 1 else reply("fine")

    router.post(URL).mock(side_effect=side_effect)
    await run("bad message", session="hist")
    await run("good question", session="hist")
    second = [m["content"] for m in seen[1] if m["role"] != "system"]
    assert second == ["good question"]


async def test_error_detail_and_model_are_stored(provider, db):
    provider({"model-a": [gemini_error(404, "NOT_FOUND", "models/model-a is not found")], "model-b": [reply("answer")]})
    await run(session="store")
    msgs = rows(db, "SELECT role, content, error, model, error_detail FROM chat_messages WHERE session_id = 'store' ORDER BY id")
    assistant = msgs[-1]
    assert assistant["content"] == "answer"
    assert assistant["error"] is None
    assert assistant["model"] == "model-b"
    detail = json.loads(assistant["error_detail"])
    assert detail == [{"model": "model-a", "kind": "model_unavailable", "status": 404, "finish": None,
                       "msg": "NOT_FOUND: models/model-a is not found"}]


async def test_clean_turn_stores_no_detail(provider, db):
    provider({"model-a": [reply("answer")]})
    await run(session="clean")
    assistant = rows(db, "SELECT model, error_detail FROM chat_messages WHERE session_id = 'clean' AND role = 'assistant'")[0]
    assert assistant == {"model": "model-a", "error_detail": None}


async def test_chat_session_stores_no_ip(provider, db):
    provider({"model-a": [reply("answer")]})
    await run(session="noip")
    assert rows(db, "SELECT ip, user_agent FROM chat_sessions WHERE session_id = 'noip'") == [{"ip": None, "user_agent": "pytest-ua"}]


# --- SSE wire format ---------------------------------------------------------

def test_sse_wire_format_over_http(client):
    with respx.mock as r:
        r.post(URL).mock(side_effect=Provider({
            "model-a": [gemini_error(429, "RESOURCE_EXHAUSTED")],
            "model-b": [reply("Hi")],
        }))
        resp = client.post("/api/chat", json={"session_id": "wire", "message": "hello", "turnstile_token": ""})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in resp.text.split("\n\n") if f]
    assert frames[0] == ": keepalive"
    data = [json.loads(f[6:]) for f in frames if f.startswith("data: ")]
    assert data == [
        {"type": "model", "model": "model-b", "provider": "Gemini API"},
        {"type": "token", "text": "Hi"},
        {"type": "done"},
    ]


def test_sse_fallback_and_error_events(client, monkeypatch):
    with respx.mock as r:
        r.post(URL).mock(side_effect=Provider({"model-a": [empty(finish="SAFETY")]}))
        resp = client.post("/api/chat", json={"session_id": "wire2", "message": "x", "turnstile_token": ""})
    data = [json.loads(f[6:]) for f in resp.text.split("\n\n") if f.startswith("data: ")]
    assert data == [{"type": "fallback", "reason": "moderated", "message": chat.MODERATED_MESSAGE}, {"type": "done"}]

    def broken():
        raise RuntimeError("content not loaded")
    monkeypatch.setattr(chat, "get_content", broken)
    resp = client.post("/api/chat", json={"session_id": "wire3", "message": "x", "turnstile_token": ""})
    data = [json.loads(f[6:]) for f in resp.text.split("\n\n") if f.startswith("data: ")]
    assert data[0]["type"] == "error" and data[0]["reason"] == "server_misconfigured"
    assert data[-1] == {"type": "done"}
