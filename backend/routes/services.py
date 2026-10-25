"""Public directory for the self-hosted service catalog.

The catalog lives in content/services.yaml and is loaded with the rest of the
content bundle at startup and by the deploy webhook.
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..content import ServiceCategory, ServiceLink, get_content
from ..i18n import DEFAULT_LANG, catalogs

router = APIRouter()


def _render_services() -> str:
    env = Environment(
        loader=FileSystemLoader(settings.templates_dir),
        autoescape=select_autoescape(["html"]),
    )
    en = catalogs()[DEFAULT_LANG]
    return env.get_template("services.html").render(
        categories=get_content().services,
        # English only; the shared PDU partial and main.js need these.
        t=en.t,
        js_strings=en.js(),
        home="/",
    )


@router.get("/services", response_class=HTMLResponse)
def services() -> HTMLResponse:
    return HTMLResponse(_render_services(), headers={"cache-control": "no-store"})


@router.get("/services/", response_class=HTMLResponse)
def services_trailing_slash() -> HTMLResponse:
    return HTMLResponse(_render_services(), headers={"cache-control": "no-store"})


def _bookmark(link: ServiceLink, name: str, indent: str) -> str:
    return f'{indent}<DT><A HREF="{escape(link.url)}">{escape(name)}</A>\n'


def _folder(name: str, body: str, indent: str) -> str:
    return f"{indent}<DT><H3>{escape(name)}</H3>\n{indent}<DL><p>\n{body}{indent}</DL><p>\n"


def _render_bookmarks(categories: tuple[ServiceCategory, ...]) -> str:
    """Netscape bookmark file, the format Chrome, Firefox and Safari import.

    One folder per category; a service with one URL becomes a bookmark named
    after the service, one with several URLs becomes a subfolder.
    """
    folders = ""
    for category in categories:
        inner = "        "
        body = "".join(_bookmark(link, link.label, inner) for link in category.urls)
        for service in category.services:
            if len(service.urls) == 1:
                body += _bookmark(service.urls[0], service.name, inner)
            else:
                links = "".join(_bookmark(link, link.label, inner + "    ") for link in service.urls)
                body += _folder(service.name, links, inner)
        folders += _folder(category.name, body, "    ")
    return (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        "<TITLE>Bookmarks</TITLE>\n"
        "<H1>Bookmarks</H1>\n"
        "<DL><p>\n"
        + _folder("gruenzweil.cc services", folders, "")
        + "</DL><p>\n"
    )


@router.get("/services/bookmarks.html")
def services_bookmarks() -> Response:
    # A download, not a page: Chrome imports it from the bookmarks manager.
    return Response(
        _render_bookmarks(get_content().services),
        media_type="text/html; charset=utf-8",
        headers={
            "cache-control": "no-store",
            "content-disposition": 'attachment; filename="gruenzweil-services-bookmarks.html"',
        },
    )
