# WealthOS — Project Handoff

_Last updated: 2026-06-17 · Wealth Execution Operating System for a Malaysian long-term investor (Moomoo MY, 70% VOO / 30% QQQ, 6-month rebalance, RM1.5–3k/mo)._

## Status at a glance
- **Phase 1 — Backend Core: COMPLETE & verified.**
- **Phase 2 — Wealth Execution Domain: COMPLETE & verified.**
- **Phase 3 — Auth hardening + Frontend foundation + core pages: NOT STARTED (next).**
- Backend only so far. No frontend code yet (frontend/ holds package.json + deps only).
- Tests: **168 passing**, ~**88%** coverage. Live HTTP smoke tests passed for both phases.

## Completed

### Phase 1 — Backend core (async FastAPI + SQLAlchemy + aiosqlite, pure-Decimal)
- 5 models: `users`, `transactions`, `net_worth_entries`, `ips_rules`, `audit_logs`.
- `Money` type = NUMERIC(18,4)/exact Decimal; `D()` rejects float → no float on money paths.
- Ledger replay engine (avg-cost, oversell protection), valuation (NAV USD+MYR), drift vs 70/30, isolated Investment/FX/Combined returns (the (1.09)(1.05)−1=14.45% identity holds), rebalance (cash→contribution→sell-last), behavior flags, XIRR/TWRR utils.
- JWT-bearer auth, transactions CRUD (full-ledger revalidation), valuation/rebalance/behavior/health endpoints.
- Alembic `0001_initial`. Adversarial review done; 6 findings fixed (rebalance leftover-cash invariant fuzz-verified 0/20k negatives; partial-sell avg_cost invariant; failed-login audit; bcrypt timing-oracle defense; input length caps; async DB URL).

### Phase 2 — Wealth Execution & Life-Cycle Domain (DESIGN §19)
- 6 new tables: `cash_accounts`, `cash_movements`, `deployment_intents`, `execution_plans`, `cycle_state_log`, `net_worth_cash_snapshots`; `ips_rules` extended with 3-tier enforcement + unified-window config columns. Alembic `0002_execution_domain` (up/down verified).
- 7 new services:
  - `cash.py` + `deployment.py` — Cash Buffer System (GXBank-style accounts, ledger-derived balances, deployable surplus = buffer balances − target buffers, deployment queue with THRESHOLD/MANUAL/WINDOW triggers).
  - `networth.py` — reporting aggregate; **portfolio ⊂ net worth**; operational cash vs `net_worth_cash_snapshots` separation (no double-count).
  - `execution.py` — **single Unified Execution Window Engine** `classify_window()`: DEPLOYMENT every 3mo (Mar/Jun/Sep/Dec), REBALANCE every 6mo as a subset (Jun/Dec); plan generation with exact share quantities + allocation correction.
  - `ips_enforcement.py` — **three-tier INFO/WARN/BLOCK**; BLOCK reserved for forbidden asset classes (leverage/options/non-allowed), audited; behavioral rules clamped to WARN; compliance score.
  - `cycle.py` — Wealth Operating Cycle state machine `{ACCUMULATION, READY_TO_DEPLOY, DEPLOYMENT, REBALANCE_WINDOW}`, derived + logged.
  - `action_status.py` — **the single output: `DO_NOTHING | REVIEW | REBALANCE_NOW`** (REVIEW = REVIEW_REQUIRED).
- New endpoints (§19.7): `/cash/*`, `/deployment/*`, `/networth/*`, `/cycle/state`, `/action-status`, `/ips` (+`/validate`,`/compliance`), `/execution/*`. Transaction-create now enforces IPS (BLOCK→422).
- Adversarial review done. **Key fix:** action-status returned `DO_NOTHING` when inside a window with deployable cash but no price data — now returns `REVIEW_REQUIRED` (and `REBALANCE_NOW` when drift>threshold in a rebalance window). Live-confirmed.

## In Progress
- None. Phase 2 closed. Awaiting go-ahead for Phase 3.

## Issues / Watch-outs
- **graphify-out/ is gitignored** (8.6 MB, regenerable, and contaminated by the `graphify setup/` skill's bundled docs which graphify's cache won't drop without an `rm` that's blocked in this env). Regenerate locally with `graphify update .`. The WealthOS code is well represented (70 backend .py files; services layer is the densest cluster).
- KL timezone uses tzdata (added to requirements). `data/` (SQLite + throwaway test DBs) is gitignored.
- `backend/smoke_test.py` (Phase 1) and `backend/smoke_phase2.py` (Phase 2) are standalone live-server smoke checks (boot uvicorn, then run the script).
- Net worth `investment_myr` is 0 when no prices/fx are supplied to `/networth/summary` (documented fallback, not a bug) — Phase 4 adds price/FX refresh so this auto-populates.

## Important Files
- `docs/DESIGN.md` — binding contract. §6 schema, §7 Phase-1 math, **§19 execution domain**, **§19.9 LOCKED canonical pipeline**, Decision Log (1–24).
- `docs/PHASES.md` — phase plan + status log (Phase 1–2 done, 3–8 pending).
- `backend/app/services/` — all domain logic (ledger, valuation, drift, rebalance, returns, behavior, cash, deployment, networth, execution, cycle, action_status, ips_enforcement, auth_service, audit).
- `backend/app/api/v1/` — routers. `backend/app/models/` — ORM. `backend/alembic/versions/` — 0001, 0002.
- `backend/tests/` — 15 suites. Run: `cd backend && SECRET_KEY=test ADMIN_EMAIL=a@b.c ADMIN_USERNAME=a ADMIN_PASSWORD=Admin12345! ENV=development .venv/Scripts/python.exe -m pytest -q`.

## Architecture Notes (LOCKED canonical pipeline — DESIGN §19.9)
```
ledger → valuation → drift
cash → deployable surplus
net worth = portfolio + cash + liabilities      (portfolio ⊂ net worth; reporting layer)
execution plan → action status
cycle state → system state machine
                 ▼
ACTION STATUS = DO_NOTHING | REVIEW | REBALANCE_NOW    ← single most important output
```
- Stack: async SQLAlchemy 2.0 + aiosqlite (Postgres-ready via DATABASE_URL); pure-Decimal money (NUMERIC(18,4)); ledger-first (all derived state computed, never stored authoritatively).
- One scheduler only (`classify_window`); three-tier IPS enforcement; operational cash (`cash_accounts`) kept separate from reporting cash (`net_worth_cash_snapshots`).

## Next Actions (Phase 3)
1. Auth hardening: cookie access + rotating refresh sessions, remember-me, rate limiting, password reset (DESIGN §9 is the end-state contract).
2. Frontend foundation: Vite + TS strict + Tailwind dark design system, app shell, mobile bottom nav (DESIGN §10).
3. Login + protected routing; Dashboard built around **Action Status** + Net Worth + Cash Buffer + Cycle state; Portfolio, Transactions, Cash, Execution/Deployment pages over the Phase 1–2 API.
- Each phase ends at an approval gate (build green + tests + live smoke + adversarial review) before the next begins.
