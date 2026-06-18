# WealthOS — Project Handoff

_Last updated: 2026-06-18 · Wealth Execution Operating System for a Malaysian long-term investor (Moomoo MY, 70% VOO / 30% QQQ, 6-month rebalance, RM1.5–3k/mo)._

## Status at a glance
- **Phase 1 — Backend Core: COMPLETE & verified.**
- **Phase 2 — Wealth Execution Domain: COMPLETE & verified.**
- **Phase 3 — Behavioral Interface Layer: COMPLETE & VERIFIED (gate 2026-06-18 — PASS).**
- **Phase 4 QA Gate (Release Readiness Gate): COMPLETE 2026-06-18 — PASS.** Live E2E 49/49, integration/financial/auth/mobile all verified; one responsiveness fix applied. _This is a gate, not a roadmap phase — roadmap NOT renumbered._
- **Phase 5 — Deployment Readiness (preparation only; NOT deployed): COMPLETE 2026-06-18.** Blueprint `docs/DEPLOYMENT.md` + env templates written. **READY FOR PUBLIC DEPLOYMENT = NO by design** (prep only); go-live gated on runbook preconditions.
- **Phase 6 — Go-live configs: present; FREE-TIER / DEMO mode active (2026-06-18).** `render.yaml` set to free/ephemeral (no disk; SQLite at `/tmp`, recreated + reseeded on every boot), `frontend/vercel.json`, `backend/Dockerfile`, `backend/smoke_prod.py`. **No app/financial code changed** — backend already self-initializes on a missing DB (verified). Free tier = **testing/UI-demo only; DB rows are NOT durable** (reset on spin-down/redeploy); durability is a later paid switch (see DEPLOYMENT.md §1.5). Not yet deployed (needs owner's Render/Vercel accounts).
- Real pages: Login, Dashboard, Execution Center, Portfolio, Cash Buffer, Settings. Transactions + Net Worth are intentional placeholders; `*` → NotFound placeholder.
- Tests: **181 passing, 89% coverage** + **49/49 live in-process E2E** (`backend/qa_e2e_phase4.py`). Frontend `tsc --noEmit` clean; `vite build` green.

## Integration & QA Hardening Pass (2026-06-18) — PASS
| Area | Result |
|------|--------|
| Live E2E (in-process ASGI + cookie jar) | **49/49** — login→cookie `/me`→page reads→cash mutation→refresh rotation (old 401)→logout (me/refresh 401)→reset single-use→rate-limit 429 |
| Integration | Mutations invalidate correct roots (cash→networth/deployment/action-status/cycle); loading/error/empty states covered; logout clears whole cache; refresh-on-401 verified |
| Financial correctness | No client-side derivation; drift display == backend `drift.py`; cash/deployable/net-worth arithmetic correct live |
| Auth security | refresh rotation/reuse, logout revocation, reset single-use + session revoke, no session fixation, rate-limit — all verified |
| Mobile | grids mobile-safe; **fixed** Portfolio `HoldingsTable` clipping (contained scroll + min-w; page still no h-scroll) |

**Bugs fixed:** Portfolio `HoldingsTable` mobile readability ([Portfolio.tsx:264](frontend/src/pages/Portfolio.tsx#L264) — `overflow-hidden`→`overflow-x-auto` + `min-w-[640px]`). _(Also fixed a QA-harness logger-capture bug; not app code.)_
**Remaining risks (Phase-5 deploy-config / defense-in-depth — not baseline blockers):** (1) prod reverse proxy must pass real client IP (uvicorn `--proxy-headers` + trusted hosts) or IP rate-limit buckets collapse; (2) SMTP delivery for password-reset tokens (currently server-log only); (3) no refresh-reuse session-family revocation; (4) access JWT valid until exp (≤30m) after logout (stateless-JWT tradeoff); (5) in-memory limiter is process-local (Redis roadmap) and retains one empty bucket per distinct IP; (6) live browser-pixel E2E not run (recommend manual 390px check pre-deploy).

## Phase 3 Verification Gate (2026-06-18) — PASS
| # | Check | Result |
|---|-------|--------|
| 1 | TypeScript `tsc --noEmit` | **PASS** — 0 errors |
| 2 | Frontend `vite build` | **PASS** — 1994 modules, per-route code-split, 81.6 kB gzip main bundle |
| 3 | Backend `pytest` (full) | **PASS** — 181 passed, 89% coverage |
| 4 | Auth (hardening / refresh rotation / session persistence) | **PASS** — 25/25: refresh rotates & old token rejected; cookie session persists (me-via-cookie); remember-me TTL; logout revokes; reset flow; rate-limit lockout; bearer fallback |
| 5 | Integration (connectivity / API typing) | **PASS** — app imports + 44 routes register; `endpoints.ts` maps 1:1 (44↔44) to backend; `tsc` validates `types/api.ts`; backend tests hit real HTTP via httpx ASGI. _(Live browser E2E not run.)_ |
| 6 | Mobile (responsive / no h-scroll / readability) | **PASS w/ 1 minor finding** — global `overflow-x-hidden`+`max-w-100vw`, AppShell width-capped, BottomNav safe-area, Dashboard Action-Status-first & single-col, ExecutionCenter cards-on-mobile, no forbidden surfaces |

**Minor finding (non-blocking, tracked for Phase 4 QA):** Portfolio `HoldingsTable` (8 cols, [Portfolio.tsx:262](frontend/src/pages/Portfolio.tsx#L262)) is in an `overflow-hidden` wrapper with no mobile-card fallback → right columns clip at ≤sm. No page horizontal-scroll (constraint still met), but a readability gap. NOT fixed here — fix is either a mobile-card variant (feature) or contained `overflow-x-auto` (owner call vs §20.7 zero-scroll); both were out of scope for the gate.

## Completed

### Phase 1 — Backend core (async FastAPI + SQLAlchemy + aiosqlite, pure-Decimal)
- 5 models: `users`, `transactions`, `net_worth_entries`, `ips_rules`, `audit_logs`.
- `Money` type = NUMERIC(18,4)/exact Decimal; `D()` rejects float → no float on money paths.
- Ledger replay engine (avg-cost, oversell protection), valuation (NAV USD+MYR), drift vs 70/30, isolated Investment/FX/Combined returns (the (1.09)(1.05)−1=14.45% identity holds), rebalance (cash→contribution→sell-last), behavior flags, XIRR/TWRR utils.
- JWT-bearer auth, transactions CRUD (full-ledger revalidation), valuation/rebalance/behavior/health endpoints.
- Alembic `0001_initial`. Adversarial review done; 6 findings fixed.

### Phase 2 — Wealth Execution & Life-Cycle Domain (DESIGN §19)
- 6 new tables: `cash_accounts`, `cash_movements`, `deployment_intents`, `execution_plans`, `cycle_state_log`, `net_worth_cash_snapshots`; `ips_rules` extended with 3-tier enforcement + unified-window config columns. Alembic `0002_execution_domain` (up/down verified).
- 7 new services: `cash.py` + `deployment.py` (Cash Buffer System + deployment queue), `networth.py` (portfolio ⊂ net worth), `execution.py` (single Unified Execution Window Engine `classify_window()`: DEPLOYMENT every 3mo, REBALANCE every 6mo subset), `ips_enforcement.py` (three-tier INFO/WARN/BLOCK), `cycle.py` (Wealth Operating Cycle state machine), `action_status.py` (single output `DO_NOTHING | REVIEW | REBALANCE_NOW`).
- New endpoints (§19.7): `/cash/*`, `/deployment/*`, `/networth/*`, `/cycle/state`, `/action-status`, `/ips` (+`/validate`,`/compliance`), `/execution/*`. Transaction-create enforces IPS (BLOCK→422). Adversarial review done; in-window action-status signal fixed and live-confirmed.

### Phase 3 — Behavioral Interface Layer (DESIGN §20; built, gate pending)
**Backend auth hardening (DESIGN §9, ends DL 14 deferral; DL 26 dual-mode):**
- `auth_service.py` (405 LOC) — login, **rotating refresh sessions** (`auth_sessions`), single-use **password-reset tokens** (`password_reset_tokens`, 15-min TTL), AUTH-event auditing. Secrets stored as SHA-256 hashes; raw refresh/reset tokens returned once.
- `core/security.py` — token primitives (`generate_refresh_token`, `generate_reset_token`, `hash_token`, `verify_dummy_password`). `core/rate_limit.py` (162 LOC) — rate limiting. `core/deps.py` — **dual-mode auth (cookie-primary, bearer fallback)** so Phase 1–2 clients/tests keep working.
- `auth.py` router reworked (HttpOnly cookies + refresh-on rotation + reset), `schemas/auth.py`, `models/auth_session.py` (`AuthSession`, `PasswordResetToken`). Alembic **`0003_auth_sessions`**. New `tests/test_auth_hardening.py` (342 LOC).

**Frontend foundation (React + TS strict + Vite + Tailwind 3.4, mobile-first dark):**
- Build config: `vite.config.ts`, `tsconfig.json`(+node), `tailwind.config.ts`, `postcss.config.js`, `index.html`, `src/index.css`, `main.tsx`.
- API layer: `api/client.ts` (cookie auth + **refresh-on-401**), `api/endpoints.ts` (300 LOC), `types/api.ts` (731 LOC mirroring all Phase 1–2 shapes). State: **TanStack Query** hooks (`useAuth`, `useActionStatus`, `useCashBuffer`, `useCycle`, `useExecution`, `useNetWorth`, `usePortfolio`, `usePricing`, `useMediaQuery`) + Zustand stores (`prices`, `toast`, `ui`).
- UI kit (~22 components in `components/ui/`), layout (`AppShell`, `BottomNav`, `Sidebar`, `TopBar`, `MoreDrawer`), `ProtectedRoute`, `PlaceholderPage`, `charts/AllocationBar`, dashboard `ActionStatusCard` + `PricingPrompt`. `lib/` = `constants`, `format`, `queryKeys`, `utils`.
- Pages (lazy-loaded): **Dashboard** (Action-Status-first, §20.3), **ExecutionCenter** (840 LOC, §20.4 only decision surface), **Portfolio** (read-only, §20.5), **CashBuffer** (1029 LOC, §20.6), **Settings** (901 LOC), **Login**.

## In Progress
- None. Phase 3 gate + Integration & QA hardening pass both green. Awaiting go-ahead for Phase 5 (deployment) prep. **Do NOT deploy yet** (owner directive).
- **Deferred (carry forward):** Transactions page + Net Worth page are placeholders (`App.tsx`).
- **Not run (org policy needs confirmation):** real-browser pixel E2E (vite dev → uvicorn → Chromium). Validated instead via in-process ASGI E2E + static review. Recommend a manual 390px device check before deploy.

## Issues / Watch-outs
- **graphify-out/ is STALE:** generated 2026-06-17 11:59 (98 files / 4257 nodes / 8711 edges / 425 communities), **before** the Phase 3 frontend commit at 16:28 — so it does NOT reflect the ~9.5k LOC of frontend/auth code. **Regeneration via `graphify update .` was declined this session** (org policy: no tool-based CLI without explicit confirmation). Re-run `graphify update .` when ready to refresh the graph. It is gitignored (8.6 MB, regenerable) and contaminated by `graphify setup/` bundled docs.
- Net worth `investment_myr` is 0 when no prices/fx supplied to `/networth/summary` (documented fallback; Phase 4 price/FX refresh auto-populates) — `PricingPrompt.tsx` + `usePricing`/`stores/prices` surface this in the UI.
- KL timezone uses tzdata. `data/` (SQLite + throwaway test DBs) gitignored. `backend/smoke_test.py` (Phase 1) and `backend/smoke_phase2.py` (Phase 2) are standalone live-server smoke checks.

## Important Files
- `docs/DESIGN.md` — binding contract. §6 schema, §7 Phase-1 math, **§9 auth end-state**, **§19 execution domain**, **§19.9 LOCKED canonical pipeline**, **§20 behavioral interface (frontend)**, Decision Log (1–26).
- `docs/PHASES.md` — phase plan + status log (Phase 1–2 done; Phase 3 in progress, 4–8 pending).
- `backend/app/services/` — all domain logic + `auth_service.py`. `backend/app/core/` — `security.py`, `rate_limit.py`, `deps.py`, `config.py`. `backend/app/api/v1/` — routers (`auth.py` hardened). `backend/app/models/auth_session.py`. `backend/alembic/versions/` — 0001, 0002, **0003_auth_sessions**.
- `backend/tests/` — incl. **test_auth_hardening.py**. Run: `cd backend && SECRET_KEY=test ADMIN_EMAIL=a@b.c ADMIN_USERNAME=a ADMIN_PASSWORD=Admin12345! ENV=development .venv/Scripts/python.exe -m pytest -q`.
- `frontend/src/` — `App.tsx` (routes), `api/` (client+endpoints+types), `hooks/`, `stores/`, `components/` (ui/layout/dashboard/charts), `pages/`, `lib/`. Build: `cd frontend && npm run build` (and `npx tsc --noEmit`).

## Architecture Notes (LOCKED canonical pipeline — DESIGN §19.9)
```
ledger → valuation → drift
cash → deployable surplus
net worth = portfolio + cash + liabilities      (portfolio ⊂ net worth; reporting layer)
execution plan → action status
cycle state → system state machine
                 ▼
ACTION STATUS = DO_NOTHING | REVIEW | REBALANCE_NOW    ← single most important output
                 ▼
Frontend (§20) = a pure MIRROR of the engine: Action Status hero first, then
Net Worth → Cash Buffer → Execution Plan → Drift (last). NO client-side decisions.
FORBIDDEN: AI advisor/LLM, stock recommendations, market news, trading signals, technical indicators.
```
- Stack: async SQLAlchemy 2.0 + aiosqlite (Postgres-ready via DATABASE_URL); pure-Decimal money (NUMERIC(18,4)); ledger-first (all derived state computed, never stored authoritatively).
- One scheduler only (`classify_window`); three-tier IPS enforcement; operational cash (`cash_accounts`) separate from reporting cash (`net_worth_cash_snapshots`).
- Auth: dual-mode (cookie-primary access + rotating refresh session, bearer fallback); all secrets SHA-256-hashed; rate-limited; every AUTH event audited.
- Frontend: React + TS strict + Vite + Tailwind, mobile-first (≤390px, dark default); TanStack Query for server state + Zustand for UI/transient state; cookie client with refresh-on-401; `types/api.ts` mirrors backend shapes.

## Next Actions
1. **Free-tier go-live (current mode):** Render → Blueprint (`render.yaml`, plan **free**, no disk) → set `SECRET_KEY` + `ADMIN_*` + `FRONTEND_ORIGIN` in the dashboard → deploy → set the Render host in `frontend/vercel.json` → deploy frontend on Vercel (free) → validate with `backend/smoke_prod.py`. Ephemeral demo only (DB resets on spin-down). Upgrade to durable later via the paid deltas in DEPLOYMENT.md §1.5. Needs owner's cloud accounts to execute.
2. **Optional pre-launch (not blockers for single-user):** SMTP email for password reset (design in DEPLOYMENT.md §5); shorter access-token TTL; `asyncpg` if moving to Postgres; Sentry; `TrustedHostMiddleware` (Phase 8).
3. **Roadmap Phase 4 (feature work, separate from the QA/readiness gates):** Market Data, Analytics & Rebalancing UI — FX + price services, metrics, FX return decomposition, charts; auto-populates `investment_myr`. Also build deferred **Transactions** + **Net Worth** pages.
4. **Regenerate graphify** (`graphify update .`, when confirmed) so the graph reflects the Phase 3 frontend + QA/readiness changes.
- QA harness: `backend/qa_e2e_phase4.py` (re-runnable in-process E2E; joins `smoke_test.py`/`smoke_phase2.py`).
- Deployment blueprint: `docs/DEPLOYMENT.md`; env templates: `.env.example`, `.env.staging.example`, `.env.production.example`.
- Each phase ends at an approval gate (build green + tests + live smoke + adversarial review) before the next begins.
