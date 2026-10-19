"""Privacy policy: English at /privacy, Austrian German at /de/privacy.

The policy text lives in templates/privacy.html (both languages, legal copy
for this page only), not in the main-page catalogs. The es/fr/it pages link
to /privacy. Keep it in step with what the code does: retention.py, the
visitor hash in main.py, net.py and what chat.py / feedback.py store.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..i18n import DEFAULT_LANG, catalogs

router = APIRouter()

ORIGIN = "https://gruenzweil.cc"

# Language code -> path. Both are listed in the sitemap with these alternates.
PATHS = {"en": "/privacy", "de": "/de/privacy"}

# Date of the last change to the policy text; shown on the page.
UPDATED = "2026-10-18"


def _render(code: str) -> str:
    env = Environment(loader=FileSystemLoader(settings.templates_dir), autoescape=select_autoescape(["html"]))
    all_catalogs = catalogs()
    catalog = all_catalogs.get(code) or all_catalogs[DEFAULT_LANG]
    return env.get_template("privacy.html").render(
        lang=code,
        paths=PATHS,
        origin=ORIGIN,
        updated=UPDATED,
        # The PDU footer and the page scripts speak the page language.
        t=catalog.t,
        js_strings=catalog.js(),
        home="/" if code == DEFAULT_LANG else f"/{code}/",
    )


def _page(code: str):
    def page():
        # Revalidate like every other HTML page (see StaticAssetCacheMiddleware).
        return HTMLResponse(_render(code), headers={"cache-control": "no-cache"})
    return page


def _redirect(to: str):
    def redirect():
        return RedirectResponse(to, status_code=301)
    return redirect


for _code, _path in PATHS.items():
    router.add_api_route(_path, _page(_code), methods=["GET", "HEAD"], include_in_schema=False)
    router.add_api_route(_path + "/", _redirect(_path), methods=["GET", "HEAD"], include_in_schema=False)
