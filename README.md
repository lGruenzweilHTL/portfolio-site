# Portfolio Site + Backend

Lukas Grünzweil's portfolio site, served by a single FastAPI process that
also runs the recruiter chatbot, analytics, feedback, study notes, file
downloads, and admin dashboard.

## What runs where

- **Main site:** `/` — hand-written HTML/CSS/JS, served as static files
- **Services directory:** `/services` — server-rendered catalog of public and home-network services
- **Study notes:** `/notes/` — `site/notes/`, hand-written cheatsheets for SYP/Java/NSCS/PL/SQL
- **Public files:** `/files/` — auto-generated listing of `site/files/`
- **Secure files:** `/files/secure/` — token-gated downloads of `site/files/secure/` (no listing; access only via signed URL generated from `/admin`)
- **Chatbot, analytics, feedback, deploy, admin:** all under `/api/*`, `/webhook/*`, `/admin`

The previous nginx setup is fully replaced. Cloudflare Tunnel remains
the only ingress; the tunnel's `config.yml` just repointed from
`localhost:80` (nginx) to `localhost:8000` (uvicorn).

## Layout

```
/site                      Static site + notes (served as /, /notes/)
  index.html               Main portfolio page (with chatbot + feedback wired in)
  chat.js                  Floating-button chatbot UI
  notes/                   Study cheatsheets (moved from /study)
  files/                   Public downloads — listed by routes/files.py, not the static mount
  files/secure/            Protected downloads (token-gated, no listing)
  img/, favicon.svg
/backend                   FastAPI app
  main.py                  App factory, middlewares, static mount, error handlers
  config.py                Pydantic settings (env / .env)
  content.py               /content loader + system-prompt renderer
  chat.py                  OpenRouter streaming + fallback policy
  turnstile.py             siteverify helper with dev bypass
  resume.py                WeasyPrint PDF render (mtime-based cache invalidation)
  routes/                  One file per route group, including /services
  db/models.py             SQLite schema + connection helpers
  templates/               resume.html (designed) + resume_ats.html (ATS), assets/,
                           admin.html, error.html, services.html
/content                   Single source of truth (YAML)
  resume.yaml              Personal, projects, skills, beyond_code
  chatbot_personality.yaml Persona + system prompt template
  services.yaml            Public/home-network service catalog
/deploy                    systemd units + tmpfiles.d drop-in for the deploy webhook
/scripts                   One-off dev tooling (devicon subsetting, résumé preview render)
/static/generated          Cached resume PDFs (gitignored, regenerated on deploy)
.env.example               Copy to .env; all required env vars documented
requirements.txt           Pinned Python dependencies
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
- Services: <http://localhost:8000/services>
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
| `/services` | GET | Server-rendered public/home-network service directory |
| `/services/` | GET | Same service directory with a trailing slash |
| `/notes/` | GET | Static study cheatsheets, served as files |
| `/notes/<slug>` | GET | Clean URL for a cheatsheet — serves the matching `.html` file |
| `/notes/_listing`, `/notes/<sub>/_listing` | GET | JSON index of a notes directory, consumed by `cheatsheet.js` |
| `/notes/notes.css`, `/notes/cheatsheet.js` | GET | Notes styling and index behaviour, served from `site/notes/` |
| `/files/` | GET | Auto-generated HTML listing of public files |
| `/files/<name>` | GET | Stream a public file |
| `/files/secure/` | GET | 404 by design (no listing) |
| `/files/secure/<name>` | GET | Stream a protected file (requires `?t=<token>`) |
| `/resume.pdf` | GET | Cached PDF, two-column styled version. Regenerated on startup + deploy |
| `/resume-ats.pdf` | GET | Single-column ATS-friendly version |
| `/resume-themed.pdf` | GET | 301 → `/resume.pdf`. Retired dark variant, kept for old bookmarks |
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

## Caching

Every asset on this site is replaced in place by a deploy — nothing is
fingerprinted or content-addressed, so there is no safe `immutable` to
send. Anything cached without revalidation is a bug waiting to happen.

`StaticAssetCacheMiddleware` (in `main.py`) therefore sets
`Cache-Control: no-cache` on HTML, CSS, JS, JSON, and SVG. Starlette
sends `ETag` + `Last-Modified` but no `Cache-Control` at all, so without
this the browser falls back to *heuristic* freshness — roughly 10% of the
file's age — and a stylesheet can sit in the disk cache long after a
deploy replaced it.

`no-cache` doesn't forbid storing the response, it requires a conditional
request before reusing it, and the ETag comparison answers that with a
bodiless 304. Two places set something stricter and are left alone
(`setdefault`, not assignment):

- `/` — `no-store`, because the Turnstile sitekey is injected per response
- `/services` — `no-store`, because it's rendered from the content bundle

Images and fonts get no header at all; browsers cache those for a
session anyway. `/resume.pdf` and `/resume-ats.pdf` are `no-cache` for
the same reason as the CSS: the deploy webhook re-renders them in place,
so an hour-long `max-age` would show a résumé that contradicts the rest
of the site.

If you ever add fingerprinted assets (`styles.abc123.css`), give those
`max-age=31536000, immutable` instead and take them out of the
middleware's extension list.

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

Edit `content/resume.yaml`, `content/chatbot_personality.yaml`, or
`content/services.yaml` and push to `main`. The deploy webhook pulls,
reloads the content bundle, and re-renders the résumé — in that order, so
what you get reflects the commit you just pushed.

The PDFs are also self-healing on startup: `ensure_resume_pdfs()` renders
when the files are missing **or** when any template, template asset, or
`content/resume.yaml` is newer than the PDFs. A `git pull` gives the files
it checks out the current mtime, so an edit to the résumé invalidates the
cache without anyone having to remember to clear it.

The services catalog uses ordered categories. A category can own multiple URLs
and/or nested services, and each nested service can own multiple URLs:

```yaml
categories:
  - id: portfolio
    name: Portfolio
    visibility: public
    urls:
      - label: Main site
        url: https://gruenzweil.cc
      - label: Admin
        url: https://gruenzweil.cc/admin
  - id: media
    name: Media
    visibility: internal
    services:
      - id: jellyfin
        name: Jellyfin
        visibility: public
        urls:
          - label: Open Jellyfin
            url: https://media.gruenzweil.cc
      - id: media-automation
        name: Automation and downloads
        visibility: internal
        urls:
          - label: Sonarr
            url: http://sonarr.home.arpa:8989
          - label: Radarr
            url: http://radarr.home.arpa:7878
