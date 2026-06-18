# WealthOS — Deployment Readiness Blueprint (DEPLOYMENT.md)

_Phase 5 — Deployment Readiness. **Preparation only — do not deploy publicly yet.** This is the production deployment blueprint and operational baseline for the verified Phase 1–3 codebase (Phase 4 QA Gate passed 2026-06-18). It changes no application code or architecture._

> Audience: the operator (single-investor owner) taking WealthOS from a verified local build to a private production instance. WealthOS is a **single-user** personal wealth app; the recommendations are sized accordingly (small, cheap, durable — not horizontally scaled).

---

## 0. Current state (what we are deploying)

- **Backend:** FastAPI (async) + SQLAlchemy 2.0, run as `uvicorn app.main:app`. On startup `init_db()` runs `Base.metadata.create_all` (creates missing tables only) **and** seeds the admin + default IPS + default cash account. Alembic migrations `0001–0003` are the versioned schema source of truth. Default DB is SQLite (`./data/wealthos.db`); `DATABASE_URL` swaps to PostgreSQL with no code change (driver caveat below).
- **Frontend:** React + Vite, built to `frontend/dist`. The API client calls **relative** paths `/api/v1/...` with `credentials: "include"`. Auth is **HttpOnly cookies**, `SameSite=Lax`, `Secure` when `ENV=production`, refresh cookie scoped to `/api/v1/auth`.
- **Security middleware present:** CORS (`FRONTEND_ORIGIN`, `allow_credentials`), in-memory IP rate limiter. **Not present in code:** TrustedHost middleware, HTTPS redirect, proxy-header trust (these are handled at the platform/proxy layer — see §4).
- **Not yet built (roadmap, out of scope here):** market data/FX/prices (Phase 4 roadmap), AI (Phase 6), SMTP email (below), Dockerfiles/compose (Phase 8 — blueprinted here).

### Decisive constraints that shape the whole deployment
1. **Same-origin is mandatory.** The frontend uses relative `/api` paths and `SameSite=Lax` cookies. The browser must see the API on the **same origin** as the app — achieved with a reverse proxy / platform rewrite, **not** by calling the backend cross-domain. This avoids `SameSite=None`, third-party-cookie blocking, and CORS-credential pitfalls entirely.
2. **Single process.** The rate limiter is in-memory per-process and SQLite is single-writer. Run **one worker / one instance**. (Scaling out later ⇒ Postgres + a shared rate-limit store; roadmap.)
3. **HTTPS everywhere.** `Secure` cookies are only sent over HTTPS; the app must be served exclusively over TLS in production.

---

## 1. Deployment Architecture Report

### 1.1 Topologies considered

| Option | Frontend | Backend | DB | Same-origin via | Best for |
|---|---|---|---|---|---|
| **A — Vercel + Render (recommended)** | Vercel static | Render Web Service (1 instance) | SQLite on Render Persistent Disk (or Render Postgres) | Vercel `rewrites` `/api/*` → backend | Simplicity, owner prefers Vercel |
| **B — Single VPS + Caddy + Docker** | Static `dist` served by Caddy | uvicorn in Docker | SQLite on host volume | Caddy reverse proxy (`/` → dist, `/api` → uvicorn) | Lowest cost, full control, most robust cookies |
| **C — Vercel + Fly.io** | Vercel static | Fly Machine (1) | SQLite on Fly Volume (+Litestream) | Vercel rewrite → Fly | Global edge, cheap small VM |
| **D — Railway** | Railway static | Railway service | Volume or Railway Postgres | Railway rewrite/proxy | All-in-one PaaS |

### 1.2 Frontend hosting — **Vercel (preferred)**, fallbacks documented
- **Vercel:** deploy `frontend/dist` (build `npm run build`). Add a rewrite so the browser stays same-origin and cookies are first-party:
  ```json
  // frontend/vercel.json
  { "rewrites": [ { "source": "/api/:path*", "destination": "https://api.wealthos.example/api/:path*" } ] }
  ```
  Verify Vercel forwards `Set-Cookie` and request cookies across the rewrite (it does for proxied rewrites — confirm in the post-deploy checklist).
