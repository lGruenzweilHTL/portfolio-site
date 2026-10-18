"""Outcome classification (backend/chat_errors.py): every row of the table,
the Gemini and OpenRouter error bodies, in-stream errors, moderation."""
from __future__ import annotations

import json

import httpx
import pytest

from backend import chat_errors as ce
from backend.chat_errors import classify, extract_error, parse_error_body

GEMINI_429 = json.dumps([{"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}}])
GEMINI_404 = json.dumps([{"error": {"code": 404, "message": "models/gemini-9 is not found for API version v1beta", "status": "NOT_FOUND"}}])
OPENROUTER_429 = json.dumps({"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min"}})
OPENROUTER_MODERATION = json.dumps({"error": {"code": 403, "message": "Your input was flagged", "metadata": {"reasons": ["harassment"], "flagged_input": "..."}}})


@pytest.mark.parametrize("status, kind", [
    (429, ce.RATE_LIMIT),
    (404, ce.MODEL_UNAVAILABLE),
    (500, ce.OVERLOADED),
    (502, ce.OVERLOADED),
    (503, ce.OVERLOADED),
    (504, ce.OVERLOADED),
    (408, ce.TIMEOUT),
    (400, ce.BAD_REQUEST),
    (413, ce.BAD_REQUEST),
    (422, ce.BAD_REQUEST),
    (401, ce.PROVIDER_AUTH),
    (402, ce.PROVIDER_AUTH),
    (403, ce.PROVIDER_AUTH),
])
def test_http_status(status, kind):
    assert classify(status=status, error={"message": "something"}) == kind
    assert classify(status=status) == kind


def test_ok_and_empty():
    assert classify(status=200, finish_reason="stop", text="Hello") == ce.OK
    assert classify(status=200, finish_reason="stop", text="  ") == ce.EMPTY_REPLY
    assert classify(status=200, finish_reason="length", text="") == ce.EMPTY_REPLY


def test_transport_errors():
    req = httpx.Request("POST", "https://x")
    assert classify(exc=httpx.ReadTimeout("slow", request=req)) == ce.TIMEOUT
    assert classify(exc=httpx.ConnectTimeout("slow", request=req)) == ce.TIMEOUT
    assert classify(exc=httpx.ConnectError("dns", request=req)) == ce.NETWORK
    assert classify(exc=httpx.RemoteProtocolError("eof", request=req)) == ce.NETWORK


def test_gemini_array_body():
    err = parse_error_body(GEMINI_429)
    assert err["status"] == "RESOURCE_EXHAUSTED"
    assert classify(status=429, error=err) == ce.RATE_LIMIT
    err = parse_error_body(GEMINI_404.encode())
    assert classify(status=404, error=err) == ce.MODEL_UNAVAILABLE
    assert "not found" in ce.error_text(err)


def test_openrouter_object_body():
    err = parse_error_body(OPENROUTER_429)
    assert err["code"] == 429
    assert classify(status=429, error=err) == ce.RATE_LIMIT


def test_unparseable_body():
    assert parse_error_body("<html>Bad Gateway</html>") is None
    assert parse_error_body(None) is None
    assert classify(status=502, error=None) == ce.OVERLOADED
    assert ce.error_text(None, "<html>Bad Gateway</html>") == "<html>Bad Gateway</html>"


@pytest.mark.parametrize("chunk, kind", [
    ({"error": {"code": 429, "message": "quota"}}, ce.RATE_LIMIT),
    ([{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}], ce.RATE_LIMIT),
    ({"error": {"status": "NOT_FOUND", "message": "gone"}}, ce.MODEL_UNAVAILABLE),
    ({"error": {"code": 503, "message": "The model is overloaded."}}, ce.OVERLOADED),
    ({"error": {"status": "UNAVAILABLE", "message": "try later"}}, ce.OVERLOADED),
    ({"error": {"status": "INTERNAL", "message": "oops"}}, ce.OVERLOADED),
    ({"error": {"message": "no code at all"}}, ce.OVERLOADED),
    ({"error": {"code": 400, "message": "Invalid value"}}, ce.BAD_REQUEST),
    ({"error": {"status": "PERMISSION_DENIED", "message": "API key not valid"}}, ce.PROVIDER_AUTH),
])
def test_in_stream_errors(chunk, kind):
    err = extract_error(chunk)
    assert err is not None
    assert classify(status=200, error=err) == kind


@pytest.mark.parametrize("finish", ["content_filter", "SAFETY", "prohibited_content", "blocklist", "spii", "recitation"])
def test_moderation_finish_reasons(finish):
    assert classify(status=200, finish_reason=finish, text="") == ce.MODERATED
    # Also when part of the reply already streamed.
    assert classify(status=200, finish_reason=finish, text="partial") == ce.MODERATED


@pytest.mark.parametrize("status, error", [
    (400, {"code": 400, "message": "The request was blocked due to SAFETY"}),
    (400, {"message": "Content blocked: PROHIBITED_CONTENT", "status": "INVALID_ARGUMENT"}),
    (403, parse_error_body(OPENROUTER_MODERATION)),
    (200, {"message": "moderation failed"}),
    (400, {"message": "content_filter triggered"}),
])
def test_moderation_errors_win_over_status(status, error):
    assert classify(status=status, error=error) == ce.MODERATED


def test_key_blocked_is_not_moderation():
    err = {"code": 403, "message": "Requests to this API generativelanguage.googleapis.com are blocked.", "status": "PERMISSION_DENIED"}
    # "blocked" alone would read as moderation; a key/project block is auth.
    assert classify(status=403, error={**err, "message": err["message"] + " API_KEY_SERVICE_BLOCKED"}) == ce.PROVIDER_AUTH
    assert classify(status=403, error={"message": "API key blocked"}) == ce.PROVIDER_AUTH


def test_visitor_reasons_cover_every_kind():
    failures = {ce.RATE_LIMIT, ce.MODEL_UNAVAILABLE, ce.OVERLOADED, ce.NETWORK, ce.TIMEOUT,
                ce.BAD_REQUEST, ce.EMPTY_REPLY, ce.PROVIDER_AUTH, ce.MODERATED, ce.NO_KEY}
    assert set(ce.VISITOR_REASON) == failures
    assert ce.VISITOR_REASON[ce.MODERATED] == "moderated"
    assert ce.VISITOR_REASON[ce.PROVIDER_AUTH] == "server_misconfigured"
    for kind in (ce.MODEL_UNAVAILABLE, ce.OVERLOADED, ce.NETWORK, ce.BAD_REQUEST, ce.EMPTY_REPLY):
        assert ce.VISITOR_REASON[kind] == "upstream_error"