```

`visibility` is `public` or `internal`; URL entries inherit their parent value
unless they provide their own visibility. Links must be absolute `http://` or
`https://` URLs with a host and no embedded credentials. Internal links are
shown publicly with a **Home network only** badge, but open directly at their
configured URL: the page does not proxy, health-check, or enforce network
access. Keep secrets out of the file.

If you change the schema, also update:
- `backend/content.py` validation
- `backend/templates/resume.html` and `resume_ats.html`
- `backend/templates/services.html` when changing the service page markup
- `site/index.html` (the rich project cards there are not driven by `/content`)

## Study notes

`site/notes/` is plain HTML/CSS/JS. To add a new cheatsheet:

1. Create `site/notes/<topic>.html` (use the same template style as the existing ones)
2. For a new subdirectory, copy `site/notes/index.html` into it as
   `index.html` — that's the generic directory index, and it works at any
   depth with no per-directory edits. It's committed as three identical
   regular files (root, `Java/`, `SYP/`), **not** a symlink, so keep the
   copies byte-identical or listings will drift apart.
3. Cross-link from `site/notes/index.html` if it should appear on the top page

The index's header comment still describes the nginx `autoindex` setup it
was written for. That config is gone — `backend/routes/notes.py` now
serves `/notes/_listing` and `/notes/<sub>/_listing` as JSON, and
`cheatsheet.js` fetches those. Treat the comment as historical; the
routes are the source of truth.

