"""FastAPI app entrypoint.

Run locally:
    cd backend
    uvicorn backend.main:app --reload --port 8000

Architecture:
    /site           -> StaticFiles mount, served as the portfolio site
    /services       -> server-rendered self-hosted service directory
    /api/...        -> JSON API routes (chat, feedback, track)
    /resume.pdf     -> generated on demand (with on-disk cache)
    /webhook/...    -> deploy trigger
    /admin          -> combined feedback + analytics view (Cloudflare Access gated)

The static mount must come AFTER API routes so /api/*, /services, /resume.pdf,
/admin, and /webhook/* resolve to handlers rather than 404s from the file server.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .config import settings
from .content import load_content, get_content
from .db.models import init_db

log = logging.getLogger(__name__)


# --- Analytics middleware ---------------------------------------------------

# Paths we never log as pageviews. Static asset requests are noise;
# API and webhook paths are noise too (logged via their own routes if needed).
_SKIP_LOG_PREFIXES = ("/static", "/favicon", "/_next", "/api/", "/webhook/", "/_api/")
_SKIP_LOG_EXACT = {"/healthz", "/admin/regenerate-resume"}
# File extensions treated as static assets — skipped at the middleware so
# they never enter the events table, and filtered in the admin top-paths
# query as a belt-and-braces guard against rows logged before this rule.
_STATIC_ASSET_EXTS = frozenset({
    "css", "js", "map",
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico",
    "woff", "woff2", "ttf", "eot",
    "pdf", "zip", "mp4", "webm", "mp3",
})


def _is_static_asset(path: str) -> bool:
    # Last path segment after the final dot. Skip if it's a known asset ext.
    dot = path.rfind("/")
    last = path[dot + 1:]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[1].lower() in _STATIC_ASSET_EXTS


def _visitor_hash(ip: str, user_agent: str) -> str:
    """Pseudo-unique visitor id, rotated daily. No cookies, no PII stored.

    SHA-256 of (ip + ua + date) truncated to 16 hex chars. Date bucket means
    a returning visitor the next day looks like a new visitor — by design for
    "daily uniques" metric. We don't link across days, and the hash isn't
    reversible without the same input triple.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = sha256(f"{ip}|{user_agent}|{today}".encode("utf-8")).hexdigest()
    return digest[:16]


