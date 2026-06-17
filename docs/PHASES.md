# WealthOS — Phased Implementation Plan

Each phase ends in a **working, verified state** (app boots, build green, phase tests pass) and stops at an **approval gate**. No phase begins until the previous one is approved.

## Phase 1 — Backend Core: Schema, Math Engine, Secure API ◀ CURRENT
Scope fixed per owner directive (2026-06-11). **Backend only.**

**Step 1 — Database & backend core (async SQLAlchemy, SQLite via aiosqlite, PostgreSQL-ready):**
models for `users` (bcrypt hashing config, role), `transactions` (asset_symbol, transaction_type ∈ {DEPOSIT, WITHDRAWAL, BUY, SELL, DIVIDEND, FEE}, quantity, unit_price_usd, fee_usd, fx_rate_recorded, total_amount_myr), `net_worth_entries` (non-investment assets/liabilities), `ips_rules` (target weights 70/30, drift threshold, forbidden-asset flags), `audit_logs` (behavioral flags incl. high-frequency-trading attempts). NUMERIC(18,4) + Python `Decimal` everywhere — **no float on money paths**. Initial Alembic migration + idempotent seed (admin from env + default IPS policy).

**Step 2 — Financial math engine (pure, high-precision Decimal):**
ledger replay (average cost, realized/unrealized, cash, oversell protection) → NAV in USD & MYR; drift vs 70:30 IPS; isolated returns (Investment vs FX vs Combined, using `fx_rate_recorded`); rebalance recommendations with priority rules (cash → future contributions → sell last) and exact share quantities.

**Step 3 — Secure REST API (FastAPI async):**
`/api/v1/auth/login` + `/auth/me` (JWT bearer; cookie/refresh hardening lands in Phase 2), `/api/v1/transactions` CRUD with strict Pydantic validation + behavior flagging, `/api/v1/portfolio/valuation`, `/api/v1/portfolio/rebalance`, `/api/v1/analytics/behavior`, `/api/v1/health`. JWT dependency injection on all non-public routes.

**Verification gate:** server boots; live login → transaction create → valuation → rebalance → behavior calls all succeed; pytest suite green (exact-Decimal ledger asserts, FX identity 9% & 5% → 14.45%, rebalance share math); adversarial math review passed.

## Phase 2 — Wealth Execution Domain (RE-SCOPED per owner directive 2026-06-11) ◀ NEXT
**Backend domain + execution logic only. No frontend, charts, analytics-metrics, or AI work** (those move to later phases). Implements DESIGN §19 in full, on top of the Phase-1 ledger.

Deliverables (each ledger-first, async, pure-Decimal, per-user isolated):
1. **Cash Buffer System** (§19.1) — `cash_accounts` (GXBank et al.) + `cash_movements` ledger; derived balances; cash buffer target logic; deployable-surplus + readiness; **deployment queue** (`deployment_intents`, threshold/manual/window triggers).
2. **Net Worth Engine expansion** (§19.4) — aggregate live investment NAV + live cash + manual assets − liabilities; **portfolio becomes a subset of Net Worth**; category breakdown.
3. **Wealth Operating Cycle Engine** (§19.2) — `{ACCUMULATION, READY_TO_DEPLOY, DEPLOYMENT, REBALANCE_WINDOW}` derived state machine + `cycle_state_log`.
4. **Action Status Engine** (§19.3) — first-class `DO_NOTHING | REVIEW_REQUIRED | REBALANCE_NOW` decision layer (consumed by API now; UI + AI later).
5. **IPS Enforcement Engine** (§19.5) — three-tier per-rule INFO/WARN/BLOCK; BLOCK reserved for forbidden asset classes (leverage/options/non-allowed) and audited; WARN/INFO surface in Action Status but allow execution; compliance score + IPS alerts.
6. **Unified Execution Window Engine** (§19.6) — single `classify_window` scheduler: DEPLOYMENT every 3 months (Mar/Jun/Sep/Dec), REBALANCE every 6 months as a subset (Jun/Dec); `execution_plans` with cash deployment plan + exact share quantities + allocation correction.
7. **Cash model separation** (§19.4, DL 23) — `cash_accounts` operational vs `net_worth_cash_snapshots` reporting; Net Worth references cash, never replaces it.

Plus: Alembic migration `0002` (new tables + `ips_rules` enforcement columns), new API endpoints (§19.7), test suites (§19.8), and an adversarial review (precision / security / spec / **state-machine & enforcement logic**).

**Verification gate:** server boots; live flow — create cash account → record movements → cash summary shows deployable surplus → cycle state correct → action-status returns expected decision → generate execution plan with exact shares → IPS blocks a forbidden-asset BUY (422) → net-worth summary aggregates portfolio as a subset; pytest green (≥90% on new services, state-machine boundary cases, enforcement blocking, execution share math); adversarial review passed.

## Phase 3 — Behavioral Interface Layer ◀ IN PROGRESS
**Not "just frontend" — the behavioral interface to the execution engine** (DESIGN §20). Goal: reduce decisions, reinforce discipline, prevent emotional investing; the UI is a pure mirror of the backend decision engine and MUST NOT generate investment decisions (§20.0). **Forbidden:** AI advisor, LLM, stock recommendations, market news, trading signals, technical indicators (§20.1).

