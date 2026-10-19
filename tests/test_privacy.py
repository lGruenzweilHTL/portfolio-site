"""Retention cleanup, no stored IPs, the visitor hash and the policy pages."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from backend import main
from backend.config import settings
from backend.db.models import get_db, init_db
from backend.retention import purge_old_data


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def count(table: str) -> int:
    with get_db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_retention_deletes_old_rows_and_keeps_new(db):
    with get_db() as conn:
        for age in (100, 10):
            conn.execute("INSERT INTO chat_messages (session_id, role, content, created_at) VALUES ('s', 'user', 'x', ?)", (iso(age),))
            conn.execute("INSERT INTO chat_sessions (session_id, created_at, last_active_at) VALUES (?, ?, ?)", (f"s{age}", iso(age), iso(age)))
            conn.execute("INSERT INTO feedback (message, created_at) VALUES ('x', ?)", (iso(age),))
            conn.execute("INSERT INTO events (kind, path, visitor_hash, created_at) VALUES ('pageview', '/', 'h', ?)", (iso(age),))
            conn.execute("INSERT INTO secure_link_events (success, created_at) VALUES (1, ?)", (iso(age),))
        conn.execute("INSERT INTO secure_links (token, filename, expires_at, max_uses, uses_remaining, created_at) VALUES ('t', 'f', ?, 1, 1, ?)", (iso(200), iso(200)))
    deleted = purge_old_data()
    assert deleted == {"chat_messages": 1, "chat_sessions": 1, "feedback": 1, "events": 1, "secure_link_events": 1}
    for table in ("chat_messages", "chat_sessions", "feedback", "events", "secure_link_events"):
        assert count(table) == 1
    assert count("secure_links") == 1


def test_retention_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "database_path", "/nonexistent/dir/x.db")
    assert purge_old_data() == {}


def test_feedback_stores_no_ip(client, db):
    resp = client.post("/api/feedback", json={"name": "Ana", "message": "Typo on the about page"})
    assert resp.status_code == 204
    with get_db() as conn:
        row = conn.execute("SELECT name, message, ip FROM feedback").fetchone()
    assert dict(row) == {"name": "Ana", "message": "Typo on the about page", "ip": None}


def test_migration_nulls_existing_ips(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO feedback (message, ip, created_at) VALUES ('x', '1.2.3.4', ?)", (iso(1),))
    conn.execute("INSERT INTO chat_sessions (session_id, ip, created_at, last_active_at) VALUES ('s', '1.2.3.4', ?, ?)", (iso(1), iso(1)))
    conn.commit()
    conn.close()
    init_db()
    with get_db() as c:
        assert c.execute("SELECT ip FROM feedback").fetchone()[0] is None
        assert c.execute("SELECT ip FROM chat_sessions").fetchone()[0] is None


def test_visitor_hash_uses_daily_salt(monkeypatch):
    a = main._visitor_hash("1.2.3.4", "ua")
    assert a == main._visitor_hash("1.2.3.4", "ua")
    # A new salt (restart or new day) gives a different hash for the same input.
    monkeypatch.setattr(main, "_salt", ("", ""))
    assert main._visitor_hash("1.2.3.4", "ua") != a


def test_policy_pages(client):
    en = client.get("/privacy")
    de = client.get("/de/privacy")
    assert en.status_code == de.status_code == 200
    assert 'hreflang="de" href="https://gruenzweil.cc/de/privacy"' in en.text
    assert "Datenschutzbehörde" in de.text and 'lang="de"' in de.text
    assert "90 days" in en.text and "Gemini API" in en.text
    assert client.get("/privacy/", follow_redirects=False).status_code == 301


def test_policy_in_sitemap_and_footer(client):
    sitemap = client.get("/sitemap.xml").text
    assert "<loc>https://gruenzweil.cc/privacy</loc>" in sitemap
    assert "<loc>https://gruenzweil.cc/de/privacy</loc>" in sitemap
    assert 'href="/de/privacy">Datenschutz</a>' in client.get("/de/").text
    assert 'href="/privacy">Privacy</a>' in client.get("/").text
    assert 'href="/privacy">' in client.get("/fr/").text
