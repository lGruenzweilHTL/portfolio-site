"""How a single model attempt ended, and what the fallback loop does about it.

Every attempt in backend/chat.py ends in exactly one outcome kind:

  kind               next model?  cooldown  visitor reason
  ok                 (done)       clears    -
  rate_limit         yes          normal    rate_limit
  model_unavailable  yes          long      upstream_error
  overloaded         yes          normal    upstream_error
  network            yes          normal    upstream_error
  timeout            yes          none      timeout
  bad_request        yes          none      upstream_error
  empty_reply        yes          none      upstream_error
  provider_auth      no, stop     none      server_misconfigured
  moderated          no, stop     none      moderated
  no_key             no, stop     none      no_key

No cooldown for timeout, bad_request and empty_reply: those are usually about
this request (long prompt, slow answer, reasoning budget), not the model
being down, and cooling them down lets one bad message degrade the bot for
the next visitor.

`classify()` is a pure function so the tests can cover every row without
HTTP. The error formats differ by provider: Gemini's OpenAI-compatible
endpoint wraps its error object in a JSON array (`[{"error": {...}}]`) with a
string `status` such as RESOURCE_EXHAUSTED; OpenRouter and OpenAI send a bare
`{"error": {...}}` with a numeric `code`, and OpenRouter's moderation 403
carries `error.metadata.reasons`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

OK = "ok"
RATE_LIMIT = "rate_limit"
MODEL_UNAVAILABLE = "model_unavailable"
OVERLOADED = "overloaded"
NETWORK = "network"
TIMEOUT = "timeout"
BAD_REQUEST = "bad_request"
EMPTY_REPLY = "empty_reply"
PROVIDER_AUTH = "provider_auth"
MODERATED = "moderated"
NO_KEY = "no_key"

# Outcomes that end the whole request: trying another model won't help.
STOP_KINDS = frozenset({PROVIDER_AUTH, MODERATED, NO_KEY})
NORMAL_COOLDOWN_KINDS = frozenset({RATE_LIMIT, OVERLOADED, NETWORK})
LONG_COOLDOWN_KINDS = frozenset({MODEL_UNAVAILABLE})

VISITOR_REASON = {
    RATE_LIMIT: "rate_limit",
    MODEL_UNAVAILABLE: "upstream_error",
    OVERLOADED: "upstream_error",
    NETWORK: "upstream_error",
    TIMEOUT: "timeout",
    BAD_REQUEST: "upstream_error",
    EMPTY_REPLY: "upstream_error",
    PROVIDER_AUTH: "server_misconfigured",
    MODERATED: "moderated",
    NO_KEY: "no_key",
}

# Moderation signals. A best guess from the OpenAI, OpenRouter and Gemini
# formats; extend these once real blocks show up in error_detail.
MODERATION_FINISH_REASONS = (
    "content_filter", "safety", "prohibited_content", "blocklist", "spii", "recitation",
)
MODERATION_MARKERS = ("safety", "blocked", "prohibited", "content_filter", "moderation")
# "blocked" also shows up in key and project errors ("Requests to this API
# ... are blocked", API_KEY_SERVICE_BLOCKED). Those are config problems the
# owner must hear about, not refusals.
_NOT_MODERATION_MARKERS = ("api key", "api_key", "permission", "billing", "credit", "quota", "service_blocked")

_STATUS_KIND = {
    "RESOURCE_EXHAUSTED": RATE_LIMIT,
    "NOT_FOUND": MODEL_UNAVAILABLE,
    "UNAVAILABLE": OVERLOADED,
    "INTERNAL": OVERLOADED,
    "UNAUTHENTICATED": PROVIDER_AUTH,
    "PERMISSION_DENIED": PROVIDER_AUTH,
    "INVALID_ARGUMENT": BAD_REQUEST,
    "FAILED_PRECONDITION": BAD_REQUEST,
}


@dataclass
class AttemptOutcome:
    kind: str
    model: str
    http_status: int | None = None
    finish_reason: str | None = None
    provider_message: str | None = None   # raw, truncated to 500 chars
    reasoning: bool = False               # reasoning was streamed (empty_reply diagnosis)
    heuristic: bool = False               # moderated by the two-empty-replies rule

    def detail(self) -> dict:
        """Compact form for chat_messages.error_detail."""
        return {
            "model": self.model,
            "kind": self.kind,
            "status": self.http_status,
            "finish": self.finish_reason,
            "msg": (self.provider_message or "")[:300] or None,
        }


def parse_error_body(raw: bytes | str | None) -> dict | None:
    """The error object from a provider error body, or None.

    Accepts Gemini's array wrapper, a bare `{"error": {...}}`, or an
    in-stream chunk. Returns the inner error dict when there is one.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return extract_error(data)


