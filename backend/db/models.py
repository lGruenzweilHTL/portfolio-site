"""SQLite schema and connection helpers.

Single DB (resume.db) holds three logical tables:
  - events: pageview + custom event rows (analytics)
  - feedback: feedback form submissions
  - chat_sessions / chat_messages: chatbot log

All timestamps are ISO-8601 strings in UTC. All writes are best-effort and
must never break a request (the analytics middleware swallows DB errors).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import settings

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- 'pageview' | 'event'
    path TEXT NOT NULL,
    name TEXT,                          -- event name (e.g. 'chatbot_opened'); NULL for pageview
    referrer TEXT,
    user_agent TEXT,
    visitor_hash TEXT NOT NULL,         -- sha256(ip + ua + date)[:16], pseudo-unique per day
    created_at TEXT NOT NULL            -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor_hash);
CREATE INDEX IF NOT EXISTS idx_events_path ON events(path);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT,
    message TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback(created_at);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,   -- client-provided UUID
    ip TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_last_active ON chat_sessions(last_active_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,                -- 'user' | 'assistant' | 'system'
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error TEXT                         -- NULL on success; populated on stream errors
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at);

-- Secure file-download links (admin-generated, time-limited, optional use cap).
CREATE TABLE IF NOT EXISTS secure_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,        -- secrets.token_urlsafe(32)
    filename TEXT NOT NULL,            -- basename only; lives in settings.secure_files_dir
    expires_at TEXT NOT NULL,          -- ISO-8601 UTC
    max_uses INTEGER NOT NULL,         -- 0 = unlimited
    uses_remaining INTEGER NOT NULL,    -- decremented on each successful use
    created_at TEXT NOT NULL,
    created_by TEXT,                   -- admin user (currently single-admin)
    last_used_at TEXT,                 -- updated on each successful use
    revoked INTEGER NOT NULL DEFAULT 0 -- 0/1
);
CREATE INDEX IF NOT EXISTS idx_secure_links_token ON secure_links(token);
CREATE INDEX IF NOT EXISTS idx_secure_links_expires ON secure_links(expires_at);

-- Append-only event log for every attempt to use a secure link.
CREATE TABLE IF NOT EXISTS secure_link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id INTEGER,                   -- NULL if the token was unknown
    token_prefix TEXT,                 -- first 8 chars of the attempted token, for debugging
    filename TEXT,                     -- filename requested
    ip TEXT,
    user_agent TEXT,
    success INTEGER NOT NULL,          -- 0/1
    reason TEXT,                       -- NULL on success; 'expired' | 'revoked' | 'exhausted' | 'unknown_token' | 'file_missing' on failure
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_secure_link_events_link ON secure_link_events(link_id);
CREATE INDEX IF NOT EXISTS idx_secure_link_events_created_at ON secure_link_events(created_at);
"""


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
    log.info("DB initialized at %s", db_path)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(
        settings.database_path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    # WAL gives us better concurrent read/write behaviour with the analytics
    # middleware writing while the admin view reads.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Context manager (and FastAPI dependency). Yields a connection,
    commits/rolls back automatically."""
    with _connect() as conn:
        yield conn
