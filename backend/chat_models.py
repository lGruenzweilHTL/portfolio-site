"""The chatbot's fallback model list, stored in SQLite (table chat_models).

Edited on /admin (routes/chat_models_admin.py); seeded once from
OPENROUTER_MODELS by init_db(). The enabled ids are cached in memory so a
chat request doesn't touch SQLite for them; every write here drops the
cache. If the DB can't be read, the .env list is used for that request.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone

from .config import settings
from .db.models import get_db

log = logging.getLogger(__name__)

# Provider model ids: no whitespace or commas (the old .env format was a
# comma-separated list), 1-200 chars after trimming.
_MODEL_RE = re.compile(r"^[^\s,]{1,200}$")

_lock = threading.Lock()
_cache: list[str] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def enabled_models() -> list[str]:
    """Enabled model ids in fallback order."""
    global _cache
    with _lock:
        if _cache is not None:
            return list(_cache)
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT model FROM chat_models WHERE enabled = 1 ORDER BY position, id"
            ).fetchall()
    except Exception as e:  # noqa: BLE001 — best-effort, never break a chat
        log.warning("chat_models read failed, using OPENROUTER_MODELS: %s", e)
        return settings.chat_models
    models = [r["model"] for r in rows]
    with _lock:
        _cache = models
    return list(models)


def validate_model_id(model: str) -> str:
    model = (model or "").strip()
    if not _MODEL_RE.match(model):
        raise ValueError("model id must be 1-200 characters, without spaces or commas")
    return model


def list_models() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, model, position, enabled, created_at, updated_at "
            "FROM chat_models ORDER BY position, id"
        ).fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def _renumber(conn, ids: list[int]) -> None:
    now = _now()
    for position, row_id in enumerate(ids):
        conn.execute(
            "UPDATE chat_models SET position = ?, updated_at = ? WHERE id = ? AND position != ?",
            (position, now, row_id, position),
        )


def _ordered_ids(conn) -> list[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM chat_models ORDER BY position, id").fetchall()]


def add_model(model: str) -> dict:
    """Append a model to the end of the list. ValueError on a bad or
    duplicate id."""
    model = validate_model_id(model)
    now = _now()
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM chat_models WHERE model = ?", (model,)).fetchone():
            raise ValueError(f"{model} is already in the list")
        row = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM chat_models").fetchone()
        cur = conn.execute(
            "INSERT INTO chat_models (model, position, enabled, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (model, row[0], now, now),
        )
        new_id = cur.lastrowid
    invalidate()
    return get_model(new_id)


def get_model(model_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, model, position, enabled, created_at, updated_at FROM chat_models WHERE id = ?",
            (model_id,),
        ).fetchone()
    if row is None:
        raise KeyError(model_id)
    return {**dict(row), "enabled": bool(row["enabled"])}


def update_model(model_id: int, *, enabled: bool | None = None, position: int | None = None) -> dict:
    """Toggle and/or move a model. `position` is the 0-based target index,
    clamped to the list. KeyError if the id doesn't exist."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM chat_models WHERE id = ?", (model_id,)).fetchone():
            raise KeyError(model_id)
        if enabled is not None:
            conn.execute(
                "UPDATE chat_models SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, _now(), model_id),
            )
        ids = _ordered_ids(conn)
        if position is not None:
            ids.remove(model_id)
            ids.insert(max(0, min(position, len(ids))), model_id)
        _renumber(conn, ids)
    invalidate()
    return get_model(model_id)


def delete_model(model_id: int) -> None:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM chat_models WHERE id = ?", (model_id,))
        if cur.rowcount == 0:
            raise KeyError(model_id)
        _renumber(conn, _ordered_ids(conn))
    invalidate()