Deliverables:
1. **Auth hardening** (implements DESIGN §9 fully; DL 14 deferral ends): cookie-based access + rotating refresh sessions (`auth_sessions`, `password_reset_tokens` via Alembic `0003`), remember-me, rate limiting, password reset, RBAC; dual-mode (cookie-primary, bearer fallback) so Phase 1–2 tests/clients keep working (DL 26).
2. **Frontend foundation**: React + TS strict + Vite + Tailwind 3.4 + shadcn-style kit; mobile-first (≤390px, zero horizontal scroll), dark default, fast mobile load; cookie auth client with refresh-on-401; `types/api.ts` mirroring all Phase 1–2 shapes; TanStack Query.
3. **Behavior-first Dashboard** (§20.3): Action Status hero (DO_NOTHING default-success / REVIEW_REQUIRED / REBALANCE_NOW) → Net Worth → Cash Buffer → Execution Plan (if any) → Drift (informational, last).
4. **Execution Center** (§20.4) — the only decision surface: window status, execution plan (exact shares + amounts), required GXBank→Moomoo transfer amount, next rebalance date, IPS compliance; generate/approve/execute/skip (backend ops only).
5. **Portfolio** (read-only, §20.5): holdings, allocation, drift — no recommendations.
6. **Cash Buffer** (§20.6): GXBank balance, deployable surplus, target buffer, readiness; record cash movements (operational entry).
7. Supporting: **Transactions** (utilitarian record/list), **Net Worth** (manage assets/liabilities), **Settings** (profile, password, strategy/buffer/IPS enforcement), Login, NotFound.

**Verification gate (§20.9):** `tsc --noEmit` + `vite build` green; auth pytest green (cookie login / refresh-rotation / logout / rate-limit / reset); behavioral-compliance review (no forbidden surfaces, no client-side decisions, Action-Status primacy, no horizontal scroll at 390px); app opens → Action Status seen first.

## Phase 4 — Market Data, Analytics & Rebalancing UI
FX service (manual + frankfurter/er-api refresh, history), price service (manual + stooq refresh); daily value series; metrics (CAGR, XIRR, TWRR, MWRR, Sharpe, volatility, max drawdown, best/worst month, rolling 12m); FX return decomposition (Investment + FX = Total); rebalance & execution-plan UI; dashboard charts.

## Phase 5 — Goals & Projections
Goals with progress/ETA + default milestones; projection engine (multi-scenario 8/10/12/custom, salary growth, business growth); Net Worth history snapshots; Goals + Projections pages.

## Phase 6 — Intelligence Layer
Notifications (evaluate/dedupe/resolve, bell + page); AI Chief Investment Officer (deterministic health score + snapshot, OpenAI narrative with rules-engine fallback, REAL TALK report) — **every AI recommendation validated through the Phase-2 IPS Enforcement Engine**; CIO + IPS + Notifications pages; dashboard health score.

## Phase 7 — Import, Settings & Admin
Moomoo CSV import (presets, auto-detect, column mapping, validation, duplicate detection, preview → commit) feeding both broker transactions and cash movements; Settings page (profile, strategy, buffer targets, enforcement levels, market data, AI key masked, notifications, data export); admin user management.

## Phase 8 — Hardening & Delivery
Full test suite (≥90% coverage on services/utils), adversarial code review (math, security, contracts, mobile UX) + fixes, Docker + compose (single & two-container), deployment guides (Railway/Render/VPS), user & maintenance guides, roadmap.

---
**Status log**
- 2026-06-11: Phase 1 in progress.
- 2026-06-11: Phase 1 COMPLETE — 69 tests pass, 93% coverage, live HTTP smoke test 20/20, alembic up/down verified. Adversarial review (precision/security/spec) ran; all 6 findings fixed and independently re-verified (rebalance leftover-cash invariant fuzz-checked 0/20,000 negatives; partial-sell avg_cost invariant; failed-login audit; bcrypt timing-oracle defense; input length caps; async DB URL).
- 2026-06-11: ARCHITECTURE UPDATE (owner directive) — added the WealthOS Execution & Life-Cycle Domain (DESIGN §19): Cash Buffer System, Wealth Operating Cycle Engine, Action Status Engine, Net Worth expansion (portfolio ⊂ net worth), IPS Enforcement Engine, Execution Window Engine + Deployment Queue. Phase 2 RE-SCOPED to this domain/execution work only (UI/auth-hardening/analytics/AI shifted to Phases 3–8).
- 2026-06-11: ARCHITECTURE REFINEMENTS (owner) incorporated into §19 + Decision Log 19/20/23/24: (1) single Unified Execution Window Engine (`classify_window`, no dual scheduling); (2) three-tier IPS enforcement INFO/WARN/BLOCK with BLOCK reserved for forbidden asset classes; (3) cash model separation (operational `cash_accounts` vs reporting `net_worth_cash_snapshots`); (4) canonical pipeline LOCKED (§19.9). Phase 2 implementation APPROVED — building now.
- 2026-06-17: Phase 2 COMPLETE & approved (168 tests, ~88% cov, live smoke + adversarial review; action-status in-window signal fixed). graphify run, HANDOFF written, pushed to origin/main (2d8f78a).
- 2026-06-17: Phase 3 APPROVED & SCOPED as the Behavioral Interface Layer (DESIGN §20 added + DL 25/26). Building now: auth hardening + behavior-first frontend. UI mirrors the engine; AI/news/signals/recommendations forbidden.
