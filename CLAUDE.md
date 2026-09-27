# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Portfolio site for Lukas Grünzweil: one FastAPI process serves the static site, study notes, file downloads, résumé PDFs, a recruiter chatbot, analytics, feedback, an admin dashboard, and the GitHub deploy webhook. Ingress in prod is a Cloudflare Tunnel straight to uvicorn on :8000 (no nginx). `README.md` is detailed and kept accurate — read the relevant section before changing deploy, caching, or content schema.

## Commands

```bash
pip install -r requirements.txt           # Python 3.10+; venv at .venv
cp .env.example .env                      # dev bypasses (Turnstile, admin, webhook HMAC) are on by default
uvicorn backend.main:app --reload --port 8000   # run from repo root; API docs at /_api/docs
python scripts/render_resume_preview.py   # render résumé templates to backend/templates/_preview_*.html (no WeasyPrint needed)
```

There is no test suite, linter, or build step. Frontend (`site/`) is hand-written HTML/CSS/JS with no bundler.

## Architecture

- **`backend/main.py`** — app factory. Order matters: middlewares are added first, then routers (`backend/routes/`, one file per route group), then the `StaticFiles` catch-all mount of `site/` at `/` **last**, so API/admin/webhook paths resolve before the file server. Middlewares (outermost last-added): `StaticAssetCacheMiddleware` (sets `no-cache` via `setdefault`, never overriding stricter headers), `NotesCleanUrlMiddleware`, `TurnstileSitekeyMiddleware` (rewrites `__TURNSTILE_SITEKEY__` in HTML and sets `no-store`), `AnalyticsMiddleware` (best-effort pageview logging to SQLite), `Static404ToHtmlMiddleware`. Errors render `templates/error.html` for browsers and JSON for `/api/`, `/_api/`, `/webhook/`.
- **`/content/*.yaml` is the single source of truth** for résumé, chatbot persona/system prompt, and services catalog. `backend/content.py` loads + validates into a module-level singleton (`get_content()`); the deploy webhook replaces it via `load_content()`. Exception: the project cards in `site/index.html` are hand-written, not content-driven. Schema changes must be mirrored in `content.py` validation, `resume.html`/`resume_ats.html`, `services.html`, and `site/index.html`.
- **Résumé PDFs** (`backend/resume.py`) — WeasyPrint renders `resume.html` (styled, two-column) and `resume_ats.html` into `static/generated/`. Rendered in a background thread on startup and re-rendered when any template/asset or `content/resume.yaml` is newer than the PDFs. Missing native libs → route returns 503, app still boots.
- **WeasyPrint 63.1 ≠ Chrome.** It silently ignores flex `gap` and `calc()`, and ignores margins on absolutely positioned boxes. The browser preview script won't reveal these; use margins and precomputed values instead (see header note in `resume.html`).
- **Turnstile** — `site/turnstile.js` exposes `getTurnstileToken(container, action)`: chat and feedback each have their own explicitly rendered, invisible (`interaction-only`, `execution: 'execute'`) widget that mints a fresh single-use token per send and resets afterwards. The sitekey reaches JS via a `<meta name="turnstile-sitekey">` tag that the middleware rewrites. The backend checks `expected_action` (`chat` / `feedback`), except for Cloudflare's test secrets, which always answer with action `test`.
- **Chatbot** — `routes/chat.py` handles HTTP/validation/rate limiting; `backend/chat.py` streams from OpenRouter (SSE) with a one-shot backoff retry on rate limit and canned fallback messages. Sessions/messages logged to SQLite.
- **SQLite** (`resume.db`, gitignored) — schema in `backend/db/models.py`, created by `init_db()` on startup. Writes are best-effort and must never break a request.
- **Admin** — gated by Cloudflare Access header `Cf-Access-Authenticated-User-Email` (`ADMIN_DEV_BYPASS=1` in dev). Secure downloads in `site/files/secure/` are token-gated; every failure mode returns the same 404 deliberately.
- **Rate limits** are in-process per-route helpers; the `*_RATE_LIMIT` settings in `config.py` are currently not wired in (routes hardcode their limits).
- **Deploy** (`routes/deploy.py` + `deploy/`) — HMAC-verified webhook runs `git pull --ff-only` **first**, then reloads content, re-renders PDFs, then touches `/run/backend-restart/trigger`; a root-owned systemd `.path` unit restarts `backend.service`. Always returns 200 — failures appear as `FAILED` in the response body/ntfy. Upgrading installed units needs explicit `systemctl restart` (see README "Upgrading an existing install").

## Conventions

- SEO/GEO: the facts on the site live in four hand-synced places besides `content/resume.yaml`: the visible copy in `site/index.html`, its JSON-LD block, `site/llms.txt`, and the meta/OG tags. Change them together. New pages go into `site/sitemap.xml`. Canonical origin is `https://gruenzweil.cc`.
- Entry animations: add `rv` (plus `rv-left`/`rv-right`/`rv-stagger`) to animate an element on scroll; the hidden state is gated on `html.js`, so never hide content without it. The vektorgrid diagram exists twice (landscape + portrait SVG) and parts are addressed by `data-part`, not `id`.
- Caching: nothing is fingerprinted, so HTML/CSS/JS/JSON/SVG must revalidate; don't add `max-age`/`immutable` to in-place assets.
- Study notes: `site/notes/index.html` is the only directory index; `NotesCleanUrlMiddleware` serves it for every `/notes/<dir>/` (and redirects `/notes/<dir>` to add the slash), so don't put `index.html` files in subdirectories. `study.css` and `cheatsheet.js` are referenced by absolute paths (`/notes/notes.css`, `/notes/cheatsheet.js`); don't make them relative. `routes/notes.py` is the source of truth for `_listing` JSON.
- Commits use Conventional Commits with scopes (`feat(resume):`, `fix(deploy):`, `chore(scripts):`).