- **Fallbacks:** Netlify (`_redirects`/`netlify.toml` proxy), Cloudflare Pages (`_redirects` / Pages Functions proxy), or serve `dist` from the same box as the backend behind Caddy/nginx (Option B — removes the rewrite entirely).

### 1.3 Backend hosting — comparison

| Platform | TLS | Persistent disk (SQLite) | Single-instance | Proxy headers | Notes |
|---|---|---|---|---|---|
| **Render** | auto | yes (paid disk) | yes | sets `X-Forwarded-*` | Simplest; native health checks; recommended primary |
| **Fly.io** | auto | yes (volumes) | yes (1 machine) | yes | Cheapest small VM; pair with Litestream |
| **Railway** | auto | yes (volumes) | yes | yes | Easy PaaS; volumes newer |
| **VPS + Docker + Caddy** | Caddy auto | host volume | yes | Caddy sets headers | Full control; you own patching/backups |

### 1.4 Recommended primary architecture

```
                    ┌─────────────────────────────────────────────┐
   Browser  ──TLS──►│  Vercel (app.wealthos.example)              │
   (cookies         │   • serves React dist (static)              │
    first-party)    │   • rewrite /api/* ──TLS──►                 │
                    └───────────────────────────┬─────────────────┘
                                                 ▼
                    ┌─────────────────────────────────────────────┐
                    │  Render Web Service (api.wealthos.example)   │
                    │   • uvicorn app.main:app  (1 worker)         │
                    │   • --proxy-headers (real client IP)         │
                    │   • RateLimit + CORS + JWT/cookie auth       │
                    │   • Persistent Disk: /data/wealthos.db ◄─────┼── nightly backup / Litestream
                    └─────────────────────────────────────────────┘
```

**Why:** matches the owner's Vercel preference; the rewrite gives same-origin cookies with zero code change; one Render instance satisfies the single-process constraint; a persistent disk keeps SQLite durable; `/api/v1/health` drives platform health checks. Start on **SQLite** (single-user, low write volume, ledger-first); migrate to Postgres only if/when multi-user or scaling is needed.

### 1.5 FREE-TIER / DEMO mode (current active config) — **testing & UI validation only**

The committed `render.yaml` is set to **free/ephemeral** mode for demo and UI validation. **No application or financial-logic code changes are required** for this — the backend already self-heals on a missing DB.

**What free tier means here:**
- **No persistent disk.** Render free has an ephemeral filesystem and **spins down after ~15 min idle**; the filesystem resets on spin-down/redeploy. SQLite lives at `/tmp/wealthos.db` and is treated as throwaway.
- **Auto-initialization (verified):** on every cold boot, `init_db()` runs `Base.metadata.create_all` (builds the latest schema directly from the models — Alembic not needed for a fresh ephemeral DB) **and idempotently re-seeds** the admin + default IPS + default cash account from env. Confirmed locally against a fresh, non-existent nested path: dir auto-created, schema built, rows seeded. `session.py` already `mkdir -p`s the SQLite parent dir.
- **Stateless-safe APIs:** all endpoints return valid empty-state data on a fresh DB (e.g. valuation `nav_usd = 0`, net worth `0`) — confirmed in the Phase 4 live E2E (49/49 on a fresh DB). No endpoint assumes pre-existing data.
- **What persists vs. what doesn't:** Render **env vars persist** across restarts, so `SECRET_KEY` stays stable (JWTs/logins survive restarts) and the admin is always available. **Database rows do NOT persist** — anything entered in a session is gone after a spin-down/redeploy. ~30–60 s cold-start on the first request after idle.

