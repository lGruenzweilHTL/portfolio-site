"""The main page, once per language.

  GET /              -> English (templates/index.html + content/i18n/en.yaml)
  GET /<code>/       -> the same page in every other catalog's language
  GET /<code>        -> 301 to /<code>/
  GET /en/           -> 301 to /
  GET /index.html    -> 301 to /

The page used to be site/index.html; it is a template now so every language
shares one markup file. Each version links all the others with hreflang, and
the sitemap lists them with the same alternates (routes/projects.py).

Language routes are registered when this module is imported, from the
catalogs on disk; a new catalog goes live with the next restart (the deploy
webhook restarts the service).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..i18n import DEFAULT_LANG, catalogs, languages

router = APIRouter()

ORIGIN = "https://gruenzweil.cc"

_env = Environment(loader=FileSystemLoader(settings.templates_dir), autoescape=select_autoescape(["html"]))


def render_home(code: str) -> str:
    catalog = catalogs()[code]
    return _env.get_template("index.html").render(
        t=catalog.t,
        lang=catalog.lang,
        langs=languages(),
        js_strings=catalog.js(),
        origin=ORIGIN,
    )


# Endpoints are built per language. No return annotations: with postponed
# annotations FastAPI would try to resolve them for every route.
def _page(code: str):
    def page():
        # Revalidate like every other HTML page (see StaticAssetCacheMiddleware).
        return HTMLResponse(render_home(code), headers={"cache-control": "no-cache"})
    return page


def _redirect(to: str):
    def redirect():
        return RedirectResponse(to, status_code=301)
    return redirect


def _add(path: str, endpoint) -> None:
    router.add_api_route(path, endpoint, methods=["GET", "HEAD"], include_in_schema=False)


_add("/", _page(DEFAULT_LANG))
_add("/index.html", _redirect("/"))
_add(f"/{DEFAULT_LANG}/", _redirect("/"))
_add(f"/{DEFAULT_LANG}", _redirect("/"))
for _code in catalogs():
    if _code != DEFAULT_LANG:
        _add(f"/{_code}/", _page(_code))
        _add(f"/{_code}", _redirect(f"/{_code}/"))