class Static404ToHtmlMiddleware(BaseHTTPMiddleware):
    """The StaticFiles mount returns its own 404 responses (not HTTPException),
    so FastAPI's exception handlers don't fire for missing static files. This
    middleware intercepts 404s *after* the inner app, renders the styled error
    page if appropriate, and lets API 404s through as JSON.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 404 and _wants_html(request):
            # Render the styled 404. Replace the body.
            html = _render_error(404, "Not found", "That page doesn't exist, or it moved.", request.url.path)
            # Drop the previous Content-Length header so Starlette recomputes.
            headers = [(k, v) for k, v in response.headers.items() if k.lower() != "content-length"]
            return HTMLResponse(html, status_code=404, headers=dict(headers))
        return response


class TurnstileSitekeyMiddleware(BaseHTTPMiddleware):
    """Replaces the ``__TURNSTILE_SITEKEY__`` token in HTML responses with the
    real sitekey from settings.

    Why a middleware instead of a Jinja template: ``/`` is served by the
    ``StaticFiles`` mount, so the on-disk ``site/index.html`` is a plain
    static file. The token is the only piece of that page that needs to
    be environment-specific, and a single-pass string replacement keeps
    the rest of the static pipeline (caching, range requests, etc.) intact.

    Only fires on HTML responses that actually contain the token — every
    other response (assets, API JSON, 404s) flows through untouched.
    """

    _TOKEN = "__TURNSTILE_SITEKEY__"

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        # Only HTML, and only if the page actually has the placeholder.
        # Checking the Content-Type first avoids reading non-text bodies.
        ctype = response.headers.get("content-type", "")
        if "text/html" not in ctype.lower():
            return response
        # Buffer the body. index.html is small; this is fine. If the file
        # ever grows large enough for this to matter, switch to a streaming
        # transform.
        #
        # Important: reading body_iterator consumes it. Once we've done that,
        # the original `response` is no longer streamable — returning it
        # would cause uvicorn to see a Content-Length header with a body of
        # zero bytes ("Response content shorter than Content-Length"). So
        # we always reconstruct the response from the buffered body, even
        # when no token replacement happened.
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunks) if chunks else b""
        if self._TOKEN.encode("utf-8") not in body:
            # No rewrite needed, but the body has been consumed. Return a
            # new Response with the same body so the outer middleware can
            # stream it normally.
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        new_body = body.replace(
            self._TOKEN.encode("utf-8"),
            settings.turnstile_sitekey.encode("utf-8"),
        )
        # Drop Content-Length — Starlette will recompute from the new body.
        headers = [(k, v) for k, v in response.headers.items() if k.lower() != "content-length"]
        # Don't let a CDN cache a page with a specific sitekey; if the
        # env value changes, visitors should see the new widget immediately.
        headers.append(("cache-control", "no-store"))
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=dict(headers),
            media_type=response.media_type,
        )


class AnalyticsMiddleware(BaseHTTPMiddleware):
    """Logs one row per non-asset request to the events table.

    Best-effort: any DB error is logged and swallowed so analytics never
    breaks the user-facing request.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response: Response = await call_next(request)
        # After handler so the response status reflects what actually happened.
        try:
            self._log_pageview(request, response)
        except Exception as e:  # noqa: BLE001 — never let logging crash a request
            log.warning("analytics log failed: %s", e)
        # Tiny perf header for dev; remove if you find it noisy.
        response.headers["X-Server-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        return response

    def _log_pageview(self, request: Request, response: Response) -> None:
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_LOG_PREFIXES):
            return
        if path in _SKIP_LOG_EXACT:
            return
        if _is_static_asset(path):
            return
        # Even 404s get logged — someone trying to reach /foo is signal.
        # The status code is captured so the admin "Top paths" view can
        # filter to accepted (2xx/3xx) responses only.
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")
        referrer = request.headers.get("referer", "")
        conn = sqlite3.connect(settings.database_path, timeout=5.0)
        try:
            conn.execute(
                "INSERT INTO events (kind, path, name, referrer, user_agent, visitor_hash, status, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    "pageview",
                    path,
                    (referrer[:512]) if referrer else None,
                    (ua[:512]) if ua else None,
                    _visitor_hash(ip, ua),
                    response.status_code,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()


# --- App factory ------------------------------------------------------------

def create_app() -> FastAPI:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    app = FastAPI(
        title="Portfolio Backend",
        version="0.1.0",
        docs_url="/_api/docs",   # hide behind a less guessable path
        redoc_url=None,
        openapi_url="/_api/openapi.json",
    )

    # Middleware (added before static mount so it wraps everything).
    # Outer-most is the static-404 rewriter so it runs LAST (closest to client).
    app.add_middleware(Static404ToHtmlMiddleware)
    app.add_middleware(AnalyticsMiddleware)
    # Turnstile sitekey injection: rewrites the __TURNSTILE_SITEKEY__ token
    # in HTML responses (only `site/index.html` carries it) with the value
    # from settings, so the env var — not a hardcoded test key — is what
    # the browser actually loads. Must wrap the static mount, which it does
    # automatically because every middleware is added before routes/mounts.
    app.add_middleware(TurnstileSitekeyMiddleware)
    # Notes clean-URL middleware: turns /notes/plsql into the .html file
    # before the static mount sees it. Must be added before the mount
    # and after the other middleware so the analytics middleware can
    # still log these requests (it wraps everything from the outside).
    from .routes.notes import NotesCleanUrlMiddleware
    app.add_middleware(NotesCleanUrlMiddleware)

    # --- Startup: DB + content + resume PDFs ---
    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        try:
            load_content()
        except Exception as e:
            log.error("Content load failed: %s", e)
            raise
        # Render resume PDFs in the background so a slow WeasyPrint import
        # doesn't delay the rest of startup. If it fails (e.g. system libs
        # missing on the server), the route will 503 with a clear message
        # rather than the whole app failing to boot.
        import threading
        def _render_resumes_bg() -> None:
            try:
                from .resume import ensure_resume_pdfs
                ensure_resume_pdfs()
            except Exception as e:
                log.warning("Resume PDF generation deferred: %s", e)
        threading.Thread(target=_render_resumes_bg, daemon=True, name="resume-gen").start()

    # --- Healthcheck (not logged as a pageview) ---
    @app.get("/healthz")
    def healthz() -> dict:
        try:
            get_content()
            content_ok = True
        except Exception:
            content_ok = False
        return {"ok": True, "content_loaded": content_ok}

    # --- API routes (mounted BEFORE static so /api/* resolves here) ---
    from .routes import chat, feedback, track, deploy, admin, resume, files
    from .routes import secure_links_admin
    from .routes import notes
    from .routes import services
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(track.router)
    app.include_router(deploy.router)
    app.include_router(admin.router)
    app.include_router(resume.router)
    app.include_router(files.router)
    app.include_router(secure_links_admin.router)
    app.include_router(notes.router)
    app.include_router(services.router)

    # --- Exception handlers: styled HTML for browsers, JSON for API clients ---
    _install_error_handlers(app)

    # --- Static site mount (catch-all, last) ---
    site_path = Path(settings.site_dir).resolve()
    if not site_path.exists():
        log.warning("Site directory does not exist yet: %s", site_path)
    app.mount("/", StaticFiles(directory=str(site_path), html=True, check_dir=False), name="site")

    return app


# --- Error rendering --------------------------------------------------------

# Paths where the client almost certainly wants JSON, not HTML.
_API_PREFIXES = ("/api/", "/_api/", "/webhook/")


def _wants_html(request: Request) -> bool:
    """Decide whether to render an error as HTML or JSON.

    Heuristic: API prefixes get JSON. Everything else gets HTML (browsers,
    crawlers, link-previewers). The Accept header is a secondary signal:
    if the client explicitly asked for JSON and didn't ask for HTML, JSON.
    """
    if any(request.url.path.startswith(p) for p in _API_PREFIXES):
        return False
    accept = request.headers.get("accept", "")
    if accept and "json" in accept and "html" not in accept:
        return False
    return True


def _render_error(code: int, title: str, message: str, path: str) -> str:
    """Render the styled error template. Sync, because the template is tiny."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(settings.templates_dir),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("error.html").render(
        code=code, title=title, message=message, path=path,
    )


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(404)
    async def _not_found(request: Request, exc):
        if _wants_html(request):
            html = _render_error(404, "Not found", "That page doesn't exist, or it moved.", request.url.path)
            return HTMLResponse(html, status_code=404)
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(403)
    async def _forbidden(request: Request, exc):
        if _wants_html(request):
            html = _render_error(403, "Forbidden", "You don't have access to this resource.", request.url.path)
            return HTMLResponse(html, status_code=403)
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    @app.exception_handler(500)
    async def _server_error(request: Request, exc):
        log.exception("500 on %s", request.url.path)
        if _wants_html(request):
            html = _render_error(500, "Server error", "Something went wrong on our end. Try again in a moment.", request.url.path)
            return HTMLResponse(html, status_code=500)
        return JSONResponse({"detail": "server error"}, status_code=500)

    # Generic HTTPException catch-all (other status codes) — render the same
    # template with the provided code/title/detail.
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        # Re-raise our specific 404/403/500 handlers by mapping; but those
        # are already registered, so this only fires for other codes.
        code = exc.status_code
        title = {400: "Bad request", 401: "Unauthorized", 405: "Method not allowed",
                 410: "Gone", 429: "Too many requests"}.get(code, "Error")
        if _wants_html(request):
            detail = exc.detail if isinstance(exc.detail, str) else title
            html = _render_error(code, title, detail, request.url.path)
            return HTMLResponse(html, status_code=code)
        return JSONResponse({"detail": exc.detail}, status_code=code)


app = create_app()
