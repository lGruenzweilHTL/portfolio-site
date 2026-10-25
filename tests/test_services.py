"""Service directory and its bookmark export."""
from __future__ import annotations

from html.parser import HTMLParser

from backend.content import get_content


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.folders: list[str] = []
        self._in_h3 = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs)["href"])
        self._in_h3 = tag == "h3"

    def handle_data(self, data):
        if self._in_h3:
            self.folders.append(data)
            self._in_h3 = False


def test_bookmarks_export_contains_every_url(client):
    r = client.get("/services/bookmarks.html")
    assert r.status_code == 200
    assert r.text.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"

    parser = _Links()
    parser.feed(r.text)
    categories = get_content().services
    expected = [link.url for c in categories for link in c.urls] + [
        link.url for c in categories for s in c.services for link in s.urls
    ]
    assert sorted(parser.hrefs) == sorted(expected)
    assert parser.folders[0] == "gruenzweil.cc services"
    assert {c.name for c in categories} <= set(parser.folders)


def test_services_page_links_the_export(client):
    assert 'href="/services/bookmarks.html"' in client.get("/services").text
