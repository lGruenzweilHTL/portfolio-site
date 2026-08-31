"""POST /api/track — custom event beacon from the client.

Use cases (wire from the frontend as needed):
  - chatbot_opened        (chatbot UI shown)
  - resume_downloaded     (user clicked the Résumé link)
  - contact_clicked       (email link clicked)
  - project_link_clicked  (with {slug} in data)

Body:
  {
    "name": "chatbot_opened",   # event name; 64 chars max, [a-z0-9_]
    "data": { ... }              # optional, small (<= 2KB) JSON
  }

We don't Turnstile-verify this. Custom events are low-value signal; if a
malicious client floods them, it just adds rows to a table only the admin
sees. Rate limit + name validation is the only guard.

Returns 204 No Content on success.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..config import settings
from ..db.models import get_db
from ..main import _visitor_hash

log = logging.getLogger(__name__)

router = APIRouter()


class TrackEvent(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    data: dict | None = None


@router.post("/api/track")
def track_event(event: TrackEvent, request: Request) -> Response:
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    referrer = request.headers.get("referer", "")
    visitor = _visitor_hash(ip, ua)

    data_str = None
    if event.data is not None:
        try:
            data_str = json.dumps(event.data, separators=(",", ":"))[:2048]
        except (TypeError, ValueError):
            data_str = None

    with get_db() as conn:
        conn.execute(
            "INSERT INTO events (kind, path, name, referrer, user_agent, visitor_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "event",
                referrer or "/",
                event.name,
                (referrer[:512]) if referrer else None,
                (ua[:512]) if ua else None,
                visitor,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return Response(status_code=204)
