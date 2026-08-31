# Portfolio Site + Backend

Lukas Grünzweil's portfolio site, served by a single FastAPI process that
also runs the recruiter chatbot, analytics, feedback, study notes, file
downloads, and admin dashboard.

See `portfolio-backend-architecture.md` for the design rationale.

## What runs where

- **Main site:** `/` — hand-written HTML/CSS/JS, served as static files
- **Study notes:** `/notes/` — `site/notes/`, hand-written cheatsheets for SYP/Java/NSCS/PL/SQL
- **Public files:** `/files/` — auto-generated listing of `site/files/`
- **Secure files:** `/files/secure/` — token-gated downloads of `site/files/secure/` (no listing; access only via signed URL generated from `/admin`)
- **Chatbot, analytics, feedback, deploy, admin:** all under `/api/*`, `/webhook/*`, `/admin`

The previous nginx setup is fully replaced. Cloudflare Tunnel remains
the only ingress; the tunnel's `config.yml` just repointed from
`localhost:80` (nginx) to `localhost:8000` (uvicorn).

## Layout

```
/site                      Static site, notes, and downloads (served as /, /notes/, /files/, /files/secure/)
  index.html               Main portfolio page (with chatbot + feedback wired in)
  chat.js                  Floating-button chatbot UI
  notes/                   Study cheatsheets (moved from /study)
  files/                   Public downloads (auto-listed)
  files/secure/            Protected downloads (token-gated, no listing)
  img/, favicon.svg
/backend                   FastAPI app
  main.py                  App factory, middlewares, static mount, error handlers
  config.py                Pydantic settings (env / .env)
  content.py               /content loader + system-prompt renderer
  chat.py                  OpenRouter streaming + fallback policy
  turnstile.py             siteverify helper with dev bypass
  resume.py                WeasyPrint PDF render (cache + invalidate)
  routes/                  One file per route group
  db/models.py             SQLite schema + connection helpers
  templates/               Resume PDF templates (light + dark), admin.html, error.html
/content                   Single source of truth (YAML)
  resume.yaml              Personal, projects, skills, beyond_code
  chatbot_personality.yaml Persona + system prompt template
/static/generated          Cached resume PDFs (gitignored, regenerated on deploy)
.env.example               Copy to .env; all required env vars documented
requirements.txt           Pinned Python dependencies
archive/                   Old nginx-era scripts and templates (kept for reference)
```

## System dependencies

Python 3.10+ is required.

WeasyPrint needs native libs on the host:

- **Debian/Ubuntu:** `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2`
- **Alpine:** `apk add pango harfbuzz cairo`
- **macOS:** `brew install pango harfbuzz cairo`

