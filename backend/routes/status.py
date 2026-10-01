"""GET /api/status — coarse live values for the rack UI on the main page.

Feeds the PDU readout in the footer, the "last push" chips on the project
blades and the model name in the chat console. Everything here is either
public anyway (GitHub push dates, the deployed commit) or deliberately coarse
(uptime, a configured node count). No hostnames, IPs, versions or service
names ever leave this endpoint.

Response:
  {
    "uptime_s": 3564000,            # host uptime from /proc/uptime, else process uptime
    "rev": "a1b2c3d",               # short SHA of the deployed working tree
    "rev_time": "2026-09-30T08:12:00+00:00",
    "edge": "VIE",                  # Cloudflare colo that served this request
    "nodes": 3,                     # settings.homelab_nodes
    "model": "gemma-4-26b-a4b-it",  # chatbot model, provider prefix and :free stripped
    "projects": {"sceneforge-ai": "2026-09-18T10:04:11Z", ...}  # GitHub pushed_at
  }

Any value that can't be determined is null; the page shows "--" for it.

The GitHub dates are refreshed in a background thread at most once per
settings.github_cache_seconds, so a request never waits on GitHub. The first
request after startup returns an empty "projects" map and kicks off the fetch.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import WORKSPACE_ROOT, settings
from ..projects import GITHUB_HEADERS, github_projects

log = logging.getLogger(__name__)

router = APIRouter()

_PROCESS_START = time.monotonic()

# The deployed commit only changes with a restart after a deploy, but the
# cache keeps it cheap if someone polls hard.
_GIT_TTL = 30.0
_git_cache: tuple[float, str | None, str | None] = (0.0, None, None)

_gh_lock = threading.Lock()
_gh_projects: dict[str, str] = {}
_gh_fetched_at = 0.0
_gh_running = False


def _uptime_seconds() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return int(time.monotonic() - _PROCESS_START)


def _git_rev() -> tuple[str | None, str | None]:
    global _git_cache
    ts, rev, rev_time = _git_cache
    if time.monotonic() - ts < _GIT_TTL:
        return rev, rev_time
    rev = rev_time = None
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %ct"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            sha, epoch = out.stdout.split()
            rev = sha
            rev_time = datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.debug("git rev lookup failed: %s", e)
    _git_cache = (time.monotonic(), rev, rev_time)
    return rev, rev_time


def _edge(request: Request) -> str | None:
    # CF-Ray looks like "8c1f2e3d4a5b6c7d-VIE"; the suffix is the colo.
    ray = request.headers.get("cf-ray", "")
    if "-" in ray:
        colo = ray.rsplit("-", 1)[1].strip().upper()
        if re.fullmatch(r"[A-Z]{3}", colo):
            return colo
    return None


def _model_label() -> str:
    model = settings.openrouter_model.split("/", 1)[-1]
    return model.split(":", 1)[0]


def _fetch_github() -> None:
    global _gh_projects, _gh_fetched_at, _gh_running
    found: dict[str, str] = {}
    try:
        with httpx.Client(timeout=5.0, headers=GITHUB_HEADERS) as client:
            for slug, _proj, src in github_projects():
                owner, repo = src.owner, src.repo
                try:
                    if repo:
                        r = client.get(f"https://api.github.com/repos/{owner}/{repo}")
                        pushed = r.json().get("pushed_at") if r.status_code == 200 else None
                    else:  # an org: its most recently pushed repo
                        r = client.get(f"https://api.github.com/orgs/{owner}/repos",
                                       params={"sort": "pushed", "per_page": 1})
                        data = r.json() if r.status_code == 200 else []
                        pushed = data[0].get("pushed_at") if data else None
                    if pushed:
                        found[slug] = pushed
                except (httpx.HTTPError, ValueError) as e:
                    log.info("GitHub lookup for %s failed: %s", slug, e)
    except Exception:  # noqa: BLE001 — a background refresh must never crash
        log.exception("GitHub status refresh failed")
    with _gh_lock:
        # Keep old values for repos that failed this round.
        _gh_projects = {**_gh_projects, **found}
        _gh_fetched_at = time.monotonic()
        _gh_running = False


def _github_projects() -> dict[str, str]:
    global _gh_running
    with _gh_lock:
        stale = _gh_fetched_at == 0.0 or time.monotonic() - _gh_fetched_at > settings.github_cache_seconds
        if stale and not _gh_running:
            _gh_running = True
            threading.Thread(target=_fetch_github, daemon=True, name="github-status").start()
        return dict(_gh_projects)


@router.get("/api/status")
def status(request: Request) -> JSONResponse:
    rev, rev_time = _git_rev()
    body = {
        "uptime_s": _uptime_seconds(),
        "rev": rev,
        "rev_time": rev_time,
        "edge": _edge(request),
        "nodes": settings.homelab_nodes,
        "model": _model_label(),
        "projects": _github_projects(),
    }
    return JSONResponse(body, headers={"cache-control": "no-store"})