def extract_error(data: object) -> dict | None:
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        return err
    if isinstance(err, str):
        return {"message": err}
    return None


def error_text(error: dict | None, raw: str | None = None) -> str | None:
    """Human-readable provider message, for logs and the admin view."""
    if error:
        parts = [str(error.get(k)) for k in ("status", "message") if error.get(k)]
        reasons = (error.get("metadata") or {}).get("reasons") if isinstance(error.get("metadata"), dict) else None
        if reasons:
            parts.append(f"reasons={reasons}")
        if parts:
            return ": ".join(parts)[:500]
    if raw:
        return raw.strip()[:500] or None
    return None


def is_moderation_error(error: dict | None) -> bool:
    if not error:
        return False
    metadata = error.get("metadata")
    if isinstance(metadata, dict) and metadata.get("reasons"):
        return True
    text = " ".join(str(error.get(k) or "") for k in ("message", "status", "code", "type")).lower()
    if any(m in text for m in _NOT_MODERATION_MARKERS):
        return False
    return any(m in text for m in MODERATION_MARKERS)


def is_moderation_finish(finish_reason: str | None) -> bool:
    return bool(finish_reason) and finish_reason.lower() in MODERATION_FINISH_REASONS


def _kind_for_code(code: object) -> str | None:
    if isinstance(code, str):
        if code.upper() in _STATUS_KIND:
            return _STATUS_KIND[code.upper()]
        if not code.isdigit():
            return None
        code = int(code)
    if not isinstance(code, int):
        return None
    if code == 429:
        return RATE_LIMIT
    if code == 404:
        return MODEL_UNAVAILABLE
    if code == 408:
        return TIMEOUT
    if code in (401, 402, 403):
        return PROVIDER_AUTH
    if code in (500, 502, 503, 504) or code >= 500:
        return OVERLOADED
    if 400 <= code < 500:
        return BAD_REQUEST
    return None


def classify(
    status: int | None = None,
    error: dict | None = None,
    finish_reason: str | None = None,
    text: str = "",
    exc: BaseException | None = None,
) -> str:
    """Outcome kind for one attempt.

    status: HTTP status of the response (None if no response).
    error: provider error object, from the body or an in-stream chunk.
    finish_reason: last finish_reason seen in the stream.
    text: the reply text streamed so far.
    exc: transport exception, if the request failed outright.
    """
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return TIMEOUT
        return NETWORK
    failed = (status is not None and not 200 <= status < 300) or error is not None
    if failed:
        # Moderation first, so a moderation 400/403 is not bad_request or
        # provider_auth.
        if is_moderation_error(error):
            return MODERATED
        if status is not None and not 200 <= status < 300:
            kind = _kind_for_code(status)
            if kind:
                return kind
        if error:
            for key in ("code", "status"):
                kind = _kind_for_code(error.get(key))
                if kind:
                    return kind
        return OVERLOADED
    if is_moderation_finish(finish_reason):
        return MODERATED
    if not text.strip():
        return EMPTY_REPLY
    return OK
