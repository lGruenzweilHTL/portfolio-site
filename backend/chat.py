"""Chatbot core: provider streaming, history management, fallback policy.

The route in routes/chat.py is HTTP-only (parses, validates, rate-limits).
This module does the work: talk to the provider, manage the message
history, stream tokens, log to the DB, and decide when to give up.

The provider is any OpenAI-compatible chat completions endpoint at
OPENROUTER_BASE_URL. Production uses the Gemini API directly; OpenRouter
still works when the base URL points at it. The env vars keep their
OPENROUTER_* names.

Key behaviours:
  - Every model attempt ends in one outcome kind (backend/chat_errors.py),
    and each kind has its own fallback and cooldown rule. Switching models
    only happens before the first token reached the client.
  - A moderated message (explicit block, or two models in a row returning
    nothing) stops the loop after that call, cools nothing down, is answered
    with a refusal, and is kept out of the session history.
  - Once the list is used up, one backoff retry (2s wait) of every model
    that answered 429. The whole request is bounded by
    CHAT_TOTAL_TIMEOUT_SECONDS.
  - Hard cap on chat history length (settings.chat_max_history_messages)
    and on max_tokens (settings.chat_max_tokens).
  - Every user + assistant message is logged to chat_messages; assistant
    rows carry the answering model and, after any failed attempt, the list
    of attempts with the provider's own error text (admin only).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx
import sqlite3

from . import chat_errors as ce
from .chat_errors import AttemptOutcome
from .chat_models import enabled_models
from .config import settings
from .content import ContentError, get_content
from .db.models import get_db

# How often to send an SSE comment line while a chat is in flight. SSE
# comments (": keepalive\n\n") are valid per the SSE spec, ignored by every
# browser parser, but count as bytes on the wire — which keeps Cloudflare
# Tunnel's idle-disconnect window from firing during long upstreams or DB
# contention. Must be well under the proxy's idle timeout (Tunnel default
# is 100s) and well under `settings.chat_request_timeout_seconds` so the
# keepalive isn't itself a no-op when the upstream is hung.
_KEEPALIVE_INTERVAL_S = 15.0

# Seconds to wait before the one retry of rate-limited models.
_BACKOFF_S = 2.0

# How much of a non-2xx body is read for classification and logging.
_ERROR_BODY_LIMIT = 2048

log = logging.getLogger(__name__)


# --- Model cooldown --------------------------------------------------------

# Per-process. A model that answered 429, is overloaded or gone is skipped
# by later requests until its cooldown runs out, so each request doesn't
# walk the same dead models (and sit through the backoff) again. Once the
# cooldown ends the model is back at its place in the list.
_cooldown_until: dict[str, float] = {}


def _available_models() -> list[str]:
    """Enabled models not in cooldown, in order. If every model is cooling
    down, all of them: a request should still get its try."""
    now = time.monotonic()
    models = enabled_models()
    return [m for m in models if _cooldown_until.get(m, 0.0) <= now] or models


def _cool_down(model: str, seconds: float) -> None:
    _cooldown_until[model] = time.monotonic() + seconds


def clear_cooldown(model: str) -> None:
    _cooldown_until.pop(model, None)


def cooldown_remaining(model: str) -> float:
    return max(0.0, _cooldown_until.get(model, 0.0) - time.monotonic())


def model_label(model: str) -> str:
    """Model id for display: provider prefix and :free suffix stripped."""
    return model.split("/", 1)[-1].split(":", 1)[0]


def active_model_label() -> str | None:
    """The model the next request starts with, or None if none is enabled."""
    models = _available_models()
    return model_label(models[0]) if models else None


def _is_openrouter() -> bool:
    return "openrouter" in (urlparse(settings.openrouter_base_url).hostname or "")


def provider_label() -> str:
    host = urlparse(settings.openrouter_base_url).hostname or ""
    if "openrouter" in host:
        return "OpenRouter"
    if host.endswith("googleapis.com"):
        return "Gemini API"
    return host


# --- Notifications for config problems -------------------------------------

# A retired model or a broken key needs the owner; everything else is too
# noisy to notify. Once per model per process, and once an hour for auth.
_notified_unavailable: set[str] = set()
_last_auth_notify = 0.0
_AUTH_NOTIFY_INTERVAL_S = 3600.0


def _notify_config_problem(outcome: AttemptOutcome) -> None:
    global _last_auth_notify
    if outcome.kind == ce.MODEL_UNAVAILABLE:
        if outcome.model in _notified_unavailable:
            return
        _notified_unavailable.add(outcome.model)
        title = f"Chat model unavailable: {outcome.model}"
    elif outcome.kind == ce.PROVIDER_AUTH:
        now = time.monotonic()
        if _last_auth_notify and now - _last_auth_notify < _AUTH_NOTIFY_INTERVAL_S:
            return
        _last_auth_notify = now
        title = f"Chat provider rejected the key ({provider_label()})"
    else:
        return
    message = f"{outcome.model} -> {outcome.kind} (HTTP {outcome.http_status})\n{outcome.provider_message or ''}"

    def _send() -> None:
        try:
            from .notify import notify
            notify(title=title, message=message, priority=4, tags=("warning", "robot"))
        except Exception:  # noqa: BLE001
            log.exception("notify() raised (shouldn't happen)")

    # notify() is a blocking HTTP call; don't hold up the stream for it.
    try:
        asyncio.get_running_loop().run_in_executor(None, _send)
    except RuntimeError:
        _send()


# --- Error / sentinel types ------------------------------------------------

@dataclass
class ChatFallback:
    """Streamed to the client when the model is unreachable or refused. The
    frontend renders the fallback message and, for most reasons, a 'contact
    me' link."""
    reason: str          # "rate_limit" | "timeout" | "upstream_error" | "no_key" | "server_misconfigured" | "moderated"
    message: str         # user-facing text


