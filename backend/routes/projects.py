"""Project pages and the sitemap.

  GET /projects          -> 301 to /#projects (the list lives on the main page)
  GET /projects/<slug>   -> one page per GitHub-hosted project in resume.yaml,
                            with its facts, JSON-LD and the repo README
  GET /sitemap.xml       -> generated: the main page in every language (with
                            hreflang alternates), project pages, résumés,
                            llms.txt, and every study note on disk

Project pages exist for search engines and LLM crawlers as much as for
people: each one is a self-contained, citable page about a single project.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..i18n import DEFAULT_LANG, catalogs, languages
from ..projects import find_project, get_readme, github_projects

router = APIRouter()

ORIGIN = "https://gruenzweil.cc"

# resume.yaml status -> LED colour + label on the page.
_STATUS = {
    "active": ("green led--pulse", "Active"),
    "shipped": ("green", "Shipped"),
    "in_progress": ("amber", "In progress"),
    "private": ("blue", "Private"),
}

# Cable colour class per language, same as the main page.
_LANG = {"C#": "cs", "Python": "py", "TypeScript": "ts", "C": "c", "C++": "cpp", "Kotlin": "kt", "Java": "java", "HTML/CSS": "html"}


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(settings.templates_dir), autoescape=select_autoescape(["html"]))


def _one_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _json_ld(slug: str, proj: dict, src, description: str) -> str:
    url = f"{ORIGIN}/projects/{slug}"
    graph = [
        {
            "@type": "WebPage",
            "@id": f"{url}#page",
            "url": url,
            "name": f"{proj['name']} · Lukas Grünzweil",
            "description": description,
            "inLanguage": "en",
            "isPartOf": {"@id": f"{ORIGIN}/#website"},
            "about": {"@id": f"{url}#project"},
            "breadcrumb": {"@id": f"{url}#breadcrumb"},
        },
        {
            "@type": "SoftwareSourceCode",
            "@id": f"{url}#project",
            "name": proj["name"],
            "description": description,
            "url": url,
            "codeRepository": src.html_url,
            "programmingLanguage": [s for s in proj.get("stack", []) if s in _LANG] or proj.get("stack", []),
            "author": {"@id": f"{ORIGIN}/#person"},
        },
        {
            "@type": "BreadcrumbList",
            "@id": f"{url}#breadcrumb",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Lukas Grünzweil", "item": f"{ORIGIN}/"},
                {"@type": "ListItem", "position": 2, "name": "Projects", "item": f"{ORIGIN}/#projects"},
                {"@type": "ListItem", "position": 3, "name": proj["name"], "item": url},
            ],
        },
    ]
    # "</" can't appear inside a <script> block.
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, indent=2).replace("</", "<\\/")


@router.get("/projects")
def projects_index() -> RedirectResponse:
    return RedirectResponse("/#projects", status_code=301)


@router.get("/projects/{slug}", response_class=HTMLResponse)
def project_page(slug: str) -> HTMLResponse:
    found = find_project(slug)
    if not found:
        raise HTTPException(status_code=404)
    proj, src = found
    description = _one_line(proj.get("summary"))
    led, status_label = _STATUS.get(proj.get("status", ""), ("green", "Shipped"))
    others = [(s, p["name"]) for s, p, _ in github_projects() if s != slug]
    html = _env().get_template("project.html").render(
        slug=slug,
        proj=proj,
        src=src,
        description=description,
        led=led,
        status_label=status_label,
        stack=[(s, _LANG.get(s, "")) for s in proj.get("stack", [])],
        readme=get_readme(slug, src),
        others=others,
        json_ld=_json_ld(slug, proj, src, description),
        origin=ORIGIN,
        # Project pages are English; the shared PDU partial and main.js /
        # chat.js still take their strings from the English catalog.
        t=catalogs()[DEFAULT_LANG].t,
        js_strings=catalogs()[DEFAULT_LANG].js(),
        home="/",
    )
    # Revalidate like every other HTML page (see StaticAssetCacheMiddleware).
    return HTMLResponse(html, headers={"cache-control": "no-cache"})


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return None


@router.get("/sitemap.xml")
def sitemap() -> Response:
    site = Path(settings.site_dir)
    resume_yaml = Path(settings.content_dir) / "resume.yaml"
    langs = languages()
    # The main page changes with its template or any catalog.
    home_sources = [Path(settings.templates_dir) / "index.html", *(Path(settings.content_dir) / "i18n").glob("*.yaml")]
    home_mod = max((m for m in map(_mtime, home_sources) if m), default=None)
    urls: list[tuple[str, str | None, str]] = [
        *[(lang.path, home_mod, "1.0" if lang.code == DEFAULT_LANG else "0.9") for lang in langs],
        *[(f"/projects/{slug}", _mtime(resume_yaml), "0.8") for slug, _, _ in github_projects()],
        ("/resume.pdf", _mtime(resume_yaml), "0.8"),
        ("/resume-ats.pdf", _mtime(resume_yaml), "0.5"),
        ("/llms.txt", _mtime(site / "llms.txt"), "0.3"),
    ]
    notes = site / "notes"
    if (notes / "index.html").exists():
        urls.append(("/notes/", _mtime(notes / "index.html"), "0.4"))
        for page in sorted(notes.rglob("*.html")):
            if page.name == "index.html":
                continue
            rel = page.relative_to(notes).with_suffix("").as_posix()
            urls.append((f"/notes/{rel}", _mtime(page), "0.3"))
    # Every language version lists all of them (itself included) plus x-default.
    alternates = "".join(
        f'<xhtml:link rel="alternate" hreflang="{lang.hreflang}" href="{escape(ORIGIN + lang.path)}"/>' for lang in langs
    ) + f'<xhtml:link rel="alternate" hreflang="x-default" href="{escape(ORIGIN)}/"/>'
    home_paths = {lang.path for lang in langs}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for path, lastmod, prio in urls:
        mod = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        alt = alternates if path in home_paths else ""
        lines.append(f"  <url><loc>{escape(ORIGIN + path)}</loc>{mod}<priority>{prio}</priority>{alt}</url>")
    lines.append("</urlset>")
    return Response("\n".join(lines) + "\n", media_type="application/xml", headers={"cache-control": "no-cache"})
