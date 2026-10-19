"""Delete personal data older than DATA_RETENTION_DAYS (default 90).

Runs once at startup and then every 24 hours (started by main.py). Covers
chat messages and sessions, feedback, analytics events and the secure-link
access log. `secure_links` themselves hold no personal data and stay.
Best-effort: failures are logged, never raised.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import settings
from .db.models import get_db

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 24 * 3600

# (table, timestamp column). Timestamps are ISO-8601 UTC strings written
# with the same format, so string comparison orders them correctly.
_TABLES = (
    ("chat_messages", "created_at"),
    ("chat_sessions", "last_active_at"),
    ("feedback", "created_at"),
    ("events", "created_at"),
    ("secure_link_events", "created_at"),
)


def purge_old_data(days: int | None = None) -> dict[str, int]:
    """Delete rows older than `days` (default: the setting). Returns the
    number of rows deleted per table; empty on failure."""
    days = settings.data_retention_days if days is None else days
    if days <= 0:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    deleted: dict[str, int] = {}
    try:
        with get_db() as conn:
            for table, column in _TABLES:
                cur = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
                deleted[table] = cur.rowcount
    except Exception as e:  # noqa: BLE001
        log.warning("retention cleanup failed: %s", e)
        return {}
    if any(deleted.values()):
        log.info("retention cleanup (older than %d days): %s", days, deleted)
    return deleted


async def retention_loop() -> None:
    """Purge now, then once a day, until the task is cancelled."""
    while True:
        await asyncio.to_thread(purge_old_data)
        await asyncio.sleep(INTERVAL_SECONDS)