@dataclass
class ChatError:
    """Streamed to the client when the route itself failed (config bug,
    unexpected exception, validation failure). Distinct from ChatFallback,
    which is the model being unreachable while the route is healthy."""
    reason: str          # "server_misconfigured" | "server_error" | "bad_request" | "stream_interrupted"
    message: str         # user-facing text


# --- Session + history -----------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _touch_session(conn: sqlite3.Connection, session_id: str, ua: str) -> None:
    """Ensure a chat_sessions row exists; bump last_active_at. No IP is
    stored (the rate limit keeps it in memory only)."""
    row = conn.execute(
        "SELECT id, message_count FROM chat_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, ip, user_agent, created_at, last_active_at, message_count) "
            "VALUES (?, NULL, ?, ?, ?, 0)",
            (session_id, ua, _now(), _now()),
        )
    else:
        conn.execute(
            "UPDATE chat_sessions SET last_active_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )


def _log_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
    error: str | None = None,
    *,
    model: str | None = None,
    error_detail: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, created_at, error, model, error_detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, role, content, _now(), error, model, error_detail),
    )
    if error is None:
        conn.execute(
            "UPDATE chat_sessions SET message_count = message_count + 1 WHERE session_id = ?",
            (session_id,),
        )
    return cur.lastrowid


def _load_history(conn: sqlite3.Connection, session_id: str, max_messages: int) -> list[dict]:
    """Return the last N messages for this session, oldest first, in OpenAI
    chat format. System prompt is added separately by the caller. Rows with
    an error (failed turns, moderated user messages) are never resent."""
    rows = conn.execute(
        "SELECT role, content FROM chat_messages "
        "WHERE session_id = ? AND error IS NULL "
        "ORDER BY id DESC LIMIT ?",
        (session_id, max_messages),
    ).fetchall()
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# --- Provider call ---------------------------------------------------------

# Sentinel pushed onto the pump-to-consumer queue when the upstream async
# generator is exhausted. Distinct from None and from any token / outcome
# so the consumer can detect clean EOF without ambiguity.
_SENTINEL_EOF = object()


def _completions_url() -> str:
    return f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"


async def _read_error_body(resp: httpx.Response) -> str:
    raw = b""
    try:
        async for chunk in resp.aiter_bytes():
            raw += chunk
            if len(raw) >= _ERROR_BODY_LIMIT:
                break
    except Exception:  # noqa: BLE001
        pass
    return raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace")


