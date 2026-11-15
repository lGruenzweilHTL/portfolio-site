"""The numbers on the profile images, from GitHub's GraphQL API.

One query per render: the contribution calendar of the last year, and the
owner's public, non-fork repositories (stars, languages by bytes, last push).
The API needs a token (settings.github_token, read-only, no scopes); without
one, or when GitHub can't be reached, the images are drawn with sample data
so a dev setup works offline. `ReadmeData.sample` says which it was.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

from ..config import settings
from ..content import get_content

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name description url stargazerCount pushedAt
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } }
      }
    }
    contributionsCollection {
      totalCommitContributions totalPullRequestContributions
      contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
    }
  }
}
"""


@dataclass
class Repo:
    name: str
    description: str
    url: str
    pushed: datetime


@dataclass
class ReadmeData:
    login: str
    contributions: int  # last year
    commits: int
    pull_requests: int
    repos: int  # public, own, non-fork
    stars: int
    days: list[tuple[date, int]]  # the last year, oldest first, full weeks (Sunday first)
    languages: list[tuple[str, float]]  # share of bytes, largest first, the rest as "Other"
    recent: list[Repo] = field(default_factory=list)  # most recently pushed first
    sample: bool = False


def login() -> str:
    return get_content().resume["personal"]["github"]


def collect() -> ReadmeData:
    """Fresh data from GitHub, or sample data if that isn't possible."""
    if not settings.github_token:
        log.info("readme: no GITHUB_TOKEN, drawing sample numbers")
        return sample()
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                GRAPHQL_URL,
                json={"query": QUERY, "variables": {"login": login()}},
                headers={"Authorization": f"Bearer {settings.github_token}", "User-Agent": "gruenzweil.cc"},
            )
            r.raise_for_status()
            body = r.json()
        if body.get("errors") or not body.get("data", {}).get("user"):
            raise ValueError(f"GraphQL errors: {str(body.get('errors'))[:300]}")
        return parse(body["data"]["user"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("readme: GitHub data unavailable, drawing sample numbers: %s", e)
        return sample()


def parse(user: dict) -> ReadmeData:
    me = login()
    repos = user["repositories"]
    nodes = repos["nodes"]
    coll = user["contributionsCollection"]
    cal = coll["contributionCalendar"]
    days = [(date.fromisoformat(d["date"]), d["contributionCount"]) for w in cal["weeks"] for d in w["contributionDays"]]
    bytes_by_lang: Counter[str] = Counter()
    for n in nodes:
        for e in n["languages"]["edges"]:
            bytes_by_lang[e["node"]["name"]] += e["size"]
    recent = [
        Repo(n["name"], n.get("description") or "", n["url"], datetime.fromisoformat(n["pushedAt"].replace("Z", "+00:00")))
        for n in nodes
        if n["name"].lower() != me.lower()  # the profile repo itself
    ]
    recent.sort(key=lambda r: r.pushed, reverse=True)
    summaries = _resume_summaries()
    for repo in recent:
        if not repo.description:
            repo.description = summaries.get(repo.url.lower().rstrip("/"), "")
    return ReadmeData(
        login=me,
        contributions=cal["totalContributions"],
        commits=coll["totalCommitContributions"],
        pull_requests=coll["totalPullRequestContributions"],
        repos=repos["totalCount"],
        stars=sum(n["stargazerCount"] for n in nodes),
        days=days,
        languages=shares(bytes_by_lang),
        recent=recent[:4],
    )


def _resume_summaries() -> dict[str, str]:
    """First sentence of each résumé project's summary, by repo URL: for repos without a description."""
    out = {}
    for proj in get_content().resume.get("projects", []):
        url, summary = (proj.get("repo_url") or "").lower().rstrip("/"), " ".join((proj.get("summary") or "").split())
        if url and summary:
            out[url] = summary.split(". ")[0].rstrip(".")
    return out


def shares(by_lang: Counter[str], top: int = 5) -> list[tuple[str, float]]:
    """The largest languages and the rest as "Other", as shares of 1."""
    total = sum(by_lang.values())
    if not total:
        return []
    out = [(name, size / total) for name, size in by_lang.most_common(top)]
    rest = 1 - sum(s for _, s in out)
    if rest > 0.005:
        out.append(("Other", rest))
    return out


def sample() -> ReadmeData:
    """Plausible, fixed numbers to lay the images out with."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=364)
    start -= timedelta(days=(start.weekday() + 1) % 7)  # back to a Sunday, like GitHub's calendar
    days = []
    d = start
    i = 0
    while d <= end:
        i += 1
        days.append((d, max(0, (i * 37 % 11) - 4 + (6 if d.weekday() < 5 and i % 9 < 4 else 0))))
        d += timedelta(days=1)
    now = datetime.now(timezone.utc)
    return ReadmeData(
        login=login(),
        contributions=sum(c for _, c in days),
        commits=604,
        pull_requests=39,
        repos=37,
        stars=15,
        days=days,
        languages=[("C#", 0.62), ("Python", 0.14), ("TypeScript", 0.09), ("Vue", 0.05), ("C++", 0.04), ("Other", 0.06)],
        recent=[
            Repo("portfolio-site", "The site at gruenzweil.cc: FastAPI on my homelab", "", now - timedelta(hours=5)),
            Repo("naollm", "Connects a NAO robot to a local AI agent", "", now - timedelta(days=8)),
            Repo("marshal", "Self-hosted AI agent for Home Assistant", "", now - timedelta(days=23)),
            Repo("SceneForge-AI", "A Unity editor assistant that proposes scene diffs", "", now - timedelta(days=52)),
        ],
        sample=True,
    )
