"""POST /api/feedback — Turnstile-gated visitor feedback.

Request:
  {
    "name": "optional",
    "email": "optional",
    "message": "required, 1-4000 chars",
    "turnstile_token": "required (or skipped in dev)"
  }

Response: 204 No Content.

Stored in the `feedback` table; surfaced in the admin view (task #8).

Rate limit: 5/min per IP (per settings.feedback_rate_limit).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr

from ..config import settings
from ..db.models import get_db
from ..turnstile import verify_turnstile

log = logging.getLogger(__name__)

router = APIRouter()

_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(ip: str, *, max_calls: int, window_seconds: int) -> bool:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[ip]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_calls:
        return True
    bucket.append(now)
    return False


class FeedbackRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)  # RFC 5321 max
    message: str = Field(..., min_length=1, max_length=4000)
    turnstile_token: str | None = None


@router.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip, max_calls=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="rate-limited")

    # Soft email format check if provided (EmailStr would 422 on invalid; we
    # accept the slight friction because the form is optional-email).
    if req.email and "@" not in req.email:
        raise HTTPException(status_code=400, detail="invalid email")

    result = await verify_turnstile(req.turnstile_token)
    if not result.ok:
        log.info("Turnstile reject on feedback: %s", result.error)
        raise HTTPException(status_code=403, detail="captcha-failed")

    ua = request.headers.get("user-agent", "")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO feedback (name, email, message, ip, user_agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                req.name or None,
                req.email or None,
                req.message,
                ip,
                (ua[:512]) if ua else None,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    log.info("Feedback received from %s (%s chars)", ip, len(req.message))

    # Notify. We send the body because the whole point of the form is
    # that it warrants a reply. Name/email are first lines if present so
    # the phone preview shows the sender.
    try:
        from ..notify import notify
        lines = []
        if req.name:
            lines.append(f"From: {req.name}")
        if req.email:
            lines.append(f"Email: {req.email}")
        lines.append("")
        lines.append(req.message)
        notify(
            title="Portfolio feedback",
            message="\n".join(lines),
            priority=3,  # default
            tags=("inbox", "speech_balloon"),
        )
    except Exception:  # noqa: BLE001 — never let notify break the response
        log.exception("notify() raised (shouldn't happen — it's fire-and-forget)")

    return Response(status_code=204)