async def _call_provider_stream(
    messages: list[dict], max_tokens: int, model: str, timeout: float,
) -> AsyncIterator[str | AttemptOutcome]:
    """Yield token deltas, then exactly one AttemptOutcome describing how
    the attempt ended. Never raises for provider or transport errors; the
    caller owns the fallback policy."""
    if not settings.openrouter_api_key:
        yield AttemptOutcome(kind=ce.NO_KEY, model=model, provider_message="missing API key")
        return

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    if _is_openrouter():
        # OpenRouter's dashboard attribution. ASCII-only: httpx validates
        # header values as ASCII and would raise on 'ü'.
        headers["HTTP-Referer"] = settings.base_url
        headers["X-Title"] = "Lukas Gruenzweil - Portfolio Chatbot"
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }

    accumulated: list[str] = []
    finish_reason: str | None = None
    reasoning = False
    status: int | None = None
    error: dict | None = None
    raw_error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", _completions_url(), headers=headers, json=body) as resp:
                status = resp.status_code
                if not 200 <= status < 300:
                    raw_error = await _read_error_body(resp)
                    error = ce.parse_error_body(raw_error)
                else:
                    # Stream SSE lines: "data: {...}\n\n" with a terminating "data: [DONE]"
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        # An error inside a 200 stream ends the attempt.
                        chunk_error = ce.extract_error(chunk)
                        if chunk_error is not None:
                            error = chunk_error
                            raw_error = payload[:500]
                            break
                        if not isinstance(chunk, dict):
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("reasoning") or delta.get("reasoning_content"):
                            reasoning = True
                        piece = delta.get("content")
                        if piece:
                            accumulated.append(piece)
                            yield piece
                        fr = choices[0].get("finish_reason")
                        if fr:
                            finish_reason = fr
    except httpx.HTTPError as e:
        yield AttemptOutcome(
            kind=ce.classify(exc=e), model=model, http_status=status,
            finish_reason=finish_reason, provider_message=(str(e) or type(e).__name__)[:500],
            reasoning=reasoning,
        )
        return

    text = "".join(accumulated)
    yield AttemptOutcome(
        kind=ce.classify(status=status, error=error, finish_reason=finish_reason, text=text),
        model=model,
        http_status=status,
        finish_reason=finish_reason,
        provider_message=ce.error_text(error, raw_error),
        reasoning=reasoning,
    )


# --- Public API used by the route ------------------------------------------

# Shown when a message was blocked by the provider's safety filter. No email
# link: it is a refusal, not a fault.
MODERATED_MESSAGE = "I can't help with that one. Ask me about Lukas's projects, skills or experience instead."

# User-facing fallback messages. Kept short, in character, and offer the
# email as the next step. The frontend decorates with the email link.
FALLBACK_MESSAGES = {
    "rate_limit": "The chatbot is rate-limited right now (free model, expected). Try again in a minute, or email me — that's the fastest way to reach me anyway.",
    "timeout": "The model is being slow. Try again, or email me if you'd rather not wait.",
    "upstream_error": "The chatbot hit an error upstream. Email me and I'll get back to you directly.",
    "no_key": "The chatbot isn't configured on this server. Email me instead.",
    "server_misconfigured": "The chatbot is temporarily unavailable — there's a config issue on my end. Email me and I'll get it sorted.",
    "moderated": MODERATED_MESSAGE,
}

# User-facing error messages for route-level failures (vs. the model-level
# fallbacks above). These fire when the route itself can't proceed — e.g.
# content didn't load, an unhandled exception, bad request. Same tone.
ERROR_MESSAGES = {
    "server_misconfigured": "The chatbot is temporarily unavailable — there's a config issue on my end. Email me and I'll get it sorted.",
    "server_error": "The chatbot hit an unexpected error. Try again, or email me if it keeps happening.",
    "bad_request": "Your message didn't go through. Try rephrasing, or email me if it keeps failing.",
    "stream_interrupted": "The connection dropped before the answer finished. Try again, or email me if it keeps happening.",
}