**Free-tier config deltas vs. the durable (paid) recommendation:**
| Setting | Free/demo (active) | Durable (paid) |
|---|---|---|
| `plan` | `free` | `starter` |
| `disk:` | omitted | 1 GB at `/data` |
| `DATABASE_URL` | `sqlite+aiosqlite:////tmp/wealthos.db` | `sqlite+aiosqlite:////data/wealthos.db` |
| schema | `create_all` on boot | `preDeployCommand: alembic upgrade head` |
| data durability | **ephemeral** | persistent |

**Use it for:** verifying the deployed UI/UX, auth flow, routing, mobile layout, and that all APIs respond in a real hosted environment. **Do not** enter data you need to keep. **Upgrade to durable** later by flipping the table above (the `render.yaml` comments mark exactly what to change). `ENV=production` is kept even on free tier so Secure cookies stay on (both Vercel and Render free are HTTPS); `SECRET_KEY` + `ADMIN_*` must still be set in the dashboard.

---

## 2. Environment Matrix (dev / staging / production)

| Aspect | Development | Staging | Production |
|---|---|---|---|
| `ENV` | `development` | `production` (parity) | `production` |
| Cookies `Secure` | off (http localhost) | **on** (HTTPS) | **on** (HTTPS) |
| `SECRET_KEY` | placeholder OK | unique strong | unique strong (validated) |
| DB | SQLite file | SQLite on volume / Postgres | SQLite on volume / Postgres |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | `https://staging…` | `https://app…` |
| Rate limiting | on (tests disable) | on | on |
| Admin seed | dev defaults allowed | required | required |
| Source maps | n/a | off (build default) | off |
| Logs | DEBUG/INFO to stdout | INFO | INFO/WARNING |

Staging deliberately runs `ENV=production` so Secure-cookie behaviour and the strict-secret check are exercised before launch. Templates: [.env.example](../.env.example), [.env.staging.example](../.env.staging.example), [.env.production.example](../.env.production.example).

---

## 3. Environment Audit

### 3.1 Variable reference (source: `backend/app/core/config.py`)

| Variable | Required | Default | Secret | Notes |
|---|---|---|---|---|
| `ENV` | rec. | `development` | no | `production` ⇒ Secure cookies + secret validation |
| `SECRET_KEY` | **prod** | placeholder | **YES** | 64+ random; placeholder rejected when prod; rotation logs everyone out |
| `DATABASE_URL` | rec. | `sqlite+aiosqlite:///./data/wealthos.db` | maybe | Postgres ⇒ `postgresql+asyncpg://…` (**add `asyncpg`**) |
| `FRONTEND_ORIGIN` | rec. | `http://localhost:5173` | no | CORS allow-list (exact origin) |
| `ADMIN_EMAIL` | **prod** | — | no | first-run seed only |
| `ADMIN_USERNAME` | **prod** | — | no | first-run seed only |
| `ADMIN_PASSWORD` | **prod** | — | **YES** | first-run seed; change after first login |
| `ACCESS_TOKEN_MINUTES` | no | `30` | no | JWT lifetime (post-logout validity window) |
| `REFRESH_DAYS_REMEMBER` | no | `30` | no | remember-me refresh lifetime |
| `REFRESH_DAYS_DEFAULT` | no | `1` | no | default refresh lifetime |
| `RATE_LIMIT_ENABLED` | no | `true` | no | keep `true` in prod |
| `LOG_LEVEL` | no | `INFO` | no | DEBUG/INFO/WARNING/ERROR |
| `OPENAI_API_KEY` | no | `""` | **YES** | unused until Phase 6 — leave blank |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | no | unused until Phase 6 |

**Frontend:** no runtime secrets. With the same-origin rewrite there is no API-URL variable; the app uses relative `/api/v1`. (If a cross-origin build were ever needed it would require a code change to introduce `VITE_API_BASE` — not the current design.)

