"""Public directory for the self-hosted service catalog.

The catalog lives in content/services.yaml and is loaded with the rest of the
content bundle at startup and by the deploy webhook.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..content import get_content

router = APIRouter()


def _render_services() -> str:
    env = Environment(
        loader=FileSystemLoader(settings.templates_dir),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("services.html").render(
        categories=get_content().services,
    )


@router.get("/services", response_class=HTMLResponse)
def services() -> HTMLResponse:
    return HTMLResponse(_render_services(), headers={"cache-control": "no-store"})


@router.get("/services/", response_class=HTMLResponse)
def services_trailing_slash() -> HTMLResponse:
    return HTMLResponse(_render_services(), headers={"cache-control": "no-store"})