def _log_attempt(outcome: AttemptOutcome, session_id: str) -> None:
    kind = outcome.kind
    if kind == ce.EMPTY_REPLY and outcome.reasoning:
        kind = "empty_reply (after reasoning)"
    elif outcome.heuristic:
        kind = "moderated (two empty replies)"
    log.warning(
        'model %s -> %s (status=%s, finish=%s, provider="%s") session=%s',
        outcome.model, kind, outcome.http_status, outcome.finish_reason,
        (outcome.provider_message or "")[:200], session_id,
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def stream_chat_response(
    session_id: str,
    user_message: str,
    user_agent: str,
) -> AsyncIterator[str]:
    """Public entry point. Yields SSE-formatted strings: 'data: {...}\\n\\n'.

    Wire format:
      data: {"type":"model","model":"...","provider":"..."}   (once, before the first token)
      data: {"type":"token","text":"..."}
      data: {"type":"fallback","reason":"...","message":"..."}
      data: {"type":"error","reason":"...","message":"..."}
      data: {"type":"done"}

    This generator never raises — any unhandled exception is converted to an
    `error` event with a `reason` code so the client always gets *something*
    renderable instead of an abruptly-terminated stream.
    """
    if not session_id or not user_message.strip():
        yield _sse({"type": "error", "reason": "bad_request", "message": ERROR_MESSAGES["bad_request"]})
        return

    # The route rejects over-long messages with a proper error; this is only
    # a backstop for other callers so nothing oversized reaches the model.
    user_message = user_message.strip()[:settings.chat_max_message_chars]

    # Send an SSE comment immediately so the response body starts flowing
    # before any blocking work. Until the first `yield` runs, Starlette's
    # StreamingResponse hasn't sent headers and zero bytes have reached
    # the proxy — Cloudflare Tunnel can drop the connection in that gap.
    # SSE comment lines (": ...") are valid per spec, ignored by browsers,
    # and `sawAnyEvent` stays false in the front-end until a real `data:`
    # event arrives, so the existing stream_interrupted guard still works.
    yield ": keepalive\n\n"

    # Everything below may fail in ways we can't predict (missing content,
    # DB hiccup, Pydantic blow-up, network blip mid-yield). We capture the
    # outcome here and turn any exception into a ChatError so the SSE stream
    # always emits a renderable event instead of terminating mid-flight.
    accumulated_text = ""
    answered_by: str | None = None
    fallback: ChatFallback | None = None
    error: ChatError | None = None
    attempts: list[AttemptOutcome] = []
    user_row_id: int | None = None
    try:
        # System prompt + history. The SQLite work is synchronous and the
        # same DB is being written by the analytics middleware on every
        # other request, so a contended connect can block for the full
        # 10s timeout. Run it in a thread so the event loop stays free
        # and the keepalive ticker (started below) can fire while we wait.
        def _pre_stream_db() -> tuple[list[dict], int]:
            with get_db() as conn:
                _touch_session(conn, session_id, user_agent)
                history = _load_history(conn, session_id, settings.chat_max_history_messages)
                # Log the user message immediately so a crash mid-stream
                # still records it.
                row_id = _log_message(conn, session_id, "user", user_message)
            return history, row_id

        content = get_content()
        system_prompt = content.chatbot_system_prompt
        history, user_row_id = await asyncio.to_thread(_pre_stream_db)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # Model fallback: try each enabled model in turn, following the
        # rules in chat_errors.py. Each model has its own quota bucket, so
        # the next one usually has room. Once the list is used up, every
        # model that answered 429 gets one more try after a 2s backoff
        # (helps with burst limits, not per-minute ones). Then hard
        # fallback. The whole loop is bounded by the total time budget.
        plan = list(_available_models())
        if not plan:
            fallback = ChatFallback(reason="no_key", message=FALLBACK_MESSAGES["no_key"])
        deadline = time.monotonic() + settings.chat_total_timeout_seconds
        rate_limited: list[str] = []
        backoff_used = False
        i = 0
        while i < len(plan):
            remaining = deadline - time.monotonic()
            if remaining <= 0 and attempts:
                log.warning("chat time budget spent after %d attempt(s) (session=%s)", len(attempts), session_id)
                break
            model = plan[i]
            attempt_timeout = max(1.0, min(settings.chat_request_timeout_seconds, remaining))
            attempt_text: list[str] = []
            outcome: AttemptOutcome | None = None
            try:
                # Race the upstream iteration against a periodic keepalive.
                # A model can pause for tens of seconds on long answers, and
                # the backoff sleep is another no-flow window — both would
                # let Cloudflare's idle-disconnect timer fire.
                #
                # We pump the upstream async generator in its own task and
                # read items from a queue here. asyncio.wait_for on the
                # queue's get() does NOT cancel the pump task (the
                # underlying async generator is left running), so a
                # keepalive tick is safe — the next token will be picked
                # up on the next iteration. This avoids the trap of
                # `wait_for` on `__anext__()`, which would terminate the
                # generator on timeout.
                # Unbounded queue. We want a backlog, not back-pressure:
                # if the upstream produces many tokens in a row, we
                # accumulate them and the consumer drains as fast as the
                # proxy can flush. A bounded queue would deadlock with
                # the consumer's `wait_for` timeout.
                item_queue: asyncio.Queue[object] = asyncio.Queue()

                async def _pump_upstream() -> None:
                    try:
                        async for item in _call_provider_stream(
                            messages, settings.chat_max_tokens, model, attempt_timeout,
                        ):
                            await item_queue.put(item)
                    except Exception as e:  # noqa: BLE001
                        # Forward as an outcome so the consumer can
                        # distinguish upstream errors from clean exits.
                        await item_queue.put(AttemptOutcome(
                            kind=ce.NETWORK, model=model, provider_message=str(e)[:500],
                        ))
                    finally:
                        await item_queue.put(_SENTINEL_EOF)

                pump_task = asyncio.create_task(_pump_upstream())
                try:
                    while True:
                        try:
                            item = await asyncio.wait_for(
                                item_queue.get(),
                                timeout=_KEEPALIVE_INTERVAL_S,
                            )
                        except asyncio.TimeoutError:
                            # Upstream hasn't yielded in
                            # `_KEEPALIVE_INTERVAL_S` seconds. Send a
                            # keepalive to the client so the proxy sees
                            # traffic; the pump task keeps running and
                            # the next token will arrive via the queue.
                            yield ": keepalive\n\n"
                            continue
                        if item is _SENTINEL_EOF:
                            break
                        if isinstance(item, AttemptOutcome):
                            outcome = item
                            break
                        # Token delta. Tell the client which model is
                        # answering before its first token; models only
                        # switch before any token, so this fires once.
                        if not attempt_text:
                            yield _sse({"type": "model", "model": model_label(model), "provider": provider_label()})
                        attempt_text.append(item)
                        yield _sse({"type": "token", "text": item})
                finally:
                    if not pump_task.done():
                        pump_task.cancel()
                    try:
                        await pump_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                log.exception("stream_chat_response iteration failed")
                outcome = AttemptOutcome(kind=ce.NETWORK, model=model, provider_message=str(e)[:500])

            if outcome is None:
                # Pump ended without an outcome; shouldn't happen.
                text = "".join(attempt_text)
                outcome = AttemptOutcome(kind=ce.OK if text.strip() else ce.EMPTY_REPLY, model=model)

            if outcome.kind == ce.OK:
                clear_cooldown(model)
                accumulated_text = "".join(attempt_text)
                answered_by = model
                fallback = None
                break

            # Two models in a row agreeing on "nothing" for the same input is
            # almost always a silent safety block. A reply cut off by
            # max_tokens (finish=length) is a budget problem, not a block.
            if (
                outcome.kind == ce.EMPTY_REPLY
                and outcome.finish_reason != "length"
                and attempts
                and attempts[-1].kind == ce.EMPTY_REPLY
                and attempts[-1].finish_reason != "length"
            ):
                outcome.kind = ce.MODERATED
                outcome.heuristic = True

            attempts.append(outcome)
            _log_attempt(outcome, session_id)
            if outcome.kind in (ce.MODEL_UNAVAILABLE, ce.PROVIDER_AUTH):
                _notify_config_problem(outcome)

            if outcome.kind == ce.MODERATED:
                # Stop: no further models, no backoff, no cooldown.
                if attempt_text:
                    answered_by = model
                fallback = ChatFallback(reason="moderated", message=MODERATED_MESSAGE)
                break

            # Switching models is only safe before any token reached the
            # client; a mid-stream failure would otherwise splice two
            # different answers together.
            if attempt_text:
                answered_by = model
                fallback = _visitor_fallback(outcome.kind)
                break

            if outcome.kind in ce.LONG_COOLDOWN_KINDS:
                _cool_down(model, settings.chat_model_unavailable_cooldown_seconds)
            elif outcome.kind in ce.NORMAL_COOLDOWN_KINDS:
                _cool_down(model, settings.chat_model_cooldown_seconds)
            if outcome.kind == ce.RATE_LIMIT:
                rate_limited.append(model)
            if outcome.kind in ce.STOP_KINDS:
                fallback = _visitor_fallback(outcome.kind)
                break
            if i + 1 < len(plan):
                log.info("falling back to %s (session=%s)", plan[i + 1], session_id)
                i += 1
                continue
            if rate_limited and not backoff_used and deadline - time.monotonic() > _BACKOFF_S:
                log.info(
                    "models exhausted, retrying %d rate-limited model(s) after %.0fs backoff (session=%s)",
                    len(rate_limited), _BACKOFF_S, session_id,
                )
                backoff_used = True
                plan.extend(rate_limited)
                # Keep the wire warm during the backoff.
                await asyncio.sleep(_BACKOFF_S)
                yield ": keepalive\n\n"
                i += 1
                continue
            break

        if answered_by is None and fallback is None and attempts:
            # List exhausted or budget spent. Report a rate limit if any
            # model hit one: "try again in a minute" is more useful than
            # the last model's error.
            last = attempts[-1].kind
            fallback = _visitor_fallback(ce.RATE_LIMIT if rate_limited else last)
    except (ContentError, RuntimeError) as e:
        # Content not loaded, malformed YAML, or anything else that says
        # "the server itself is broken, not the upstream model."
        log.exception("chat misconfigured: %s", e)
        error = ChatError(reason="server_misconfigured", message=ERROR_MESSAGES["server_misconfigured"])
    except Exception as e:  # noqa: BLE001
        log.exception("chat unexpected error: %s", e)
        error = ChatError(reason="server_error", message=ERROR_MESSAGES["server_error"])

    detail = json.dumps([a.detail() for a in attempts], separators=(",", ":")) if attempts else None

    # Log assistant result + emit the final event. Both halves are
    # individually protected — a DB error here must not strand the stream
    # without an event. Same `to_thread` rationale as the pre-stream write.
    def _post_stream_db() -> None:
        with get_db() as conn:
            if error is not None:
                _log_message(conn, session_id, "assistant", error.message, error=error.reason, error_detail=detail)
            elif fallback is not None:
                if fallback.reason == "moderated" and user_row_id is not None:
                    # Keep the blocked message out of every later turn's
                    # history, or each turn would resend it and fail again.
                    conn.execute("UPDATE chat_messages SET error = 'moderated' WHERE id = ?", (user_row_id,))
                _log_message(
                    conn, session_id, "assistant", fallback.message,
                    error=fallback.reason, model=answered_by, error_detail=detail,
                )
            else:
                _log_message(conn, session_id, "assistant", accumulated_text, model=answered_by, error_detail=detail)

    try:
        await asyncio.to_thread(_post_stream_db)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to log chat result: %s", e)

    try:
        if error is not None:
            yield _sse({"type": "error", "reason": error.reason, "message": error.message})
        elif fallback is not None:
            yield _sse({"type": "fallback", "reason": fallback.reason, "message": fallback.message})
        yield _sse({"type": "done"})
    except Exception as e:  # noqa: BLE001
        # Stream is already broken (client disconnected mid-write). Nothing
        # we can usefully do.
        log.warning("failed to emit final chat event: %s", e)


def _visitor_fallback(kind: str) -> ChatFallback:
    reason = ce.VISITOR_REASON.get(kind, "upstream_error")
    return ChatFallback(reason=reason, message=FALLBACK_MESSAGES.get(reason, FALLBACK_MESSAGES["upstream_error"]))


def new_session_id() -> str:
    return uuid.uuid4().hex
