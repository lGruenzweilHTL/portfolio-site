"""ntfy.sh-compatible notification helper.

One function: notify(title, message, **kwargs). Sends a single HTTP POST to
`{NTFY_BASE_URL}/{NTFY_TOPIC}` with the relevant headers. Fire-and-forget —
never raises into the caller. Failures are logged at warning level.

Configuration (env / .env):
  NTFY_ENABLED       "1" to send, anything else to disable. Default: disabled
                     in dev, but you set it to 1 once your ntfy is reachable.
  NTFY_BASE_URL      e.g. https://ntfy.yourdomain.com  (no trailing slash)
  NTFY_TOPIC         single topic name (e.g. "lukas-portfolio")
  NTFY_AUTH_TOKEN    optional Bearer token, if your ntfy requires auth

Message format (ntfy headers):
  Title     -> Title
  Priority  -> Priority (1=min, 5=max; default 3)
  Tags      -> Tags (comma-joined; for emoji like "white_check_mark")
  Click     -> Click (URL; tappable link in the phone UI)
  Actions   -> not exposed here; if you need them, extend the signature

References:
  https://docs.ntfy.sh/publish/
"""
from __future__ import annotations

import logging
from typing import Iterable

import httpx

from .config import settings

log = logging.getLogger(__name__)


def notify(
    title: str,
    message: str,
    *,
    priority: int = 3,
    tags: Iterable[str] | None = None,
    click: str | None = None,
) -> None:
    """Send a notification. Returns immediately. Never raises.

    If notifications are disabled (NTFY_ENABLED != "1") or unconfigured,
    this is a no-op (other than a single debug log on the first call).
    """
    if str(settings.ntfy_enabled).lower() not in ("1", "true", "yes"):
        return
    if not settings.ntfy_base_url or not settings.ntfy_topic:
        log.debug("ntfy: skipped (NTFY_BASE_URL or NTFY_TOPIC not set)")
        return

    url = f"{settings.ntfy_base_url.rstrip('/')}/{settings.ntfy_topic}"
    headers = {
        "Title": title,
        "Priority": str(int(priority)),
    }
    if tags:
        # ntfy accepts comma-separated tags; first tag can be an emoji shortcode.
        headers["Tags"] = ",".join(tags)
    if click:
        headers["Click"] = click
    if settings.ntfy_auth_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_auth_token}"

    try:
        # Short timeout — if ntfy is down, we don't want a 30s hang in a
        # request handler. The whole point of fire-and-forget is to not
        # block the user.
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, content=message.encode("utf-8"), headers=headers)
        if resp.status_code >= 400:
            log.warning("ntfy returned %s: %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as e:
        log.warning("ntfy request failed: %s", e)
    except Exception as e:  # noqa: BLE001
        # Absolute last-resort safety net: never let a notify() break the caller.
        log.warning("ntfy unexpected error: %s", e)
