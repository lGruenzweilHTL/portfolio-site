"""POST /webhook/deploy — GitHub webhook handler.

Security:
  - HMAC-SHA256 verification using X-Hub-Signature-256.
    The shared secret is DEPLOY_WEBHOOK_SECRET (set in the GitHub webhook
    config and on the server). The header format is "sha256=<hex>".
  - Per-IP rate limit (settings.deploy_rate_limit).
  - If the secret is empty AND we're not in dev bypass mode, the endpoint
    refuses all requests.

Actions triggered (idempotent — safe to call repeatedly):
  1. Reload /content (cheap; safe to do even without a git pull).
  2. Regenerate resume PDFs (in-process; takes a few seconds).
  3. (Production only) `git pull` in the project dir, then
     `systemctl restart backend` so the new code is live. Both run via
     subprocess; failures are logged but don't return 5xx to GitHub (we
     already accepted the deploy, returning 5xx just causes GitHub to retry).

Returns 200 with a one-line summary of what happened.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..config import settings
from ..content import reload_content
from ..resume import regenerate_resume_pdfs

log = logging.getLogger(__name__)

router = APIRouter()

_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(ip: str, *, max_calls: int, window_seconds: int) -> bool:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[ip]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_calls:
        return True
    bucket.append(now)
    return False


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify a GitHub-style HMAC-SHA256 signature.

    Returns True if the signature matches. Returns False on any mismatch
    or missing header. constant-time comparison via hmac.compare_digest.
    """
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess, capture stdout+stderr. Returns (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return -1, "", f"exec error: {e}"


@router.post("/webhook/deploy")
async def deploy(request: Request) -> PlainTextResponse:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip, max_calls=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="rate-limited")

    # Read raw body for signature verification
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")

    if not settings.deploy_webhook_secret:
        # In dev with an empty secret, allow it but log loudly.
        log.warning("DEPLOY_WEBHOOK_SECRET not set — accepting request without HMAC verify")
    else:
        if not _verify_signature(settings.deploy_webhook_secret, body, sig):
            log.warning("Webhook signature mismatch from %s", ip)
            raise HTTPException(status_code=401, detail="signature mismatch")

    log.info("Deploy webhook received from %s (body=%d bytes)", ip, len(body))

    steps: list[str] = []

    # 1) Reload /content (cheap, always safe)
    try:
        reload_content()
        steps.append("content: reloaded")
    except Exception as e:
        log.error("Content reload failed: %s", e)
        steps.append(f"content: FAILED ({e})")

    # 2) Regenerate resume PDFs
    try:
        light, dark = regenerate_resume_pdfs()
        steps.append(f"resume: regenerated ({light.stat().st_size} + {dark.stat().st_size} bytes)")
    except Exception as e:
        log.exception("Resume regeneration failed")
        steps.append(f"resume: FAILED ({e})")

    # 3) Production: git pull + restart. The dev case skips these.
    # Detected by the presence of a project root that contains a .git dir
    # AND the existence of a systemd unit named 'backend' (configurable via
    # DEPLOY_SYSTEMD_UNIT env, default 'backend').
    project_root = Path(__file__).resolve().parents[2]  # /workspace
    git_dir = project_root / ".git"
    if git_dir.is_dir():
        rc, out, err = _run(["git", "pull", "--ff-only"], cwd=str(project_root), timeout=60)
        if rc == 0:
            steps.append(f"git: pulled ({out.strip().splitlines()[-1] if out.strip() else 'ok'})")
        else:
            steps.append(f"git: FAILED ({err.strip()[:200]})")
            log.error("git pull failed: %s", err)
    else:
        steps.append("git: skipped (no .git dir)")

    # Restart strategy: the app process runs as www-data (no sudo), so it
    # can't call `systemctl restart` directly — polkit rejects non-root
    # callers. Instead we touch a flag file under /run; a separate systemd
    # .path unit (deploy/backend-restart.path) watches the file and runs
    # the restart as root when it appears. The .service unit also removes
    # the flag in ExecStartPre so the next deploy triggers a fresh
    # PathExists= event.
    #
    # The flag file must be pre-created root-owned with mode 0664 and
    # group=www-data (see deploy/backend-restart.conf + the README
    # install section). /run/ is mode 0755 owned by root, so touch()ing
    # a root-owned file there as www-data returns EACCES otherwise.
    #
    # Why not signal uvicorn directly: the unit runs in a hardened
    # sandbox (ProtectSystem=strict, ReadWritePaths=/opt/portfolio), so
    # sending a signal to $MAINPID from outside isn't reliable across
    # versions — letting systemd do the restart through a .path unit is
    # the supported path.
    systemd_unit = getattr(settings, "deploy_systemd_unit", "backend")
    restart_flag = Path(getattr(settings, "deploy_restart_flag", "/run/backend.restart"))
    if systemd_unit and Path("/run/systemd/system").exists():
        try:
            restart_flag.parent.mkdir(parents=True, exist_ok=True)
            restart_flag.touch(exist_ok=True)
            steps.append(f"systemd: restart flag set ({restart_flag})")
        except OSError as e:
            steps.append(f"systemd: FAILED ({e})")
            log.error("could not write restart flag %s: %s", restart_flag, e)
    else:
        steps.append("systemd: skipped (no systemd)")

    summary = " · ".join(steps)
    log.info("Deploy complete: %s", summary)

    # Notify. The summary line carries success/failure info: "FAILED" appears
    # in any failing step's value, "skipped" / "pulled" / "restarted" otherwise.
    try:
        from ..notify import notify
        failed = "FAILED" in summary
        notify(
            title="Deploy FAILED" if failed else "Deploy ok",
            message=summary,
            priority=4 if failed else 2,  # high on failure, low on success
            tags=("rotating_light" if failed else "white_check_mark", "rocket"),
        )
    except Exception:  # noqa: BLE001
        log.exception("notify() raised (shouldn't happen)")

    return PlainTextResponse(f"ok · {summary}\n")