`site/notes/study.css` is served from the fixed path `/notes/notes.css`
(and `cheatsheet.js` from `/notes/cheatsheet.js`) rather than relatively,
because the same file is reachable at several depths. Don't switch those
to relative `./` links.

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

1. `git pull --ff-only` in the project root
2. Reload `/content` in-process
3. Regenerate resume PDFs
4. Create the restart trigger at `/run/backend-restart/trigger` (skipped
   if there's no systemd, or if the pull failed)

A separate systemd `.path` unit watches for the trigger and restarts
`backend.service` *as root*. The whole process takes ~5–10 seconds
end-to-end. Return value is a single-line summary suitable for logging.

**The pull comes first on purpose.** Steps 2 and 3 read from the working
tree, so running them earlier reloads and re-renders the *pre-pull*
content and templates — which leaves the résumé one deploy behind,
since nothing re-renders it later (`ensure_resume_pdfs()` only filled
in *missing* files, and it now compares mtimes against the templates
and `content/resume.yaml` instead).

### Why a trigger file instead of `systemctl restart` from the webhook

The app process runs as `www-data` (or whatever user the systemd unit
declares), which has no privilege to call `systemctl restart` — polkit
rejects non-root callers, and the unit's `ProtectSystem=strict` sandbox
makes sending a signal to `$MAINPID` from outside unreliable across
systemd versions. The supported path is a `.path` unit that watches a
file `www-data` is allowed to write to, and a watcher service that
runs the restart as root.

Two details make or break it:

- **The trigger lives in a group-writable *directory*, and is not
  pre-created.** `/run` is root-owned `0755`, so the app cannot create
  a file there — tmpfiles.d creates `/run/backend-restart` as
  `root:www-data 0770` instead, and the app creates `trigger` inside
  it. Each deploy therefore produces a real "file appeared" edge, and
  the watcher deletes the trigger in `ExecStartPre=` so the next deploy
  re-arms the condition. A pre-created trigger file cannot work: it
  fires once at boot before any deploy, and once consumed it can never
  be recreated.
- **`backend-restart.service` must not set `RemainAfterExit=yes`.** It
  reads like a cosmetic choice for `systemctl status`, but it makes the
  oneshot latch in `active (exited)` permanently — and a `.path` unit's
  start request against an already-active unit is a no-op. With it
  set, the mechanism fires exactly once and then silently does nothing
  forever.

### systemd units

Three units + one tmpfiles snippet work together. Reference files live in `deploy/`:

| File | Role |
|------|------|
| `backend.service` | The uvicorn process. Configurable name (`DEPLOY_SYSTEMD_UNIT`). |
| `backend-restart.path` | Watches `/run/backend-restart/trigger`, triggers the service below. |
| `backend-restart.service` | Removes the trigger, then `systemctl restart backend`. No `RemainAfterExit` — see above. |
| `backend-restart.conf` | tmpfiles.d drop-in: creates `/run/backend-restart/` on every boot, `root:www-data`, mode 0770. Required — without it the app can't create the trigger at all. |

Install and enable all four (order matters — the `.path` unit only
watches once both it and the service it points at are present):

```bash
# 1. Copy the unit files
sudo cp deploy/backend.service            /etc/systemd/system/backend.service
sudo cp deploy/backend-restart.path       /etc/systemd/system/backend-restart.path
sudo cp deploy/backend-restart.service    /etc/systemd/system/backend-restart.service
# 2. Install the tmpfiles drop-in so the trigger directory is recreated on
#    every boot with the right ownership (root:www-data, 0770). Adjust
#    `www-data` to match the User= in backend.service if it differs.
sudo cp deploy/backend-restart.conf       /etc/tmpfiles.d/backend-restart.conf
sudo systemd-tmpfiles --create
# 3. Reload + enable
sudo systemctl daemon-reload
sudo systemctl enable --now backend.service backend-restart.path
# 4. Clean up the old pre-created flag file, if one is lying around
sudo rm -f /run/backend.restart
```

If you skip the tmpfiles drop-in, every deploy fails with
`PermissionError` on the trigger. The drop-in is the durable fix.

Verify it without waiting for a push:

```bash
sudo touch /run/backend-restart/trigger
systemctl status backend-restart.service   # expect: ran, then inactive (dead)
systemctl status backend.service           # expect: active (running), new PID
```

`inactive (dead)` is the correct end state, and it's the whole point. A
oneshot with `RemainAfterExit=yes` would show `active (exited)` instead —
and that latched state is exactly what stops the next deploy from
retriggering.

### Upgrading an existing install

If the units are already installed and enabled, `systemctl enable --now`
is **not** enough. Both units are already active, so the start is a
no-op, and `daemon-reload` doesn't re-activate anything:

- `backend-restart.path` set up its inotify watch against the *old*
  `PathExists=` at activation. Re-reading the file doesn't move the watch.
- `backend-restart.service` is latched `active (exited)` from the old
  `RemainAfterExit=yes`, and `daemon-reload` doesn't clear that state.

Pushing the new code before running any of this is safe — the old
process keeps running the old code, and the worst outcome is a
`systemd: FAILED` line in the webhook response (the handler can't create
the trigger directory that isn't installed yet). Static files, content,
and the résumé still update, so there's no downtime; only Python code
changes fail to activate.

To migrate, use explicit restarts instead of `--now`:

```bash
sudo cp deploy/backend.service            /etc/systemd/system/backend.service
sudo cp deploy/backend-restart.path       /etc/systemd/system/backend-restart.path
sudo cp deploy/backend-restart.service    /etc/systemd/system/backend-restart.service
sudo cp deploy/backend-restart.conf       /etc/tmpfiles.d/backend-restart.conf

sudo systemd-tmpfiles --create            # creates /run/backend-restart/ 0770
sudo rm -f /run/backend.restart           # old pre-created flag, now unused
sudo systemctl daemon-reload

# Both units are already active, so they need an explicit restart.
# Restarting the oneshot also restarts backend, which loads the new code.
sudo systemctl restart backend-restart.service
sudo systemctl restart backend-restart.path
sudo systemctl enable backend.service backend-restart.path
```

`daemon-reload` must come first, or the restart re-runs the *old*
definitions. The `restart backend-restart.service` line is the
load-bearing one: it clears the latched state and restarts `backend`, so
the new code is live immediately instead of at the next deploy.

Note that `systemd-tmpfiles --create` only creates what the current
config describes; it won't remove the old `/run/backend.restart` file,
which is why the `rm` is there.

`backend.service` is deliberately written to restart cleanly when the
watcher cycles it — the two design choices that matter are:

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

The handler in `backend/routes/deploy.py` returns as soon as the
trigger file is created — before the `.path` unit has fired, before
`systemctl restart` has scheduled the stop, and before the new uvicorn
process has bound :8000. So GitHub sees `200 · systemd: restart flag
set (/run/backend-restart/trigger)` while the new instance may still be
starting. In practice the 2s `RestartSec` plus uvicorn's own startup
time means the new process is up within a second or two of the
response going out.

The response body is also the *only* place a failing step shows up: the
handler returns 200 even when `git pull` fails or the trigger can't be
written, so GitHub shows a green delivery either way. Check **Settings
→ Webhooks → Recent Deliveries** for the body, or watch for the `FAILED`
marker — it also flips the ntfy notification to a failure.

If you need a stronger guarantee — e.g. you want the deploy to fail
loudly if the new instance never comes up — change the deploy handler
to poll `systemctl is-active backend` (or to wait on a
`/run/backend-restart/done` sibling file written by the watcher
service's `ExecStartPost=`) for a few seconds before returning. That's a
code change, not a unit change, so it's not done by default.

## What's not in this repo

- The `html/` and `study/` directories from the nginx era are gone —
  their contents have been moved into `site/`.
