"""Projects with a GitHub home: slugs, GitHub coordinates, and their READMEs.

Shared by /api/status (last-push dates), /projects/<slug> (project pages) and
/sitemap.xml. The project list itself comes from content/resume.yaml; only
projects whose repo_url points at GitHub get a page.

READMEs are fetched from the GitHub API as rendered HTML, then rebuilt through
an allowlist (see _ReadmeSanitizer) before they reach the page: headings are
shifted under the page's own, relative links and images point back at GitHub,
and anything outside the allowlist is dropped. They are cached in memory for
settings.github_cache_seconds; a cold cache fetches synchronously (so crawlers
get the content), a stale one refreshes in the background.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .config import settings
from .content import get_content

log = logging.getLogger(__name__)

_GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?/?$")
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "gruenzweil.cc"}


def slugify(name: str) -> str:
    """Project name to URL slug, e.g. "RoboDucks — RescueMaze" -> "roboducks-rescuemaze"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass(frozen=True)
class GithubSource:
    owner: str
    repo: str | None  # None = a GitHub organization (vektorgrid)

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}" + (f"/{self.repo}" if self.repo else "")

    # An org's README is profile/README.md in its .github repository.
    @property
    def _readme_repo(self) -> str:
        return self.repo or ".github"

    @property
    def _readme_dir(self) -> str:
        return "" if self.repo else "profile/"

    @property
    def readme_api(self) -> str:
        if self.repo:
            return f"https://api.github.com/repos/{self.owner}/{self.repo}/readme"
        return f"https://api.github.com/repos/{self.owner}/.github/contents/profile/README.md"

    @property
    def blob_base(self) -> str:
        return f"https://github.com/{self.owner}/{self._readme_repo}/blob/HEAD/{self._readme_dir}"

    @property
    def raw_base(self) -> str:
        return f"https://raw.githubusercontent.com/{self.owner}/{self._readme_repo}/HEAD/{self._readme_dir}"


def github_source(url: str | None) -> GithubSource | None:
    m = _GITHUB_URL.match(url or "")
    return GithubSource(m.group(1), m.group(2)) if m else None


def github_projects() -> list[tuple[str, dict, GithubSource]]:
    """(slug, project, source) for every resume.yaml project hosted on GitHub."""
    out = []
    for proj in get_content().resume.get("projects", []):
        src = github_source(proj.get("repo_url"))
        if src:
            out.append((slugify(proj.get("name", "")), proj, src))
    return out


def find_project(slug: str) -> tuple[dict, GithubSource] | None:
    for s, proj, src in github_projects():
        if s == slug:
            return proj, src
    return None


# --- README sanitising ------------------------------------------------------

_ALLOWED = {
    "p", "a", "strong", "em", "b", "i", "code", "pre", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td",
    "blockquote", "img", "br", "hr", "del", "s", "sup", "sub", "kbd", "details", "summary",
}
_VOID = {"img", "br", "hr"}
# Dropped together with everything inside them.
_DROP = {"svg", "script", "style", "template", "button", "iframe", "object", "form", "textarea", "select"}
# Void elements that are dropped: no end tag, so they must not start a skip.
_DROP_VOID = {"input", "embed", "source", "track", "meta", "link"}
_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "ol": {"start"},
    "details": {"open"},
}
_HEADING_SHIFT = 2  # page h1 = project name, h2 = "README" unit; README h1 -> h3


class _ReadmeSanitizer(HTMLParser):
    def __init__(self, src: GithubSource) -> None:
        super().__init__(convert_charrefs=True)
        self.src = src
        self.out: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    @staticmethod
    def _heading(tag: str) -> str:
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            return f"h{min(6, int(tag[1]) + _HEADING_SHIFT)}"
        return tag

    def _url(self, value: str, *, image: bool) -> str | None:
        value = value.strip()
        if not value:
            return None
        if value.startswith("#"):
            return None if image else value
        scheme = urlparse(value).scheme.lower()
        if scheme in ("http", "https"):
            return value
        if scheme == "mailto" and not image:
            return value
        if scheme:
            return None  # javascript:, data:, ...
        return urljoin(self.src.raw_base if image else self.src.blob_base, value.removeprefix("./"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        a = {k: (v or "") for k, v in attrs}
        # GitHub's heading permalink: drop it, keep its target as an anchor.
        if tag == "a" and "anchor" in a.get("class", "").split():
            anchor_id = a.get("id", "").removeprefix("user-content-")
            if anchor_id:
                self.out.append(f'<span id="{html.escape(anchor_id)}"></span>')
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in _DROP_VOID:
            return
        if tag in _DROP:
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag not in _ALLOWED:
            return  # unwrap: keep the children, lose the element
        parts = [self._heading(tag)]
        for key in _ATTRS.get(tag, ()):
            if key not in a:
                continue
            val: str | None = a[key]
            if key in ("href", "src"):
                val = self._url(val, image=(key == "src"))
                if val is None:
                    continue
            parts.append(f'{key}="{html.escape(val, quote=True)}"')
        if tag == "a" and a.get("href", "").startswith(("http://", "https://")):
            parts.append('target="_blank" rel="noopener"')
        if tag == "img":
            parts.append('loading="lazy" decoding="async"')
        self.out.append("<" + " ".join(parts) + ">")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in _ALLOWED and tag not in _VOID:
            self.out.append(f"</{self._heading(tag)}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_tag:
            self.out.append(html.escape(data, quote=False))


def sanitize_readme(raw_html: str, src: GithubSource) -> str:
    parser = _ReadmeSanitizer(src)
    parser.feed(raw_html)
    parser.close()
    return "".join(parser.out).strip()


# --- README cache -----------------------------------------------------------

_lock = threading.Lock()
_readmes: dict[str, tuple[float, str | None]] = {}
_refreshing: set[str] = set()


def _fetch_readme(src: GithubSource) -> str | None:
    try:
        headers = {**GITHUB_HEADERS, "Accept": "application/vnd.github.html+json"}
        with httpx.Client(timeout=4.0, headers=headers) as client:
            r = client.get(src.readme_api)
        if r.status_code != 200:
            log.info("README for %s: HTTP %s", src.html_url, r.status_code)
            return None
        return sanitize_readme(r.text, src)
    except httpx.HTTPError as e:
        log.info("README for %s failed: %s", src.html_url, e)
        return None


def _refresh(slug: str, src: GithubSource) -> None:
    body = _fetch_readme(src)
    with _lock:
        old = _readmes.get(slug, (0.0, None))[1]
        # Keep the last good copy if GitHub is unreachable this time.
        _readmes[slug] = (time.monotonic(), body if body is not None else old)
        _refreshing.discard(slug)


def get_readme(slug: str, src: GithubSource) -> str | None:
    """Sanitised README HTML, or None if GitHub has none / can't be reached."""
    with _lock:
        cached = _readmes.get(slug)
        stale = cached is None or time.monotonic() - cached[0] > settings.github_cache_seconds
        start_bg = stale and cached is not None and slug not in _refreshing
        if start_bg:
            _refreshing.add(slug)
    if cached is None:
        _refresh(slug, src)
        return _readmes[slug][1]
    if start_bg:
        threading.Thread(target=_refresh, args=(slug, src), daemon=True, name=f"readme-{slug}").start()
    return cached[1]
