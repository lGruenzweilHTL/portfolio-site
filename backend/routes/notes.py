"""Notes section: directory listings + asset aliases + clean URLs.

Routes:
  GET /notes/                       -> served by the static site mount
                                      (index.html at site/notes/index.html)
  GET /notes/_listing               -> JSON listing of /notes/
  GET /notes/<subdir>/_listing      -> JSON listing of <subdir>
  GET /notes/notes.css              -> alias for site/notes/study.css
  GET /notes/cheatsheet.js          -> alias for site/notes/cheatsheet.js

Plus a middleware (`NotesCleanUrlMiddleware`) that handles extensionless
URLs like /notes/plsql -> site/notes/plsql.html. The middleware runs
before the static mount and short-circuits only for clean-URL requests;
real files and 404s flow through to the static mount as usual.

Why this module exists:
  The notes index pages (root + Java/ + SYP/) are a single template
  symlinked into every listing directory. The template fetches
  `./_listing/` and expects a JSON array of `{name, type, ...}` entries
  (the same shape nginx's `autoindex_format json` produces). Since the
  app runs behind FastAPI only — no nginx — we provide that endpoint
  here.

  Cheatsheet pages reference `/notes/notes.css` and `/notes/cheatsheet.js`
  with absolute paths. The actual files on disk are `study.css` and
  `cheatsheet.js`, and the same files work for cheatsheets at any depth
  under /notes/. Aliasing them at fixed paths keeps every cheatsheet
  working without per-file edits.

  The template also uses extensionless links (`./plsql` rather than
  `./plsql.html`). Starlette's StaticFiles only resolves `index.html`
  on directory requests, not arbitrary `.html` files. Doing the
  fallback in a middleware keeps the route table clean and lets the
  static mount handle everything it normally would.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter()

_NOTES_ROOT = Path(settings.site_dir) / "notes"
_STUDY_CSS = _NOTES_ROOT / "study.css"
_CHEATSHEET_JS = _NOTES_ROOT / "cheatsheet.js"

# Anything starting with a dot or underscore is treated as private.
# Mirrors the client-side isHidden() check in notes/index.html.
_HIDDEN_PREFIXES = (".", "_")


def _safe_subdir(request_path: str) -> Path | None:
    """Resolve a request subpath to a directory under _NOTES_ROOT, or None.

    Refuses:
      - absolute paths
      - '..' segments
      - any escape outside _NOTES_ROOT
      - hidden directories (dotfiles, underscore-prefixed)
    The trailing `_listing/` segment is the client's job to append; this
    function only validates the directory part.
    """
    # Strip the leading "/notes" prefix that the static mount matches.
    rel = request_path
    if rel.startswith("/notes"):
        rel = rel[len("/notes"):]
    rel = rel.lstrip("/")
    if not rel:
        return _NOTES_ROOT
    candidate = (_NOTES_ROOT / rel).resolve()
    try:
        candidate.relative_to(_NOTES_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_dir():
        return None
    name = candidate.name
    if any(name.startswith(p) for p in _HIDDEN_PREFIXES):
        return None
    return candidate


@router.get("/notes/_listing")
@router.get("/notes/_listing/")
def notes_root_listing() -> JSONResponse:
    """Root listing — same shape as the subdir variant, just for /notes/ itself.
    Both slash variants are registered because the client-side JS appends
    `_listing/` to a directory URL and would otherwise 404.
    """
    return _list_dir(_NOTES_ROOT)


def _list_dir(target: Path) -> JSONResponse:
    """Build the JSON listing for an already-validated directory."""
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        name = child.name
        if any(name.startswith(p) for p in _HIDDEN_PREFIXES):
            continue
        try:
            st = child.stat()
            mtime = int(st.st_mtime)
        except OSError:
            continue
        if child.is_dir():
            entries.append({"name": name, "type": "directory", "mtime": mtime})
        elif child.is_file():
            entries.append({
                "name": name,
                "type": "file",
                "mtime": mtime,
                "size": st.st_size,
            })
    return JSONResponse(content=entries, headers={"Cache-Control": "no-store"})


@router.get("/notes/{subdir:path}/_listing")
@router.get("/notes/{subdir:path}/_listing/")
def notes_listing(subdir: str) -> JSONResponse:
    """Return a JSON array describing the immediate children of a notes
    subdirectory. Each entry is `{name, type, mtime, size?}`, matching the
    shape nginx's `autoindex_format json` produces — the notes index page
    only reads `name` and `type`, but extra fields are cheap and useful
    for debugging from devtools.

    Both slash variants are registered because the client-side JS appends
    `_listing/` to a directory URL.
    """
    request_path = f"/notes/{subdir}/"
    target = _safe_subdir(request_path)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    return _list_dir(target)


@router.get("/notes/notes.css")
def notes_css() -> FileResponse:
    if not _STUDY_CSS.is_file():
        raise HTTPException(status_code=404, detail="notes.css not found")
    return FileResponse(
        _STUDY_CSS,
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/notes/cheatsheet.js")
def notes_js() -> FileResponse:
    if not _CHEATSHEET_JS.is_file():
        raise HTTPException(status_code=404, detail="cheatsheet.js not found")
    return FileResponse(
        _CHEATSHEET_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


# --- Clean URL middleware --------------------------------------------------
#
# The notes index links to cheatsheets with extensionless URLs
# (`./plsql`, `./Java/javafx`). The static mount only resolves
# `index.html` on directory requests, so a path like `/notes/plsql`
# would otherwise 404. This middleware turns such requests into
# `FileResponse`s of `<path>.html` directly, before the static mount
# has a chance to handle them.
#
# Skipped cases (the request falls through to the static mount):
#   - any path outside /notes/
#   - any path that has an extension (`.html`, `.css`, etc.) — the
#     static mount serves these
#   - dotfile / underscore-prefixed segments (private)
#   - any escape outside _NOTES_ROOT
#
# Why a middleware and not a route: a catch-all route would have to
# either match paths with extensions (and shadow the static mount for
# them) or use a regex constraint that's easy to get wrong. The
# middleware runs first and only fires for the exact clean-URL case,
# so the static mount keeps its full responsibility for everything
# else under /notes/.

class NotesCleanUrlMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/notes/"):
            handled = self._try_clean_url(path)
            if handled is not None:
                return handled
        return await call_next(request)

    def _try_clean_url(self, path: str) -> FileResponse | None:
        rel = path[len("/notes/"):]
        if not rel or rel.endswith("/"):
            return None  # directory request -> static mount serves index.html
        base = rel.rsplit("/", 1)[-1]
        if "." in base:
            return None  # has extension -> static mount handles
        if any(seg.startswith(p) for seg in rel.split("/") for p in _HIDDEN_PREFIXES):
            return None
        target = (_NOTES_ROOT / rel).resolve()
        try:
            target.relative_to(_NOTES_ROOT.resolve())
        except ValueError:
            return None
        html_path = target.with_suffix(".html")
        if not html_path.is_file():
            return None  # let the static mount 404 normally
        return FileResponse(html_path, media_type="text/html; charset=utf-8")

