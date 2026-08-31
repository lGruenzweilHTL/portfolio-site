"""Public file downloads + token-gated secure file downloads.

Routes:
  GET /files/                          -> HTML listing of public files
  GET /files/<name>                    -> stream a public file
  GET /files/secure/<name>?t=<token>   -> stream a protected file (token required)

The public listing is generated from the filesystem on every request (cheap,
no DB). Secure files have no listing at all — `/files/secure/` returns 404.
The /files/secure/<name> endpoint only succeeds if the caller provides a valid
token in `?t=`.

Path traversal: every filename is resolved against its root and checked to
make sure it doesn't escape via `..` or absolute paths.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from ..config import settings
from ..db.models import get_db

log = logging.getLogger(__name__)

router = APIRouter()


# --- Path safety -----------------------------------------------------------

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\- ]{1,255}$")
# A restrictive allowlist: letters, digits, dot, underscore, dash, space.
# Rejects '..', '/', '\\', and anything else. Long enough for sane names,
# short enough that a single name can't be used to probe for vulnerabilities.

_PUBLIC_ROOT = Path(settings.files_dir).resolve()
_SECURE_ROOT = Path(settings.secure_files_dir).resolve()


def _safe_resolve(root: Path, filename: str) -> Path | None:
    """Resolve a filename under root, refusing anything that escapes.

    Returns the absolute Path on success, None on rejection.
    """
    if not _FILENAME_RE.match(filename):
        return None
    if not filename or filename in (".", ".."):
        return None
    candidate = (root / filename).resolve()
    # is_relative_to is the cleanest way to ensure the resolved path is
    # still under root. Resolving symlinks too, so a symlink pointing
    # outside the root is also rejected.
    if not candidate.is_relative_to(root):
        return None
    return candidate


# --- Public listing + downloads -------------------------------------------

def _format_size(n: int) -> str:
    """Human-friendly size, e.g. 1.4 MB."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _human_mtime(iso_ts: str) -> str:
    """Render an ISO timestamp as 'YYYY-MM-DD HH:MM' (UTC). Best-effort."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_ts or "—"


def _list_public_files() -> list[dict]:
    """Return one row per file in the public files dir, sorted by name.

    Skips hidden files (starting with `.`) and any subdirectories.
    """
    if not _PUBLIC_ROOT.exists():
        return []
    rows = []
    for entry in sorted(_PUBLIC_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if not entry.is_file():
            continue
        st = entry.stat()
        rows.append({
            "name": entry.name,
            "size": st.st_size,
            "size_human": _format_size(st.st_size),
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "mtime_human": _human_mtime(datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")),
        })
    return rows


@router.get("/files/", response_class=HTMLResponse)
def files_index() -> HTMLResponse:
    rows = _list_public_files()
    body_rows = "".join(
        f'<li><a class="file-link" href="/files/{quote(r["name"])}" download>'
        f'<span class="file-name">{r["name"]}</span>'
        f'<span class="file-meta">{r["size_human"]} · {r["mtime_human"]}</span></a></li>'
        for r in rows
    ) or '<li class="empty">No public files yet.</li>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Files · Lukas Grünzweil</title>
<link rel="stylesheet" href="/site.css">
<style>
  body {{ background: var(--bg, #0e0d0b); color: var(--text, #d0ccc0);
          font-family: var(--font-body, system-ui, sans-serif); margin: 0; padding: 48px 24px; }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-family: var(--font-display, 'Bricolage Grotesque', sans-serif);
        font-size: 28px; color: var(--hi, #eceae1); margin: 0 0 6px 0; font-weight: 600; letter-spacing: -0.01em; }}
  .lede {{ color: var(--muted, #918c7f); font-family: var(--font-mono, monospace);
            font-size: 12px; margin-bottom: 28px; letter-spacing: 0.02em; }}
  .back {{ font-family: var(--font-mono, monospace); font-size: 12px; color: var(--muted);
           text-decoration: none; display: inline-block; margin-bottom: 16px; }}
  .back:hover {{ color: var(--accent, #eba53a); }}
  ul {{ list-style: none; padding: 0; margin: 0; border: 1px solid var(--line, #232019);
        border-radius: 6px; overflow: hidden; background: var(--panel, #191712); }}
  li {{ border-bottom: 1px solid var(--line, #232019); }}
  li:last-child {{ border-bottom: none; }}
  li.empty {{ padding: 20px; text-align: center; color: var(--muted, #918c7f); font-style: italic; font-size: 13px; }}
  .file-link {{ display: flex; align-items: center; justify-content: space-between; gap: 12px;
                padding: 14px 18px; text-decoration: none; color: var(--text, #d0ccc0);
                transition: background 0.12s, color 0.12s; }}
  .file-link:hover {{ background: var(--bg2, #15140f); color: var(--hi, #eceae1); }}
  .file-name {{ font-family: var(--font-display, 'Bricolage Grotesque', sans-serif);
                font-size: 14px; font-weight: 500; color: var(--hi, #eceae1); }}
  .file-meta {{ font-family: var(--font-mono, monospace); font-size: 11px; color: var(--muted, #918c7f); }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/">← back to site</a>
  <h1>Files</h1>
  <p class="lede">public downloads · no auth required</p>
  <ul>{body_rows}</ul>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/files/{filename}")
def download_public(filename: str):
    path = _safe_resolve(_PUBLIC_ROOT, filename)
    if path is None or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path,
        filename=path.name,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --- Secure (token-gated) downloads ---------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_duration(s: str) -> int:
    """Parse a duration string like '30m', '2h', '1d', '3w', '90s', or a raw
    integer (seconds). Returns the number of seconds, or raises ValueError.
    """
    s = s.strip()
    if not s:
        raise ValueError("empty duration")
    if s.isdigit():
        return int(s)
    unit = s[-1].lower()
    body = s[:-1]
    if not body.isdigit():
        raise ValueError(f"invalid duration: {s!r}")
    n = int(body)
    if unit == "s": return n
    if unit == "m": return n * 60
    if unit == "h": return n * 3600
    if unit == "d": return n * 86400
    if unit == "w": return n * 604800
    raise ValueError(f"unknown unit {unit!r} in {s!r}")


def generate_token() -> str:
    """Cryptographically random URL-safe token."""
    return secrets.token_urlsafe(32)


def create_secure_link(filename: str, *, expires_in: str, max_uses: int = 5, created_by: str | None = None) -> dict:
    """Create a secure link record. Returns the dict (incl. token, expires_at).

    Raises ValueError on bad input, FileNotFoundError if the file doesn't exist.
    """
    path = _safe_resolve(_SECURE_ROOT, filename)
    if path is None or not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {filename!r}")

    seconds = _parse_duration(expires_in)
    if seconds < 60:
        raise ValueError("expires_in must be at least 60 seconds (1m)")
    if seconds > 60 * 60 * 24 * 365:
        raise ValueError("expires_in must be at most 1 year")
    if max_uses < 0 or max_uses > 10000:
        raise ValueError("max_uses must be between 0 and 10000 (0 = unlimited)")

    token = generate_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO secure_links (token, filename, expires_at, max_uses, uses_remaining, "
            "created_at, created_by, last_used_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)",
            (token, filename, expires_at, max_uses, max_uses if max_uses > 0 else 0,
             _now(), created_by),
        )
        link_id = cur.lastrowid
    return {
        "id": link_id,
        "token": token,
        "filename": filename,
        "expires_at": expires_at,
        "max_uses": max_uses,
        "uses_remaining": max_uses if max_uses > 0 else 0,
        "created_at": _now(),
        "created_by": created_by,
        "url": f"{settings.base_url.rstrip('/')}/files/secure/{quote(filename)}?t={token}",
    }


def _verify_token(token: str) -> tuple[int | None, dict | None, str | None]:
    """Look up a token. Returns (link_id, link_row_or_None, error_reason).

    On success: link_id is set, link_row is the SQLite Row, reason is None.
    On failure: link_id may be None or set (if the token existed but is
    invalid for some other reason); link_row is None; reason is set.
    """
    if not token:
        return None, None, "missing_token"
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM secure_links WHERE token = ?", (token,)
        ).fetchone()
    if row is None:
        return None, None, "unknown_token"
    if row["revoked"]:
        return row["id"], None, "revoked"
    # Check expiry
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            return row["id"], None, "expired"
    except (ValueError, TypeError):
        return row["id"], None, "expired"  # bad data; treat as expired
    # Check uses_remaining (only if max_uses > 0)
    if row["max_uses"] > 0 and row["uses_remaining"] <= 0:
        return row["id"], None, "exhausted"
    return row["id"], row, None


def _log_event(link_id: int | None, token: str | None, filename: str, ip: str, ua: str, success: bool, reason: str | None) -> None:
    """Append a row to secure_link_events. Best-effort: errors are logged, not raised."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO secure_link_events (link_id, token_prefix, filename, ip, user_agent, success, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    link_id,
                    (token[:8] if token else None),
                    filename,
                    ip,
                    (ua[:512]) if ua else None,
                    1 if success else 0,
                    reason,
                    _now(),
                ),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to log secure_link_event: %s", e)


