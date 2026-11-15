"""GitHub profile README images: data parsing, drawing, serving. No network (respx)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from backend.config import settings
from backend.readme import IMAGES, cards, data, render_all

SVG = "{http://www.w3.org/2000/svg}"


def _user(**over) -> dict:
    """A GraphQL `user` payload shaped like GitHub's."""
    start = date(2025, 11, 16)  # a Sunday
    user = {
        "repositories": {
            "totalCount": 3,
            "nodes": [
                {"name": "lGruenzweilHTL", "description": "profile", "url": "https://github.com/lGruenzweilHTL/lGruenzweilHTL",
                 "stargazerCount": 0, "pushedAt": "2026-11-15T09:00:00Z", "languages": {"edges": []}},
                {"name": "marshal", "description": None, "url": "https://github.com/lGruenzweilHTL/marshal",
                 "stargazerCount": 4, "pushedAt": "2026-11-10T09:00:00Z",
                 "languages": {"edges": [{"size": 900, "node": {"name": "C#"}}, {"size": 100, "node": {"name": "Vue"}}]}},
                {"name": "naollm", "description": "Robot meets LLM", "url": "https://github.com/lGruenzweilHTL/naollm",
                 "stargazerCount": 1, "pushedAt": "2026-11-12T09:00:00Z",
                 "languages": {"edges": [{"size": 1000, "node": {"name": "Python"}}]}},
            ],
        },
        "contributionsCollection": {
            "totalCommitContributions": 600,
            "totalPullRequestContributions": 40,
            "contributionCalendar": {
                "totalContributions": 812,
                "weeks": [
                    {"contributionDays": [{"date": (start + timedelta(days=w * 7 + i)).isoformat(), "contributionCount": (w + i) % 5}
                                          for i in range(7)]}
                    for w in range(53)
                ],
            },
        },
    }
    user.update(over)
    return user


def test_parse_counts_shares_and_recent(db):
    d = data.parse(_user())
    assert (d.contributions, d.commits, d.pull_requests, d.repos, d.stars) == (812, 600, 40, 3, 5)
    assert dict(d.languages) == {"Python": 0.5, "C#": 0.45, "Vue": 0.05}
    # newest first, without the profile repository itself
    assert [r.name for r in d.recent] == ["naollm", "marshal"]
    assert len(d.days) == 53 * 7 and d.sample is False


def test_repo_without_description_takes_the_resume_summary(db):
    marshal = data.parse(_user()).recent[1]
    assert marshal.description == "A self-hosted AI agent for Home Assistant"


def test_shares_fold_the_tail_into_other():
    from collections import Counter
    out = data.shares(Counter({"a": 50, "b": 20, "c": 10, "d": 10, "e": 5, "f": 3, "g": 2}))
    assert [n for n, _ in out] == ["a", "b", "c", "d", "e", "Other"]
    assert sum(s for _, s in out) == pytest.approx(1)


def test_no_token_draws_sample_numbers(db):
    assert data.collect().sample is True


@respx.mock
def test_github_failure_falls_back_to_sample(db, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "t")
    respx.post(data.GRAPHQL_URL).mock(return_value=httpx.Response(502))
    assert data.collect().sample is True


@respx.mock
def test_graphql_errors_fall_back_to_sample(db, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "t")
    respx.post(data.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"errors": [{"message": "bad"}], "data": None}))
    assert data.collect().sample is True


@respx.mock
def test_collect_sends_the_token(db, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "secret-token")
    route = respx.post(data.GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"user": _user()}}))
    assert data.collect().contributions == 812
    assert route.calls[0].request.headers["authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("name", list(IMAGES))
def test_every_image_is_a_standalone_svg(db, name):
    svg = IMAGES[name](data.parse(_user()))
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG}svg" and root.get("role") == "img"
    assert root.findtext(f"{SVG}title").strip() == root.get("aria-label")
    # an image may load nothing: every reference is local or a data URL
    for el in root.iter():
        href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href")
        assert href is None or href.startswith(("data:", "#"))
    assert "url(http" not in svg and "@import" not in svg
    assert "prefers-reduced-motion" in svg


def test_drawing_is_deterministic(db):
    d = data.parse(_user())
    assert cards.stats(d) == cards.stats(d)
    assert cards.activity(d) == cards.activity(d)


def test_weekly_sums_sunday_to_saturday():
    start = date(2026, 11, 1)  # Sunday
    days = [(start + timedelta(days=i), 1) for i in range(10)]
    assert cards.weekly(days) == [(start, 7), (start + timedelta(days=7), 3)]


@pytest.mark.parametrize("delta,expected", [
    (timedelta(hours=2), "today"), (timedelta(days=1), "yesterday"), (timedelta(days=9), "9 days ago"),
    (timedelta(days=30), "4 weeks ago"), (timedelta(days=95), "3 months ago"),
])
def test_ago(delta, expected):
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    assert cards._ago(now - delta, now) == expected


def test_route_serves_what_was_drawn(client):
    assert client.get("/readme/stats.svg").status_code == 503  # not drawn yet
    assert render_all(data.parse(_user())) == list(IMAGES)
    r = client.get("/readme/stats.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "public, no-cache"
    assert client.get("/readme/nope.svg").status_code == 404
    assert client.get("/readme/..%2Fresume.svg").status_code == 404


def test_a_repo_with_a_quip_shows_it_instead_of_its_description(db):
    svg = cards.recent(data.parse(_user()))
    assert "walk into a wall" in svg and "Robot meets LLM" not in svg