If the system libs are missing, the resume endpoint returns 503 with a
clear message rather than crashing the app.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # then edit values
uvicorn backend.main:app --reload --port 8000
```

- Main site: <http://localhost:8000/>
- Notes: <http://localhost:8000/notes/>
- Files: <http://localhost:8000/files/>
- Admin: <http://localhost:8000/admin> (dev bypass on by default — see `.env.example`)
- API docs: <http://localhost:8000/_api/docs>

The dev bypass values in `.env.example` (`TURNSTILE_DEV_BYPASS=1`,
`ADMIN_DEV_BYPASS=1`, empty `DEPLOY_WEBHOOK_SECRET`) let you exercise every
endpoint without real keys. **Never set those in production.**

## Endpoints

| Path | Method | Notes |
| --- | --- | --- |
| `/` | GET | Static site (main portfolio page) |
| `/notes/` | GET | Static study cheatsheets, served as files |
| `/files/` | GET | Auto-generated HTML listing of public files |
| `/files/<name>` | GET | Stream a public file |
| `/files/secure/` | GET | 404 by design (no listing) |
| `/files/secure/<name>` | GET | Stream a protected file (requires `?t=<token>`) |
| `/resume.pdf` | GET | Cached PDF, regenerated on startup + deploy |
| `/resume-themed.pdf` | GET | Dark/branded variant |
| `/api/chat` | POST | SSE stream from OpenRouter. Turnstile-gated. 10/min/IP. |
| `/api/feedback` | POST | Turnstile-gated. 5/min/IP. |
| `/api/track` | POST | Custom event beacon. 120/min/IP. |
| `/webhook/deploy` | POST | GitHub HMAC. 10/min/IP. Pulls + restarts. |
| `/admin` | GET | Cloudflare Access gated. Combined dashboard. |
| `/admin/secure-links` | POST | Create a secure download link (returns full URL) |
| `/admin/secure-links/<id>/revoke` | POST | Revoke a link (idempotent) |
| `/admin/regenerate-resume` | POST | Manual PDF re-render. |
| `/healthz` | GET | Liveness check. |
| `/_api/docs` | GET | Swagger UI (non-standard path; hide in prod). |

## Secure download links

Drop a file in `site/files/secure/`, then from `/admin`:

1. Pick the file in the dropdown
2. Set `expires_in` (e.g. `7d`, `2h`, `30m`, `3w`, or raw seconds)
3. Set `max_uses` (0 = unlimited)
4. Click Generate — copy the URL

Recipients open the URL; the backend:
- verifies the token (exists, not expired, not exhausted, not revoked)
- logs the attempt (success or failure reason) in `secure_link_events`
- decrements `uses_remaining` if `max_uses > 0`
- streams the file with `Content-Disposition: attachment`

All failure modes return the same 404 — by design, so a leaked URL
doesn't reveal whether the file exists or just the link is bad.

## Content updates

Edit `content/resume.yaml` (or `chatbot_personality.yaml`) and push to
`main`. The deploy webhook reloads content + regenerates PDFs automatically.

If you change the schema, also update:
- `backend/content.py` validation
- `backend/templates/resume_light.html` and `resume_dark.html`
- `site/index.html` (the rich project cards there are not driven by `/content`)

## Study notes

`site/notes/` is plain HTML/CSS/JS. To add a new cheatsheet:

1. Create `site/notes/<topic>.html` (use the same template style as the existing ones)
2. Either copy `site/notes/index.html` into a subdir as a directory listing, or hand-write a subdir index
3. Cross-link from `site/notes/index.html` if it should appear on the top page

The breadcrumb pattern is `~/<b>topic</b>` linking to `/notes/`. Internal
CSS class names (`study-nav`, `study-footer`) are historical; renaming
them is safe if you do it everywhere.

## Notifications (ntfy)

The app can push to a self-hosted ntfy instance on two triggers:

- **Feedback received** — priority high, tag `speech_balloon`. Message body is
  the form contents (name, email, message). This is the only trigger that
  fires per-user-action, by design.
- **Deploy success/fail** — priority low on success (tag `white_check_mark`),
  max on any FAILED step (tag `rotating_light`). Single message per deploy
  containing the same summary line the webhook returns.

All other events (chatbot, pageviews, secure downloads, fallback) are silent.
The "don't spam my phone" rule.

Configuration (`.env`):
```
NTFY_ENABLED=1
NTFY_BASE_URL=https://ntfy.yourdomain.com
NTFY_TOPIC=lukas-portfolio
NTFY_AUTH_TOKEN=...                  # optional
```

`NTFY_ENABLED=0` (the default) makes the whole system a no-op. There is no
retry logic and no queue — if ntfy is down, the notification is dropped
with a warning in the server log. The HTTP call has a 5s timeout and never
blocks the request handler.

For a self-hosted ntfy, the topic ACL should restrict who can publish (your
server only) and who can subscribe (you). Example `server.yml`:

```yaml
auth-default-perm: deny-all
topic: {
  "lukas-portfolio": {
    publish: "Bearer <your-token>"
    read:    "Bearer <your-token>"
  }
}
```

Reference: <https://docs.ntfy.sh/publish/>

## Database

Single SQLite file at `resume.db` (gitignored). Created on first run.
Tables: `events`, `feedback`, `chat_sessions`, `chat_messages`,
`secure_links`, `secure_link_events`. Schema in `backend/db/models.py`.

To inspect:
```bash
sqlite3 resume.db
sqlite> .schema
sqlite> SELECT name, COUNT(*) FROM events WHERE kind='event' GROUP BY name;
sqlite> SELECT * FROM secure_links ORDER BY id DESC LIMIT 10;
```

## Deploy

GitHub webhook → `POST /webhook/deploy` with header
`X-Hub-Signature-256: sha256=<hmac-of-body>`. On valid signature:

1. Reload `/content` in-process
2. Regenerate resume PDFs
3. `git pull --ff-only` in the project root
4. `systemctl restart backend` (skipped if no systemd)

The whole process takes ~5–10 seconds end-to-end. Return value is a
single-line summary suitable for logging.

### systemd unit

The deploy webhook's step 4 assumes a unit called `backend` exists (the
name is configurable via `DEPLOY_SYSTEMD_UNIT`). A reference unit lives
at `deploy/backend.service`. Install it with:

```bash
sudo cp deploy/backend.service /etc/systemd/system/backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now backend.service
```

Adjust `User=`, `Group=`, `WorkingDirectory=`, and `EnvironmentFile=`
to match the server layout. The unit is deliberately written to restart
cleanly when the webhook cycles it — the two design choices that matter
are:

- **`Type=simple` + `Restart=on-failure` + `RestartSec=2s`.** uvicorn
  doesn't speak `sd_notify` natively, so `Type=notify` would need an
  extra shim for no real gain here. `on-failure` + a 2s delay gives the
  old process time to release :8000 before the new one tries to bind,
  so a second-startup `EADDRINUSE` is avoided. `StartLimitBurst=5` over
  `StartLimitIntervalSec=60s` keeps a broken unit (e.g. missing env
  var) from looping forever and filling the journal.
- **`KillMode=mixed` + `TimeoutStopSec=90s`.** `systemctl restart` sends
  SIGTERM, waits 90s, then SIGKILLs the cgroup. 90s is generous for
  in-flight SSE streams on `/api/chat`; raise it if you add slower
  endpoints. `mixed` is the same as `process` for a single-worker
  service but future-proofs the unit if you ever switch to
  `uvicorn --workers N`.

#### Why the webhook returns 200 before the new instance is up

`_run(["systemctl", "restart", "backend"])` in `backend/routes/deploy.py`
returns as soon as systemd has *scheduled* the restart — before the new
uvicorn process has bound :8000. So GitHub sees `200 · systemd:
restarted backend` while the new instance may still be starting. In
practice the 2s `RestartSec` plus uvicorn's own startup time means the
new process is up within a second or two of the response going out.

If you need a stronger guarantee — e.g. you want the deploy to fail
loudly if the new instance never comes up — change `_run` to fire
`systemctl restart` detached, then poll `systemctl is-active backend`
for a few seconds before returning. That's a code change, not a unit
change, so it's not done by default.

## What's not in this repo

- The `html/` and `study/` directories from the nginx era are gone —
  their contents have been moved into `site/`.