@router.get("/files/secure/")
def secure_root() -> PlainTextResponse:
    # Intentionally opaque: never reveal that the directory exists.
    raise HTTPException(status_code=404, detail="not found")


@router.get("/files/secure/{filename}")
def download_secure(filename: str, request: Request):
    token = request.query_params.get("t", "")
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    link_id, row, reason = _verify_token(token)
    if row is None:
        _log_event(link_id, token, filename, ip, ua, success=False, reason=reason)
        # Same 404 regardless of reason — don't leak which failure mode it was.
        raise HTTPException(status_code=404, detail="not found")

    path = _safe_resolve(_SECURE_ROOT, row["filename"])
    if path is None or not path.exists() or not path.is_file():
        _log_event(link_id, token, filename, ip, ua, success=False, reason="file_missing")
        raise HTTPException(status_code=404, detail="not found")

    # Decrement uses_remaining (only when max_uses > 0) and update last_used_at.
    with get_db() as conn:
        if row["max_uses"] > 0:
            conn.execute(
                "UPDATE secure_links SET uses_remaining = uses_remaining - 1, last_used_at = ? "
                "WHERE id = ? AND uses_remaining > 0",
                (_now(), link_id),
            )
        else:
            conn.execute(
                "UPDATE secure_links SET last_used_at = ? WHERE id = ?",
                (_now(), link_id),
            )

    _log_event(link_id, token, filename, ip, ua, success=True, reason=None)
    return FileResponse(
        path,
        filename=path.name,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{path.name}"',
        },
    )
