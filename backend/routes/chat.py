"""POST /api/chat — SSE-streamed OpenRouter response.

Request:
  {
    "session_id": "<uuid>",          # client-generated, reused across the session
    "message": "user's question",     # <= CHAT_MAX_MESSAGE_CHARS chars
    "turnstile_token": "<token>"      # from the client widget
  }

Response: text/event-stream (SSE). Each event is one line of JSON
parsed by the frontend. See backend/chat.py for the wire format.

Pre-stream errors (rate limit, captcha) return JSON (not SSE) with a
structured `{"reason", "message"}` body so the frontend can render a
friendly error without parsing an exception trace.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..chat import stream_chat_response
from ..config import settings
from ..turnstile import verify_turnstile

log = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    # No max_length here: Pydantic would answer an over-long message with a
    # bare 422 the frontend can't explain. The configurable cap is checked
    # in the route instead; this ceiling only stops absurd bodies.
    message: str = Field(..., min_length=1, max_length=100_000)
    turnstile_token: str | None = None


# In-memory rate limit (per IP). No Redis needed for a portfolio site.
# We keep a small deque of timestamps per IP and reject if too recent.
# A more robust solution is slowapi, but this avoids another dep just for chat.
import time
from collections import deque, defaultdict
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(ip: str, *, max_calls: int, window_seconds: int) -> bool:
    """Returns True if this IP has exceeded the rate limit. Records the call
    either way. The bucket is cleaned of expired entries on every call.
    """
    now = time.monotonic()
    bucket = _RATE_BUCKETS[ip]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_calls:
        return True
    bucket.append(now)
    return False


# User-facing copy for the pre-stream errors. Mirrors the tone of
# FALLBACK_MESSAGES / ERROR_MESSAGES in backend/chat.py — short, in
# character, and points at the email as the next step.
_ROUTE_ERROR_MESSAGES = {
    "rate_limited": "You're sending messages too fast. Wait a moment, or email me.",
    "captcha_failed": "Captcha check failed. Refresh and try again.",
    "message_too_long": "That message is too long ({length} characters, the limit is {limit}). Please shorten it and send it again.",
}


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    # Per-IP rate limit. 10/minute is a reasonable default for a free chatbot.
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip, max_calls=10, window_seconds=60):
        return JSONResponse(
            status_code=429,
            content={"reason": "rate_limited", "message": _ROUTE_ERROR_MESSAGES["rate_limited"]},
        )

    # Length cap. Checked before Turnstile so an over-long message doesn't
    # burn a token, and before the model so it never costs tokens or lands
    # in the history that is resent on every later turn.
    length = len(req.message.strip())
    limit = settings.chat_max_message_chars
    if length > limit:
        return JSONResponse(
            status_code=413,
            content={
                "reason": "message_too_long",
                "message": _ROUTE_ERROR_MESSAGES["message_too_long"].format(length=length, limit=limit),
                "length": length,
                "limit": limit,
            },
        )

    # Turnstile (skipped in dev with TURNSTILE_DEV_BYPASS=1).
    result = await verify_turnstile(req.turnstile_token, expected_action="chat")
    if not result.ok:
        log.info("Turnstile reject: %s", result.error)
        return JSONResponse(
            status_code=403,
            content={"reason": "captcha_failed", "message": _ROUTE_ERROR_MESSAGES["captcha_failed"]},
        )

    ua = request.headers.get("user-agent", "")

    async def event_source() -> AsyncIterator[str]:
        async for chunk in stream_chat_response(req.session_id, req.message, ip, ua):
            yield chunk

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering if any
        },
    )
