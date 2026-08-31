"""Chatbot core: OpenRouter streaming, history management, fallback policy.

The route in routes/chat.py is HTTP-only (parses, validates, rate-limits).
This module does the work: talk to OpenRouter, manage the message history,
stream tokens, log to the DB, and decide when to give up.

Key behaviours (per agreed spec):
  - One exponential-backoff retry on HTTP 429 from OpenRouter (2s wait).
  - Hard fallback after that: return a structured error the frontend
    renders as 'service busy, email me instead'.
  - Hard cap on chat history length (settings.chat_max_history_messages)
    so the prompt doesn't bloat across long sessions.
  - Hard cap on max_tokens (settings.chat_max_tokens) to bound cost on
    free-tier models.
  - Every user + assistant message is logged to chat_messages, plus a
    chat_sessions row updated with last_active_at + message_count.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import sqlite3

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

log = logging.getLogger(__name__)


# --- Error / sentinel types ------------------------------------------------

@dataclass
class ChatFallback:
    """Streamed to the client when the model is unreachable. The frontend
    renders the fallback message and a 'contact me' link."""
    reason: str          # "rate_limit" | "timeout" | "upstream_error" | "no_key"
    message: str         # user-facing text


@dataclass
class ChatError:
    """Streamed to the client when the route itself failed (config bug,
    unexpected exception, validation failure). Distinct from ChatFallback,
    which is the model being unreachable while the route is healthy."""
    reason: str          # "server_misconfigured" | "server_error" | "bad_request" | "stream_interrupted"
    message: str         # user-facing text


@dataclass
class ChatToken:
    """One delta from the assistant."""
    text: str


# --- Session + history -----------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _touch_session(conn: sqlite3.Connection, session_id: str, ip: str, ua: str) -> None:
    """Ensure a chat_sessions row exists; bump last_active_at and message_count."""
    row = conn.execute(
        "SELECT id, message_count FROM chat_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, ip, user_agent, created_at, last_active_at, message_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (session_id, ip, ua, _now(), _now()),
        )
    else:
        conn.execute(
            "UPDATE chat_sessions SET last_active_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )


def _log_message(conn: sqlite3.Connection, session_id: str, role: str, content: str, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, created_at, error) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, _now(), error),
    )
    if error is None:
        conn.execute(
            "UPDATE chat_sessions SET message_count = message_count + 1 WHERE session_id = ?",
            (session_id,),
        )


def _load_history(conn: sqlite3.Connection, session_id: str, max_messages: int) -> list[dict]:
    """Return the last N messages for this session, oldest first, in OpenAI
    chat format. System prompt is added separately by the caller."""
    rows = conn.execute(
        "SELECT role, content FROM chat_messages "
        "WHERE session_id = ? AND error IS NULL "
        "ORDER BY id DESC LIMIT ?",
        (session_id, max_messages),
    ).fetchall()
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# --- OpenRouter call -------------------------------------------------------

# OpenRouter is OpenAI-compatible; the streaming chat completions endpoint
# returns Server-Sent Events in the same shape as OpenAI.
_OPENROUTER_URL = f"{settings.openrouter_base_url}/chat/completions"


@dataclass
class _UpstreamResult:
    text: str
    finish_reason: str | None
    raw: dict | None = None


# Sentinel pushed onto the pump-to-consumer queue when the upstream async
# generator is exhausted. Distinct from None and from any ChatToken /
# ChatFallback so the consumer can detect clean EOF without ambiguity.
_SENTINEL_EOF = object()


async def _call_openrouter_stream(messages: list[dict], max_tokens: int) -> AsyncIterator[str | _UpstreamResult | ChatFallback]:
    """Yield token deltas. Yields ChatFallback (a sentinel) if upstream is
    unreachable or rate-limited. Caller is responsible for backoff policy.
    """
    if not settings.openrouter_api_key:
        yield ChatFallback(
            reason="no_key",
            message="The chatbot is not configured (missing API key). Email me instead.",
        )
        return

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # OpenRouter recommends these; helps the dashboard attribution.
        "HTTP-Referer": "https://lukasgruenzweil.com",
        # ASCII-only — httpx validates header values as ASCII and will raise
        # UnicodeEncodeError on 'ü' / em-dash. Use a transliterated form here;
        # the dashboard only shows the title, the model never sees it.
        "X-Title": "Lukas Gruenzweil - Portfolio Chatbot",
    }
    body = {
        "model": settings.openrouter_model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }

    accumulated: list[str] = []
    finish_reason: str | None = None
    last_error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=settings.chat_request_timeout_seconds) as client:
            async with client.stream("POST", _OPENROUTER_URL, headers=headers, json=body) as resp:
                if resp.status_code == 429:
                    yield ChatFallback(reason="rate_limit", message="")
                    return
                if resp.status_code >= 500:
                    yield ChatFallback(reason="upstream_error", message="")
                    return
                if resp.status_code >= 400:
                    # 4xx other than 429 — read the body for logging, then fallback.
                    try:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")[:512]
                    except Exception:  # noqa: BLE001
                        err_body = ""
                    log.warning("OpenRouter %s: %s", resp.status_code, err_body)
                    yield ChatFallback(reason="upstream_error", message="")
                    return

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
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        accumulated.append(piece)
                        yield piece
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr
    except httpx.TimeoutException:
        yield ChatFallback(reason="timeout", message="")
        return
    except httpx.HTTPError as e:
        log.warning("OpenRouter HTTP error: %s", e)
        yield ChatFallback(reason="upstream_error", message="")
        return

    yield _UpstreamResult(text="".join(accumulated), finish_reason=finish_reason)


# --- Public API used by the route ------------------------------------------

# User-facing fallback messages. Kept short, in character, and offer the
# email as the next step. The frontend decorates with the email link.
FALLBACK_MESSAGES = {
    "rate_limit": "The chatbot is rate-limited right now (free model, expected). Try again in a minute, or email me — that's the fastest way to reach me anyway.",
    "timeout": "The model is being slow. Try again, or email me if you'd rather not wait.",
    "upstream_error": "The chatbot hit an error upstream. Email me and I'll get back to you directly.",
    "no_key": "The chatbot isn't configured on this server. Email me instead.",
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


async def stream_chat_response(
    session_id: str,
    user_message: str,
    ip: str,
    user_agent: str,
) -> AsyncIterator[str]:
    """Public entry point. Yields SSE-formatted strings: 'data: {...}\\n\\n'.

    Wire format:
      data: {"type":"token","text":"..."}
      data: {"type":"fallback","reason":"...","message":"..."}
      data: {"type":"error","reason":"...","message":"..."}
      data: {"type":"done"}

    This generator never raises — any unhandled exception is converted to an
    `error` event with a `reason` code so the client always gets *something*
    renderable instead of an abruptly-terminated stream.
    """
    if not session_id or not user_message.strip():
        yield f"data: {json.dumps({'type': 'error', 'reason': 'bad_request', 'message': ERROR_MESSAGES['bad_request']})}\n\n"
        return

    # Truncate absurd inputs at the door — the LLM would just ignore them
    # and they cost tokens.
    user_message = user_message.strip()[:4000]

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
    fallback: ChatFallback | None = None
    error: ChatError | None = None
    try:
        # System prompt + history. The SQLite work is synchronous and the
        # same DB is being written by the analytics middleware on every
        # other request, so a contended connect can block for the full
        # 10s timeout. Run it in a thread so the event loop stays free
        # and the keepalive ticker (started below) can fire while we wait.
        def _pre_stream_db() -> list[dict]:
            with get_db() as conn:
                _touch_session(conn, session_id, ip, user_agent)
                history = _load_history(conn, session_id, settings.chat_max_history_messages)
                # Log the user message immediately so a crash mid-stream
                # still records it.
                _log_message(conn, session_id, "user", user_message)
            return history

        content = get_content()
        system_prompt = content.chatbot_system_prompt
        history = await asyncio.to_thread(_pre_stream_db)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # One-shot backoff retry: if the first attempt yields a rate_limit
        # fallback, sleep 2s and try again. If that also fails, hard fallback.
        for attempt in (0, 1):
            # Each attempt gets a fresh accumulator and a fresh message history
            # (we re-use `messages`, which is fine).
            attempt_text: list[str] = []
            attempt_fallback: ChatFallback | None = None
            try:
                # Race the upstream iteration against a periodic keepalive.
                # The OpenRouter free-tier model can pause for tens of seconds
                # on long answers, and the 2s `asyncio.sleep` on a 429 retry
                # is another no-flow window — both would let Cloudflare's
                # idle-disconnect timer fire.
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
                        async for item in _call_openrouter_stream(
                            messages, settings.chat_max_tokens,
                        ):
                            await item_queue.put(item)
                    except Exception as e:  # noqa: BLE001
                        # Forward as a fallback so the consumer can
                        # distinguish upstream errors from clean exits.
                        await item_queue.put(ChatFallback(reason="upstream_error", message=str(e)))
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
                        if isinstance(item, ChatFallback):
                            attempt_fallback = item
                            break
                        if isinstance(item, _UpstreamResult):
                            # Final result of the stream.
                            break
                        # Token delta
                        attempt_text.append(item)
                        yield f"data: {json.dumps({'type': 'token', 'text': item})}\n\n"
                finally:
                    if not pump_task.done():
                        pump_task.cancel()
                    try:
                        await pump_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                log.exception("stream_chat_response iteration failed")
                attempt_fallback = ChatFallback(reason="upstream_error", message=str(e))

            if attempt_fallback is None:
                accumulated_text = "".join(attempt_text)
                fallback = None
                break

            # Got a fallback. Retry only on rate_limit, only once, with a backoff.
            if attempt_fallback.reason == "rate_limit" and attempt == 0:
                log.info("OpenRouter 429, retrying after 2s backoff (session=%s)", session_id)
                # Keep the wire warm during the backoff.
                await asyncio.sleep(2.0)
                yield ": keepalive\n\n"
                continue
            fallback = attempt_fallback
            break
    except (ContentError, RuntimeError) as e:
        # Content not loaded, malformed YAML, or anything else that says
        # "the server itself is broken, not the upstream model."
        log.exception("chat misconfigured: %s", e)
        error = ChatError(reason="server_misconfigured", message=ERROR_MESSAGES["server_misconfigured"])
    except Exception as e:  # noqa: BLE001
        log.exception("chat unexpected error: %s", e)
        error = ChatError(reason="server_error", message=ERROR_MESSAGES["server_error"])

    # Log assistant result + emit the final event. Both halves are
    # individually protected — a DB error here must not strand the stream
    # without an event. Same `to_thread` rationale as the pre-stream write.
    def _post_stream_db() -> None:
        with get_db() as conn:
            if error is not None:
                _log_message(conn, session_id, "assistant", error.message, error=error.reason)
            elif fallback is not None:
                _log_message(
                    conn, session_id, "assistant",
                    FALLBACK_MESSAGES.get(fallback.reason, "Chat unavailable."),
                    error=fallback.reason,
                )
            else:
                _log_message(conn, session_id, "assistant", accumulated_text)

    try:
        await asyncio.to_thread(_post_stream_db)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to log chat result: %s", e)

    try:
        if error is not None:
            yield f"data: {json.dumps({'type': 'error', 'reason': error.reason, 'message': error.message})}\n\n"
        elif fallback is not None:
            yield f"data: {json.dumps({'type': 'fallback', 'reason': fallback.reason, 'message': FALLBACK_MESSAGES.get(fallback.reason, '')})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    except Exception as e:  # noqa: BLE001
        # Stream is already broken (client disconnected mid-write). Nothing
        # we can usefully do.
        log.warning("failed to emit final chat event: %s", e)


def new_session_id() -> str:
    return uuid.uuid4().hex