### 3.2 Secret-leak check (performed 2026-06-18)
- `.gitignore` ignores `.env`, `data/`, `*.db`, `node_modules`, `frontend/dist`, `graphify-out/`, `.claude/settings.local.json`. ✓
- `git ls-files` shows **no** committed `.env` (only `.env.example` + the new `.env.*.example` templates, all placeholders) and **no** keys/certs. ✓
- The only "secret-shaped" value in the repo is the SMTP/JWT discussion in docs and placeholder examples. ✓
- **Action:** keep all real secrets in the platform secret store; never `git add` a populated `.env`.

---

## 4. Security Hardening Report (production settings)

| Control | Status in code | Required production action |
|---|---|---|
| **Cookies** | HttpOnly ✓, `SameSite=Lax` ✓, `Secure` when `ENV=production` ✓, refresh scoped to `/api/v1/auth` ✓ | Set `ENV=production`; serve only over HTTPS so Secure cookies transmit |
| **HTTPS** | not enforced in app | Terminate TLS at platform/proxy; force HTTP→HTTPS redirect (Vercel/Render/Caddy do this); enable HSTS at the edge |
| **CORS** | exact origin + credentials (no wildcard) ✓ | Set `FRONTEND_ORIGIN` to the real HTTPS origin. With same-origin rewrite, CORS is not exercised but keep it correct |
| **Trusted hosts** | no `TrustedHostMiddleware` | Restrict Host at the platform/proxy (route only the real domain to the app). Optional code hardening deferred to roadmap Phase 8 |
| **Proxy headers** | uvicorn flag, not code | Run `uvicorn … --proxy-headers --forwarded-allow-ips="<proxy CIDR or *>"` so `request.client.host` = real client IP — **required** for IP rate limiting to work behind a proxy |
| **SECRET_KEY** | placeholder rejected in prod ✓ | Provide a unique 64+ char random value via secret store |
| **Rate limiting** | in-memory, IP-keyed, login+reset+global ✓ | Keep enabled; run a single instance; revisit shared store before scaling |
| **Admin bootstrap** | seeded from env, prod-required ✓ | Set strong `ADMIN_*`; change password after first login |
| **Password storage** | bcrypt cost 12 ✓ | none |
| **JWT** | HS256, typed, `require sub/exp/iat` ✓ | shorter `ACCESS_TOKEN_MINUTES` (e.g. 15) tightens the post-logout window |

**Residual (accepted) risks** — none blocking for a single-user private instance; track for hardening:
- Access JWT remains valid until expiry after logout (stateless-JWT tradeoff) — mitigate with a short `ACCESS_TOKEN_MINUTES`.
- No refresh-reuse "session-family" revocation (reused rotated tokens are still rejected).
- In-memory limiter retains one empty bucket per distinct client IP for the process lifetime (negligible at this scale).

---

## 5. SMTP / Password-Reset Readiness Report

**Current behaviour:** `POST /auth/password-reset/request` issues a single-use, 15-minute, SHA-256-hashed token and **logs the raw token server-side** (no email is sent). `POST /auth/password-reset/confirm` consumes it (single-use, verified in QA) and revokes all live sessions. So reset **works end-to-end today** — delivery is the only missing piece.

