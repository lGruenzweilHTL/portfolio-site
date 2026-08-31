"""Cloudflare Turnstile siteverify helper.

Docs: https://developers.cloudflare.com/turnstile/get-started/server-side-validation/

In dev (TURNSTILE_DEV_BYPASS=1), this returns success without making the HTTP
call so you can test the rest of the flow without real keys. The dev bypass
also logs every call so it's obvious in the logs when it's being used.

NEVER set TURNSTILE_DEV_BYPASS=1 in production. The chatbot and feedback
endpoints are the two consumers — both must verify before doing real work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)


@dataclass
class TurnstileResult:
    ok: bool
    error: str | None = None
    hostname: str | None = None
    action: str | None = None
    raw: dict | None = None


async def verify_turnstile(token: str | None, *, expected_action: str | None = None) -> TurnstileResult:
    """Verify a Turnstile token. Returns TurnstileResult; never raises.

    `expected_action` is optional — if set, the result.action must match.
    The Turnstile widget on the client can be configured with a data-action
    attribute that gets echoed back here.
    """
    if settings.turnstile_dev_bypass:
        log.warning("Turnstile dev bypass active — accepting token without verification")
        return TurnstileResult(ok=True, error=None, hostname=None, action=expected_action)

    if not token:
        return TurnstileResult(ok=False, error="missing-token")

    if not settings.turnstile_secret:
        log.error("TURNSTILE_SECRET not set; refusing to bypass in non-dev mode")
        return TurnstileResult(ok=False, error="server-misconfigured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": settings.turnstile_secret,
                    "response": token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        log.warning("Turnstile siteverify HTTP error: %s", e)
        return TurnstileResult(ok=False, error="siteverify-unreachable", raw={"error": str(e)})

    success = bool(data.get("success"))
    if not success:
        return TurnstileResult(
            ok=False,
            error="turnstile-failed",
            raw=data,
        )

    action = data.get("action")
    if expected_action and action != expected_action:
        return TurnstileResult(
            ok=False,
            error=f"action-mismatch (expected {expected_action!r}, got {action!r})",
            raw=data,
        )

    return TurnstileResult(
        ok=True,
        hostname=data.get("hostname"),
        action=action,
        raw=data,
    )
