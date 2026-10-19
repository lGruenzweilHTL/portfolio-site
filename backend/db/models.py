"""SQLite schema and connection helpers.

Single DB (resume.db) holds these logical tables:
  - events: pageview + custom event rows (analytics)
  - feedback: feedback form submissions
  - chat_sessions / chat_messages: chatbot log
  - chat_models: the chatbot's fallback model list (edited in /admin)
  - secure_links / secure_link_events: token-gated downloads + audit log
  - app_meta: small key/value flags (e.g. chat_models_seeded)

Personal data is deleted after DATA_RETENTION_DAYS (backend/retention.py).

All timestamps are ISO-8601 strings in UTC. All writes are best-effort and
must never break a request (the analytics middleware swallows DB errors).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
    visitor_hash TEXT NOT NULL,         -- sha256(daily salt + ip + ua + date)[:16], pseudo-unique per day
    status INTEGER,                     -- HTTP response status (pageview only); NULL for events and for pre-migration rows
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
    ip TEXT,                           -- no longer written (always NULL)
    user_agent TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback(created_at);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,   -- client-provided UUID
    ip TEXT,                           -- no longer written (always NULL)
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
    error TEXT,                        -- NULL on success; populated on stream errors and 'moderated'
    error_detail TEXT,                 -- JSON list of failed model attempts (assistant rows)
    model TEXT                         -- model that produced the assistant text
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

-- The chatbot's fallback model list, tried in `position` order. Seeded once
-- from OPENROUTER_MODELS; after that only the admin edits it.
CREATE TABLE IF NOT EXISTS chat_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL UNIQUE,        -- provider model id, as sent in the request
    position INTEGER NOT NULL,         -- 0-based order in the fallback list
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db() -> None:
    """Create tables if they don't exist, then run additive migrations.
    Safe to call repeatedly."""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    log.info("DB initialized at %s", db_path)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN. SQLite has no IF NOT EXISTS for
    columns, so we ask the schema via PRAGMA table_info and skip if the
    column is already present. `decl` is the full column declaration
    after the name, e.g. 'INTEGER' or 'TEXT NOT NULL DEFAULT \"\"'."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in existing:
        return
    conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')
    log.info("migration: added %s.%s %s", table, column, decl)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations applied after the base schema. Each step is
    idempotent so the function is safe to call on every boot."""
    # events.status — added so the admin "Top paths" view can filter
    # out 404s/5xx. Nullable on purpose: pre-migration rows stay valid
    # (NULL = unknown, treated as not-successful by the filter).
    _add_column_if_missing(conn, "events", "status", "INTEGER")
    # Index added in the same step so the filtered admin query stays
    # fast even on a few years of accumulated pageviews. The CREATE
    # INDEX IF NOT EXISTS in SCHEMA covers fresh DBs; this is the
    # belt-and-braces for DBs that existed before the column landed.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status)")
    # chat_messages.error_detail / model — which models were tried for an
    # assistant reply and which one answered (admin view only).
    _add_column_if_missing(conn, "chat_messages", "error_detail", "TEXT")
    _add_column_if_missing(conn, "chat_messages", "model", "TEXT")
    # Raw IPs are no longer stored for chat or feedback (see the privacy
    # policy). Clear the ones written before that; a no-op once done.
    conn.execute("UPDATE chat_sessions SET ip = NULL WHERE ip IS NOT NULL")
    conn.execute("UPDATE feedback SET ip = NULL WHERE ip IS NOT NULL")
    _seed_chat_models(conn)


def _seed_chat_models(conn: sqlite3.Connection) -> None:
    """Copy OPENROUTER_MODELS into chat_models once. After that the flag
    stays set and .env is never read for the list again, so an admin who
    deletes every model doesn't get the .env list back on the next boot."""
    if conn.execute("SELECT 1 FROM app_meta WHERE key = 'chat_models_seeded'").fetchone():
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for position, model in enumerate(settings.chat_models):
        conn.execute(
            "INSERT OR IGNORE INTO chat_models (model, position, enabled, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?)",
            (model, position, now, now),
        )
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('chat_models_seeded', '1')")
    log.info("migration: seeded chat_models with %d model(s)", len(settings.chat_models))


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
