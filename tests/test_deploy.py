"""Deploy webhook: HMAC, branch filter, ping, failure reporting."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from backend.config import settings
from backend.routes import deploy

SECRET = "s3cret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def hook(client, monkeypatch, tmp_path):
    """Webhook with the secret set and every side effect stubbed out."""
    monkeypatch.setattr(settings, "deploy_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "deploy_restart_flag", str(tmp_path / "restart" / "trigger"))
    calls: list[list[str]] = []
    result = {"rc": 0, "out": "Updating abc..def\nFast-forward\n", "err": ""}

    def fake_run(cmd, cwd=None, timeout=30):
        calls.append(cmd)
        return result["rc"], result["out"], result["err"]

    monkeypatch.setattr(deploy, "_run", fake_run)
    monkeypatch.setattr(deploy, "reload_content", lambda: None)

    class FakePdf:
        def stat(self):
            class S:
                st_size = 1
            return S()

    monkeypatch.setattr(deploy, "regenerate_resume_pdfs", lambda: (FakePdf(), FakePdf()))
    notes = []
    import backend.notify
    monkeypatch.setattr(backend.notify, "notify", lambda **kw: notes.append(kw))

    def post(payload: dict | None = None, event: str = "push", signature: str | None = "auto", raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload or {}).encode()
        headers = {"X-GitHub-Event": event, "content-type": "application/json"}
        if signature == "auto":
            headers["X-Hub-Signature-256"] = sign(body)
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return client.post("/webhook/deploy", content=body, headers=headers)

    post.calls = calls
    post.result = result
    post.notes = notes
    return post


def test_valid_push_to_master_deploys(hook):
    resp = hook({"ref": "refs/heads/master"})
    assert resp.status_code == 200
    assert hook.calls == [["git", "pull", "--ff-only"]]
    assert "git: pulled" in resp.text
    assert "content: reloaded" in resp.text
    assert "FAILED" not in resp.text
    assert len(hook.notes) == 1


def test_invalid_signature(hook):
    resp = hook({"ref": "refs/heads/master"}, signature=sign(b"other"))
    assert resp.status_code == 401
    assert hook.calls == []


def test_missing_signature(hook):
    resp = hook({"ref": "refs/heads/master"}, signature=None)
    assert resp.status_code == 401
    assert hook.calls == []


def test_wrong_prefix_signature(hook):
    body = json.dumps({"ref": "refs/heads/master"}).encode()
    resp = hook(raw=body, signature=sign(body).replace("sha256=", "sha1="))
    assert resp.status_code == 401


@pytest.mark.parametrize("ref", ["refs/heads/feat/site-hardening", "refs/tags/v1", "refs/heads/master-old"])
def test_other_refs_are_skipped(hook, ref):
    resp = hook({"ref": ref})
    assert resp.status_code == 200
    assert resp.text == f"ok · skipped ({ref})\n"
    assert hook.calls == []
    assert hook.notes == []


def test_other_events_are_skipped(hook):
    resp = hook({"action": "opened"}, event="pull_request")
    assert resp.status_code == 200
    assert resp.text.startswith("ok · skipped")
    assert hook.calls == []
    assert hook.notes == []


def test_ping(hook):
    resp = hook({"zen": "Keep it simple."}, event="ping")
    assert resp.text == "ok · pong\n"
    assert hook.calls == []


def test_deploy_branch_setting(hook, monkeypatch):
    monkeypatch.setattr(settings, "deploy_branch", "main")
    assert "skipped" in hook({"ref": "refs/heads/master"}).text
    assert "git: pulled" in hook({"ref": "refs/heads/main"}).text


def test_failed_pull_reports_and_skips_restart(hook):
    hook.result.update(rc=1, out="", err="fatal: Not possible to fast-forward, aborting.")
    resp = hook({"ref": "refs/heads/master"})
    assert resp.status_code == 200
    assert "git: FAILED (fatal: Not possible to fast-forward" in resp.text
    assert "systemd: skipped (git pull failed)" in resp.text
    assert hook.notes[0]["title"] == "Deploy FAILED"


def test_garbage_body_with_valid_signature_is_skipped(hook):
    resp = hook(raw=b"not json")
    assert resp.status_code == 200
    assert "skipped" in resp.text
    assert hook.calls == []