**For a single-user owner instance, email is optional:** the owner can (a) read the token from logs, or (b) reset via the CLI utility `scripts/create_admin.py` (supports overwrite of an existing admin's password). Self-service email reset only becomes necessary if/when multi-user is introduced.

**Provider-agnostic design (when email delivery is implemented — roadmap, not this phase):**
- Add a thin `EmailSender` interface with one **SMTP** implementation (works with *any* provider that speaks SMTP — no lock-in): Resend, Postmark, SendGrid, Mailgun, AWS SES, Fastmail, or a self-hosted relay all expose SMTP.
- Reserved env vars (already documented in `.env.production.example`): `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS / 465 TLS), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`.
- Wire `request_password_reset` to call `EmailSender.send(...)` instead of (or in addition to) the log line; keep the log path for dev. **No change required for this deployment.**

---

## 6. Docker & Operations Review

### 6.1 Is Docker required?
- **Render / Railway:** not required (native Python build from `requirements.txt`). Docker optional for parity.
- **Fly.io / VPS:** Docker recommended for reproducibility.

**Recommendation:** keep a single, small backend image for portability. Example (blueprint — actual Dockerfiles are roadmap Phase 8 deliverables; do not need to be committed for this phase):
```dockerfile
# backend/Dockerfile (blueprint)
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# Migrate then serve; single worker (in-memory rate limit + SQLite single-writer).
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --workers 1 --proxy-headers --forwarded-allow-ips="*"
```
Single-origin VPS alternative (Caddy auto-HTTPS, no CORS/rewrite needed):
```
# Caddyfile (blueprint)
app.wealthos.example {
    handle /api/* { reverse_proxy 127.0.0.1:8000 }
    handle { root * /srv/wealthos/dist; try_files {path} /index.html; file_server }
}
```

### 6.2 Database persistence
- **SQLite MUST live on a persistent volume** (Render Disk / Fly Volume / VPS disk). On ephemeral PaaS filesystems the DB is wiped on every redeploy — **data loss**. Mount the volume and point `DATABASE_URL` at the absolute path (e.g. `sqlite+aiosqlite:////data/wealthos.db`).
- **PostgreSQL alternative:** set `DATABASE_URL=postgresql+asyncpg://…` and **add `asyncpg>=0.29` to `backend/requirements.txt`** (currently only `aiosqlite` is present). Migrations and app code work unchanged.

### 6.3 Backup strategy
- **SQLite (recommended): Litestream** — continuous streaming replication of the `.db` file to S3/Backblaze/any object store; near-zero RPO, trivial restore. Alternative: scheduled `sqlite3 wealthos.db ".backup '/backups/wealthos-$(date).db'"` (consistent online backup) + offsite copy + retention.
- **PostgreSQL:** rely on the managed provider's automated backups / PITR; verify restore at least once.
- **Always test a restore** before relying on any backup.

### 6.4 Migrations on deploy
- Run **`alembic upgrade head`** as a release/predeploy step (it stamps `alembic_version`). App-startup `create_all` is then a no-op on existing tables and only seeds. On a brand-new DB, run `alembic upgrade head` **before first boot** so versioning is stamped (avoids create_all vs. Alembic divergence).

---

## 7. Operations & Monitoring Plan (lightweight)

| Concern | Plan |
|---|---|
| **Application logs** | App emits single-line **structured JSON** to stdout (`StructuredFormatter`). Use the platform's log stream (Render/Fly/Railway). No extra agent needed. |
| **Auth logs** | Every auth event is written to the `audit_logs` table (LOGIN / LOGIN_FAILED / REFRESH / LOGOUT / PASSWORD_RESET_*) **and** logged. Review via DB query or log search; failed logins + rate-limit hits are WARNING level. |
| **Error tracking** | Optional, deferred: add Sentry (`sentry-sdk`) later for exception aggregation. For launch, platform logs + structured records suffice (keep it lightweight). |
| **Uptime** | External monitor (UptimeRobot / BetterStack / Healthchecks.io) polling **`GET /api/v1/health`** (real DB round-trip) every 1–5 min; alert on non-200. Also use it as the platform health check. |
| **Resource/cost** | Single small instance + small disk; watch disk usage (SQLite + backups) and memory (limiter map). |

---

## 8. Deployment Runbook

### 8.1 Prerequisites
- Domain + DNS for `app.…` (frontend) and `api.…` (backend), or one domain with a `/api` proxy.
- Generated secrets: `SECRET_KEY` (64+ random), strong `ADMIN_PASSWORD`.
- Decided DB: SQLite-on-volume (default) or Postgres (`asyncpg` added).

### 8.2 Deploy sequence
1. **Provision backend** (Render/Fly/VPS): create the service + **attach a persistent disk** (mount `/data`).
2. **Set backend env** from `.env.production.example` via the secret store (`ENV=production`, `SECRET_KEY`, `DATABASE_URL` → volume path, `FRONTEND_ORIGIN`, `ADMIN_*`).
3. **Schema:** run `alembic upgrade head` (release/predeploy command).
4. **Start backend:** `uvicorn app.main:app --workers 1 --proxy-headers --forwarded-allow-ips="<proxy>"`. First boot seeds the admin.
5. **Build frontend:** `cd frontend && npm ci && npm run build`.
6. **Deploy frontend** to Vercel; add the `/api/*` **rewrite** to the backend origin. Enable HTTPS + HSTS.
7. **DNS/TLS:** point domains, confirm valid certificates, force HTTPS.
8. Run the **post-deploy validation checklist** (§8.4).

### 8.3 Rollback procedure
- **Frontend:** Vercel → instantly promote the previous deployment (one click / `vercel rollback`).
- **Backend:** redeploy the previous image/commit. Keep migrations **backward-compatible** within a release; if a migration must be undone, use its Alembic `downgrade` (verified up/down for 0001–0003) **before** rolling back code.
- **Database:** SQLite → stop app, restore the latest Litestream/backup snapshot to the volume, restart. Postgres → provider PITR/restore.
- **Secrets:** rotating `SECRET_KEY` forces global re-login (acceptable mitigation if a token leak is suspected).

### 8.4 Post-deploy validation checklist (run against the live URL)
- [ ] `GET /api/v1/health` → `200` (DB round-trip OK).
- [ ] App loads over **HTTPS**; HTTP redirects to HTTPS; no mixed-content warnings.
- [ ] Login works; response sets `wos_access` + `wos_refresh` with **`Secure; HttpOnly; SameSite=Lax`**.
- [ ] `GET /api/v1/auth/me` succeeds **via cookie** (no bearer header) → confirms same-origin cookie flow.
- [ ] Refresh rotates (old refresh rejected); logout clears cookies and a subsequent `/me` is `401`.
- [ ] Rate limit: rapid bad logins eventually return `429` with `Retry-After` (confirms proxy passes real client IP).
- [ ] Dashboard shows **Action Status first**; net worth / cash / portfolio / execution / settings load.
- [ ] Mobile at **390px**: no horizontal page scroll; Portfolio holdings reachable (contained scroll).
- [ ] Admin password changed from the seed value; `audit_logs` shows the LOGIN event.
- [ ] Backup job runs and a **test restore** succeeds.

> Tip: the in-process harness `backend/qa_e2e_phase4.py` mirrors most of these checks; a curl-based variant can be run against the live host for a real-network smoke test.

---

## 9. READY FOR PUBLIC DEPLOYMENT — **NO (by design; this phase is preparation only)**

The **deployment blueprint and operational baseline are READY.** Public launch is intentionally deferred per the owner directive and is gated on executing this runbook plus these **go-live preconditions**:
1. Provision backend with a **persistent volume** (or Postgres + `asyncpg`) and run `alembic upgrade head`.
2. Set `ENV=production`, a strong unique `SECRET_KEY`, and `ADMIN_*` via the secret store; serve over **HTTPS** only.
3. Run uvicorn with **`--proxy-headers`** (real client IP) and a **single worker**.
4. Configure the frontend **same-origin `/api` rewrite** (or Caddy single-origin) and verify cookie round-trip.
5. Enable **backups** (Litestream/managed) and an **uptime monitor** on `/health`.
6. Complete the **post-deploy validation checklist** (§8.4).

Optional before/at launch (not blockers for single-user): SMTP email delivery for password reset; shorter `ACCESS_TOKEN_MINUTES`; Sentry; `TrustedHostMiddleware` (roadmap Phase 8). Actual Dockerfiles/compose and user/maintenance guides are roadmap **Phase 8 — Hardening & Delivery** deliverables.
