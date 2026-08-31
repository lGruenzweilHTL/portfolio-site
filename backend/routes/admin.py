"""GET /admin — combined feedback + analytics + chat logs view.

In production, gated by Cloudflare Access. The trusted header is
`Cf-Access-Authenticated-User-Email` (configurable). If Cloudflare Access
isn't in front of us (dev), ADMIN_DEV_BYPASS=1 lets any request in with
the dev user.

The view is a single page that pulls everything in one DB pass. It does
*not* live-reload (no JS polling); refresh to see new data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from ..db.models import get_db

log = logging.getLogger(__name__)

router = APIRouter()

# Cloudflare Access injects this header. Name is configurable so the same
# code works in self-hosted zero-trust setups.
ACCESS_HEADER = "cf-access-authenticated-user-email"


def _authenticate(request: Request) -> str | None:
    """Return the authenticated user email, or None if unauthenticated."""
    if settings.admin_dev_bypass:
        return settings.admin_dev_user or "dev@local"
    user = request.headers.get(ACCESS_HEADER)
    if not user:
        return None
    return user.strip()


def _aggregate(conn) -> dict:
    """Compute the dashboard's aggregate numbers and lists in a single
    connection (multiple read queries). Cheap; no caching needed at this
    scale.
    """
    now = datetime.now(timezone.utc)
    seven_days_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    thirty_days_ago = (now - timedelta(days=30)).isoformat(timespec="seconds")

    def scalar(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    stats = {
        "pageviews_7d": scalar(
            "SELECT COUNT(*) FROM events WHERE kind = 'pageview' AND created_at >= ?",
            (seven_days_ago,),
        ),
        "uniques_7d": scalar(
            "SELECT COUNT(DISTINCT visitor_hash) FROM events WHERE kind = 'pageview' AND created_at >= ?",
            (seven_days_ago,),
        ),
        "events_7d": scalar(
            "SELECT COUNT(*) FROM events WHERE kind = 'event' AND created_at >= ?",
            (seven_days_ago,),
        ),
        "feedback_total": scalar("SELECT COUNT(*) FROM feedback"),
        "feedback_7d": scalar(
            "SELECT COUNT(*) FROM feedback WHERE created_at >= ?", (seven_days_ago,)
        ),
        "chat_sessions_7d": scalar(
            "SELECT COUNT(*) FROM chat_sessions WHERE created_at >= ?", (seven_days_ago,)
        ),
        "chat_messages_7d": scalar(
            "SELECT COUNT(*) FROM chat_messages WHERE created_at >= ?", (seven_days_ago,)
        ),
    }

    # Top paths — only real routes, no static assets. The exclude regex
    # mirrors the middleware's _is_static_asset so historical rows that
    # slipped through before the rule existed don't pollute the list.
    _ROUTE_PATH_SQL = """
        SELECT path, COUNT(*) as count FROM events
        WHERE kind = 'pageview' AND created_at >= ?
          AND path NOT GLOB '*.[cC][sS][sS]'
          AND path NOT GLOB '*.[jJ][sS]'
          AND path NOT GLOB '*.[mM][aA][pP]'
          AND path NOT GLOB '*.[pP][nN][gG]'
          AND path NOT GLOB '*.[jJ][pP][gG]'
          AND path NOT GLOB '*.[jJ][pP][eE][gG]'
          AND path NOT GLOB '*.[gG][iI][fF]'
          AND path NOT GLOB '*.[wW][eE][bB][pP]'
          AND path NOT GLOB '*.[sS][vV][gG]'
          AND path NOT GLOB '*.[iI][cC][oO]'
          AND path NOT GLOB '*.[wW][oO][fF][fF]'
          AND path NOT GLOB '*.[wW][oO][fF][fF]2'
          AND path NOT GLOB '*.[tT][tT][fF]'
          AND path NOT GLOB '*.[eE][oO][tT]'
          AND path NOT GLOB '*.[pP][dD][fF]'
          AND path NOT GLOB '*.[zZ][iI][pP]'
          AND path NOT GLOB '*.[mM][pP]4'
          AND path NOT GLOB '*.[wW][eE][bB][mM]'
          AND path NOT GLOB '*.[mM][pP]3'
        GROUP BY path ORDER BY count DESC LIMIT 10
    """
    top_paths = [
        {"path": r["path"], "count": r["count"]}
        for r in conn.execute(_ROUTE_PATH_SQL, (seven_days_ago,)).fetchall()
    ]
    # Top events
    top_events = [
        {"name": r["name"], "count": r["count"]}
        for r in conn.execute(
            "SELECT name, COUNT(*) as count FROM events "
            "WHERE kind = 'event' AND name IS NOT NULL AND created_at >= ? "
            "GROUP BY name ORDER BY count DESC LIMIT 10",
            (seven_days_ago,),
        ).fetchall()
    ]
    # Recent feedback
    feedback = [
        dict(r) for r in conn.execute(
            "SELECT name, email, message, ip, created_at FROM feedback "
            "ORDER BY id DESC LIMIT 30"
        ).fetchall()
    ]
    # Recent chat sessions with their messages inlined
    sessions = []
    for s in conn.execute(
        "SELECT session_id, created_at, last_active_at, message_count "
        "FROM chat_sessions ORDER BY last_active_at DESC LIMIT 20"
    ).fetchall():
        sid = s["session_id"]
        msgs = [dict(m) for m in conn.execute(
            "SELECT role, content, error, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id ASC", (sid,)
        ).fetchall()]
        # Round-trip the full timestamp strings as-is from SQLite; the
        # template just renders them.
        err_count = sum(1 for m in msgs if m.get("error"))
        sessions.append({
            "session_id": sid,
            "created_at": s["created_at"],
            "last_active_at": s["last_active_at"],
            "message_count": s["message_count"],
            "error_count": err_count,
            "messages": msgs,
        })

    return {
        "stats": stats,
        "top_paths": top_paths,
        "top_events": top_events,
        "feedback": feedback,
        "chat_sessions": sessions,
        "secure_links": _list_secure_links(conn),
        "secure_files": _list_secure_files(),
    }


def _list_secure_links(conn) -> list[dict]:
    """Return all secure links, most recent first. Each link includes a flag
    `active` so the template can show the right state without recomputing."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rows = []
    for r in conn.execute(
        "SELECT id, filename, expires_at, max_uses, uses_remaining, created_at, "
        "created_by, last_used_at, revoked "
        "FROM secure_links ORDER BY id DESC LIMIT 100"
    ).fetchall():
        d = dict(r)
        try:
            expired = datetime.fromisoformat(d["expires_at"]) < now
        except (ValueError, TypeError):
            expired = True
        d["expired"] = expired
        d["exhausted"] = (d["max_uses"] > 0 and d["uses_remaining"] <= 0)
        d["active"] = (not d["revoked"]) and (not expired) and (not d["exhausted"])
        rows.append(d)
    return rows


def _list_secure_files() -> list[str]:
    """List file basenames in the secure files directory, for the
    'available files' dropdown in the create-link form."""
    from pathlib import Path
    from ..config import settings
    root = Path(settings.secure_files_dir)
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request) -> HTMLResponse:
    user = _authenticate(request)
    if user is None:
        # In production, Cloudflare Access handles the challenge; we'd never
        # see a request without the header. If we do (e.g. Access misconfig),
        # return 401 with a clear message.
        raise HTTPException(
            status_code=401,
            detail="Admin is gated by Cloudflare Access. Visit / via the Access-protected hostname.",
        )

    with get_db() as conn:
        data = _aggregate(conn)

    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(settings.templates_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("admin.html")
    html = template.render(
        user=user,
        dev_bypass=settings.admin_dev_bypass,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **data,
    )
    return HTMLResponse(html)
