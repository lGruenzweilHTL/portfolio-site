"""Admin gate, the chat model list (store + API), and secure-link 404s."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend import chat, chat_models
from backend.config import settings
from backend.db.models import get_db, init_db
from backend.routes import files

ADMIN = {"Cf-Access-Authenticated-User-Email": "lukas@example.com"}
JSON = {**ADMIN, "content-type": "application/json"}


# --- gate --------------------------------------------------------------------

def test_admin_requires_access_header(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/api/models").status_code == 401
    assert client.post("/admin/api/models", json={"model": "x"}).status_code == 401
    assert client.post("/admin/secure-links", json={"filename": "a", "expires_in": "1d"}).status_code == 401


def test_admin_page_renders(client):
    resp = client.get("/admin", headers=ADMIN)
    assert resp.status_code == 200
    assert "Chat models" in resp.text
    assert "lukas@example.com" in resp.text
    assert "model-a" in resp.text


def test_dev_bypass(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_dev_bypass", True)
    assert client.get("/admin").status_code == 200


# --- model store ---------------------------------------------------------------

def test_seeded_once_from_settings(db, monkeypatch):
    assert [m["model"] for m in chat_models.list_models()] == ["model-a", "model-b", "model-c"]
    for m in chat_models.list_models():
        chat_models.delete_model(m["id"])
    # A restart must not bring the .env list back.
    monkeypatch.setattr(settings, "openrouter_models", "other-1,other-2")
    init_db()
    assert chat_models.list_models() == []


def test_enabled_models_cached_and_invalidated(db):
    assert chat_models.enabled_models() == ["model-a", "model-b", "model-c"]
    row = chat_models.list_models()[1]
    chat_models.update_model(row["id"], enabled=False)
    assert chat_models.enabled_models() == ["model-a", "model-c"]


def test_db_failure_falls_back_to_settings(db, monkeypatch):
    monkeypatch.setattr(settings, "database_path", "/nonexistent/dir/x.db")
    chat_models.invalidate()
    assert chat_models.enabled_models() == ["model-a", "model-b", "model-c"]


# --- model API -----------------------------------------------------------------

def ids(client) -> list[str]:
    return [m["model"] for m in client.get("/admin/api/models", headers=ADMIN).json()["models"]]


def test_add_reorder_disable_delete(client):
    resp = client.post("/admin/api/models", json={"model": "  model-d  "}, headers=JSON)
    assert resp.status_code == 201
    new = resp.json()
    assert new["model"] == "model-d" and new["position"] == 3 and new["enabled"] is True
    assert ids(client) == ["model-a", "model-b", "model-c", "model-d"]

    resp = client.patch(f"/admin/api/models/{new['id']}", json={"position": 0}, headers=JSON)
    assert resp.status_code == 200
    assert ids(client) == ["model-d", "model-a", "model-b", "model-c"]
    positions = [m["position"] for m in client.get("/admin/api/models", headers=ADMIN).json()["models"]]
    assert positions == [0, 1, 2, 3]

    assert client.patch(f"/admin/api/models/{new['id']}", json={"enabled": False}, headers=JSON).json()["enabled"] is False
    assert chat._available_models()[0] == "model-a"

    assert client.delete(f"/admin/api/models/{new['id']}", headers=JSON).status_code == 200
    assert ids(client) == ["model-a", "model-b", "model-c"]
    assert client.delete(f"/admin/api/models/{new['id']}", headers=JSON).status_code == 404
    assert client.patch("/admin/api/models/9999", json={"enabled": True}, headers=JSON).status_code == 404


@pytest.mark.parametrize("model", ["", "   ", "has space", "a,b", "x" * 201])
def test_add_rejects_bad_ids(client, model):
    assert client.post("/admin/api/models", json={"model": model}, headers=JSON).status_code == 400


def test_add_rejects_duplicate(client):
    assert client.post("/admin/api/models", json={"model": "model-a"}, headers=JSON).status_code == 400


def test_writes_require_json(client):
    resp = client.post("/admin/api/models", content="model=x", headers={**ADMIN, "content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 415
    assert client.delete("/admin/api/models/1", headers=ADMIN).status_code == 415


def test_writes_check_origin(client):
    evil = {**JSON, "origin": "https://evil.example"}
    assert client.post("/admin/api/models", json={"model": "x"}, headers=evil).status_code == 403
    ok = {**JSON, "origin": "https://gruenzweil.cc"}
    assert client.post("/admin/api/models", json={"model": "x"}, headers=ok).status_code == 201
    same_host = {**JSON, "origin": "http://testserver"}
    assert client.post("/admin/api/models", json={"model": "y"}, headers=same_host).status_code == 201


def test_reenable_clears_cooldown(client):
    row = client.get("/admin/api/models", headers=ADMIN).json()["models"][0]
    chat._cool_down(row["model"], 600)
    client.patch(f"/admin/api/models/{row['id']}", json={"enabled": False}, headers=JSON)
    assert chat.cooldown_remaining(row["model"]) > 0
    client.patch(f"/admin/api/models/{row['id']}", json={"enabled": True}, headers=JSON)
    assert chat.cooldown_remaining(row["model"]) == 0


# --- secure links ----------------------------------------------------------------

@pytest.fixture
def secure_dir(tmp_path, monkeypatch):
    root = tmp_path / "secure"
    root.mkdir()
    (root / "cv.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(files, "_SECURE_ROOT", root.resolve())
    return root


def _link(**overrides) -> str:
    token = files.generate_token()
    now = datetime.now(timezone.utc)
    row = {
        "token": token, "filename": "cv.pdf",
        "expires_at": (now + timedelta(days=1)).isoformat(timespec="seconds"),
        "max_uses": 5, "uses_remaining": 5, "revoked": 0,
        **overrides,
    }
    with get_db() as conn:
        conn.execute(
            "INSERT INTO secure_links (token, filename, expires_at, max_uses, uses_remaining, created_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["token"], row["filename"], row["expires_at"], row["max_uses"], row["uses_remaining"], now.isoformat(), row["revoked"]),
        )
    return token


def test_secure_link_works(client, secure_dir):
    token = _link()
    resp = client.get(f"/files/secure/cv.pdf?t={token}")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


@pytest.mark.parametrize("case", ["unknown", "missing", "expired", "revoked", "exhausted", "file_missing"])
def test_secure_link_failures_all_look_the_same(client, secure_dir, case):
    if case == "unknown":
        url = "/files/secure/cv.pdf?t=nope"
    elif case == "missing":
        url = "/files/secure/cv.pdf"
    elif case == "expired":
        url = f"/files/secure/cv.pdf?t={_link(expires_at='2020-01-01T00:00:00+00:00')}"
    elif case == "revoked":
        url = f"/files/secure/cv.pdf?t={_link(revoked=1)}"
    elif case == "exhausted":
        url = f"/files/secure/cv.pdf?t={_link(uses_remaining=0)}"
    else:
        url = f"/files/secure/gone.pdf?t={_link(filename='gone.pdf')}"
    resp = client.get(url, headers={"accept": "application/json"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}
