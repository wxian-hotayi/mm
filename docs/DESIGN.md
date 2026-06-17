# WealthOS — Engineering Design Document

**This document is the single source of truth.** Every implementer MUST read it fully before writing code and MUST follow the contracts here exactly. If something is ambiguous, follow the conventions section; do not invent alternative shapes.

---

## 1. Product Overview

WealthOS is a personal wealth operating system for a Malaysian long-term investor (Moomoo MY broker, 70% VOO / 30% QQQ, 6-month rebalance, RM1,500–3,000/month contributions). It acts as a personal CFO: portfolio tracking, net worth, goals, projections, analytics, a rule-validated AI Chief Investment Officer, and a behavior-protection system that discourages emotional investing.

**Philosophy encoded in features:** Discipline > Intelligence. Consistency > Prediction. The system must be allowed — and often expected — to recommend doing nothing.

## 2. Architecture

- **Monorepo**: `backend/` (FastAPI + SQLAlchemy + SQLite) and `frontend/` (React + Vite + TS + Tailwind).
- **API-first**: frontend talks only to `/api/v1/*` REST endpoints. OpenAPI docs at `/docs`.
- **Derived state**: portfolio holdings, cash, P&L, allocations are ALWAYS computed from the transaction ledger. There is **no stored `holdings` table** — this is a deliberate decision (see §18 Decision Log).
- **Auth**: JWT access token + DB-backed refresh sessions, both in HttpOnly cookies.
- **Deploy**: Docker (single-container option: FastAPI serves built frontend from `/` if `frontend_dist` exists; two-container option via compose with nginx). SQLite file in a mounted volume; `DATABASE_URL` env allows PostgreSQL swap with zero code change.
- **External data** (graceful-failure, never blocks app): stooq.com for ETF daily closes, frankfurter.app (ECB) for USD/MYR with open.er-api.com fallback, OpenAI API for CIO narrative (optional — deterministic rules engine fallback when no key).

## 3. Repository Layout (exact)

```
mm/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # app factory, middleware, static serving
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py              # pydantic-settings Settings
│   │   │   ├── security.py            # bcrypt hash/verify, JWT encode/decode
│   │   │   ├── deps.py                # get_db, get_current_user, require_admin
│   │   │   ├── rate_limit.py          # in-memory sliding window middleware
│   │   │   ├── errors.py              # AppError hierarchy + handlers
│   │   │   └── logging.py             # structured logging setup
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # DeclarativeBase, naming conventions
│   │   │   ├── session.py             # engine, SessionLocal (Postgres-ready)
│   │   │   ├── types.py               # Money TypeDecorator (Decimal<->TEXT sqlite / NUMERIC pg)
│   │   │   └── init_db.py             # create_all + seed (roles, admin, goals, IPS, settings)
│   │   ├── models/                    # one file per aggregate; import all in __init__.py
│   │   │   ├── __init__.py
│   │   │   ├── user.py                # Role, User, AuthSession, PasswordResetToken
│   │   │   ├── transaction.py
│   │   │   ├── asset.py               # Asset, NetWorthEntry
│   │   │   ├── market.py              # FxRate, PriceHistory
│   │   │   ├── goal.py
│   │   │   ├── setting.py
│   │   │   ├── ips.py                 # IpsRule
│   │   │   ├── ai_report.py
│   │   │   ├── notification.py
│   │   │   └── audit.py
│   │   ├── schemas/                   # pydantic v2 request/response models
│   │   │   ├── __init__.py
│   │   │   ├── auth.py, user.py, transaction.py, portfolio.py, analytics.py,
│   │   │   ├── networth.py, goal.py, projection.py, rebalance.py, fx.py,
│   │   │   ├── ips.py, cio.py, behavior.py, notification.py, imports.py,
│   │   │   └── settings.py, common.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── portfolio.py           # ledger replay engine (THE core)
│   │   │   ├── fx.py                  # fx CRUD + decomposition
│   │   │   ├── prices.py              # price CRUD + latest lookup
│   │   │   ├── market_data.py         # stooq + frankfurter/er-api HTTP clients
│   │   │   ├── analytics.py           # series + metrics
│   │   │   ├── rebalance.py
│   │   │   ├── projection.py
│   │   │   ├── networth.py
│   │   │   ├── goals.py
│   │   │   ├── behavior.py            # discipline rule engine
│   │   │   ├── ips.py
│   │   │   ├── cio.py                 # snapshot builder, OpenAI + rules narrative
│   │   │   ├── importer.py            # Moomoo CSV parse/validate/dedupe
│   │   │   ├── notifications.py       # evaluate + resolve, dedupe_key
│   │   │   ├── audit.py
│   │   │   └── auth_service.py        # login/refresh/logout/reset flows
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── router.py          # aggregates all routers under /api/v1
│   │   │       ├── auth.py, admin.py, transactions.py, portfolio.py,
│   │   │       ├── analytics.py, fx.py, prices.py, networth.py, goals.py,
│   │   │       ├── projections.py, rebalance.py, ips.py, cio.py, behavior.py,
│   │   │       └── notifications.py, imports.py, settings.py, health.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── money.py               # D() Decimal helper, quantize, to_float
│   │       ├── dates.py               # KL timezone, month math
│   │       └── xirr.py                # XIRR Newton + bisection fallback
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/0001_initial.py   # hand-written, mirrors models exactly
│   ├── alembic.ini
│   ├── tests/                         # pytest; see §16
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .dockerignore
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                    # ALL routes defined here (lazy)
│   │   ├── index.css
│   │   ├── types/api.ts               # mirrors §8 response shapes exactly
│   │   ├── api/client.ts              # fetch wrapper, refresh-on-401
│   │   ├── api/endpoints.ts           # typed endpoint functions
│   │   ├── lib/format.ts              # fmtUSD, fmtMYR, fmtPct, fmtShares, gainClass
│   │   ├── lib/utils.ts               # cn() = clsx + tailwind-merge
│   │   ├── lib/constants.ts
│   │   ├── stores/toast.ts            # zustand toast store
│   │   ├── stores/ui.ts               # drawer/sheet state
│   │   ├── hooks/                     # useAuth, useMediaQuery, usePortfolio etc.
│   │   ├── components/
│   │   │   ├── ui/                    # hand-rolled shadcn-style kit (see §10)
│   │   │   ├── layout/                # AppShell, Sidebar, BottomNav, TopBar, MoreDrawer
│   │   │   └── charts/                # Recharts wrappers, shared dark theme
│   │   └── pages/
│   │       ├── Login.tsx, Dashboard.tsx, Portfolio.tsx, Transactions.tsx,
│   │       ├── NetWorth.tsx, Goals.tsx, Projections.tsx, Analytics.tsx,
│   │       ├── Rebalance.tsx, Cio.tsx, Ips.tsx, ImportPage.tsx,
│   │       └── SettingsPage.tsx, NotificationsPage.tsx, NotFound.tsx
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts                 # proxy /api -> http://localhost:8000
│   ├── tsconfig.json                  # strict: true
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── Dockerfile
│   ├── nginx.conf
│   └── .dockerignore
├── docs/                              # DESIGN.md (this), DEPLOYMENT.md, USER_GUIDE.md,
│                                      # MAINTENANCE.md, ROADMAP.md, API.md
├── scripts/
│   ├── create_admin.py                # CLI admin bootstrap
│   ├── backup.sh / restore.sh         # sqlite backup strategy
│   └── dev.sh                         # run backend + frontend dev
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── .gitignore
└── README.md
```

## 4. Pinned Stack

Backend (Python 3.12+, runs on 3.14): fastapi≥0.115, uvicorn[standard], **async SQLAlchemy≥2.0.36** (typed 2.0 style: `Mapped[]`/`mapped_column`, `AsyncSession`, aiosqlite driver; `DATABASE_URL=sqlite+aiosqlite:///...`, PostgreSQL via asyncpg later), alembic (async env), pydantic≥2.10 + pydantic-settings, bcrypt≥4.2 (use `bcrypt` directly, NOT passlib), PyJWT≥2.10, httpx, python-multipart, openai≥1.55, pytest + pytest-asyncio.

Frontend: React 18.3, react-router-dom 6.x, @tanstack/react-query 5.x, recharts 2.15.x, zustand 5.x, Tailwind **3.4.x** (classic `tailwind.config.ts`, NOT v4 CSS config), lucide-react, clsx, tailwind-merge, date-fns 4.x, Vite 5.4.x, TypeScript 5.6 strict.

## 5. Conventions

**Backend**
- Full type hints everywhere. SQLAlchemy 2.0 declarative (`Mapped`, `mapped_column`). No legacy Query API — use `select()`.
- All money/shares/rates are `Decimal` internally. Use `app.db.types.Money` column type (stores TEXT in SQLite, NUMERIC(20,8) in PostgreSQL). Never float arithmetic on money.
- JSON serialization: Decimals → floats via `app.utils.money.to_float` (quantize 8dp; 2dp for display fields is frontend's job). Dates → `"YYYY-MM-DD"`. Datetimes → ISO 8601 UTC.
- Errors: raise `AppError(status, code, message)` subclasses (`NotFoundError`, `ValidationFailed`, `AuthError`, `ForbiddenError`, `ConflictError`). Handler returns `{"detail": message, "code": code}`. FastAPI 422 validation passes through unchanged.
- Services take `(db: Session, user: User, ...)` and contain ALL business logic. Routers are thin: parse → call service → return schema. No business logic in routers.
- Every mutating endpoint writes an audit log via `services.audit.log(db, user, action, entity, entity_id, payload)`.
- Logging: `logging.getLogger("wealthos.<module>")`, configured once in `core/logging.py`.
- Timezone: store UTC; "today"/review-date logic uses `Asia/Kuala_Lumpur` (`app.utils.dates.kl_today()`).

**Frontend**
- TS strict; no `any` (use `unknown` + narrowing where needed).
- Server state via TanStack Query only; query keys: `['portfolio','summary']`, `['transactions', filters]`, `['networth','summary']`, etc. Mutations invalidate related keys (portfolio mutations invalidate `['portfolio']`, `['analytics']`, `['notifications']`, `['behavior']`).
- All API calls through `api/endpoints.ts`; components never call `fetch` directly.
- Mobile-first: design at 375px first, enhance with `md:`/`lg:`. No fixed widths > 100vw, tables become card lists on mobile.
- Currency display: MYR primary for net-worth/goals (user's life currency), USD primary with MYR secondary for portfolio.

## 6. Database Schema

SQLite (file `data/wealthos.db`), via SQLAlchemy; all PKs integer autoincrement unless noted. All tables have `created_at` (UTC datetime, server default). `Money` = Decimal type per §5.

| Table | Columns |
|---|---|
| `roles` | id, name TEXT UNIQUE (`admin`, `user`) |
| `users` | id, email TEXT UNIQUE NOT NULL, username TEXT UNIQUE NOT NULL, password_hash TEXT, role_id FK→roles, base_currency TEXT default `MYR`, is_active BOOL default true, last_login_at DT NULL, created_at, updated_at |
| `auth_sessions` | id TEXT PK (uuid4 hex), user_id FK, refresh_token_hash TEXT UNIQUE, user_agent TEXT, ip TEXT, remember BOOL, expires_at DT, revoked_at DT NULL, created_at |
| `password_reset_tokens` | id, user_id FK, token_hash TEXT UNIQUE, expires_at DT, used_at DT NULL, created_at |
| `transactions` | id, user_id FK, transaction_date DATE NOT NULL, transaction_type TEXT NOT NULL ∈ {DEPOSIT, WITHDRAWAL, BUY, SELL, DIVIDEND, FEE}, asset_symbol TEXT NULL (required for BUY/SELL/DIVIDEND), quantity Money NULL (shares, 4dp), unit_price_usd Money NULL, fee_usd Money NOT NULL default 0, fx_rate_recorded Money NOT NULL (USD→MYR at txn date), total_amount_myr Money NOT NULL (MYR; authoritative for cash events, server-derived for trades = (quantity×unit_price_usd+fee_usd)×fx_rate_recorded), amount_usd computed in engine (never stored), notes TEXT default '', import_hash TEXT NULL UNIQUE, created_at, updated_at. Index (user_id, transaction_date), (user_id, asset_symbol) |
| `assets` | id, user_id FK, name TEXT, category TEXT ∈ {INVESTMENT, CASH, EMERGENCY_FUND, BUSINESS, SAVINGS, OTHER_ASSET, LIABILITY}, currency TEXT ∈ {MYR, USD}, is_auto_portfolio BOOL default false (exactly one allowed: value mirrors computed portfolio), sort_order INT, is_archived BOOL default false, created_at, updated_at |
| `net_worth_entries` | id, user_id FK, asset_id FK, date DATE, value Money (asset currency; liabilities entered positive, subtracted in math), note TEXT default '', created_at. UNIQUE(asset_id, date) |
| `fx_rates` | id, date DATE UNIQUE, rate Money (1 USD = X MYR), source TEXT ∈ {manual, frankfurter, erapi, import}, created_at |
| `price_history` | id, ticker TEXT, date DATE, close Money (USD), source TEXT ∈ {stooq, manual}, created_at. UNIQUE(ticker, date) |
| `goals` | id, user_id FK, name TEXT, target_amount Money (MYR), sort_order INT, target_date DATE NULL, achieved_at DT NULL, is_default BOOL, created_at, updated_at |
| `settings` | id, user_id FK, key TEXT, value TEXT (JSON-encoded), UNIQUE(user_id, key) |
| `ips_rules` | id, user_id FK, rule_type TEXT (see §7.8), value TEXT (JSON), is_active BOOL default true, created_at, updated_at |
| `ai_reports` | id, user_id FK, model TEXT, source TEXT ∈ {openai, rules}, health_score INT, snapshot TEXT (JSON), report TEXT (JSON, shape §8 CioReport), raw_response TEXT NULL, tokens_in INT NULL, tokens_out INT NULL, created_at |
| `notifications` | id, user_id FK, type TEXT ∈ {REBALANCE_NEEDED, GOAL_ACHIEVED, CASH_DRAG, IPS_VIOLATION, DRIFT_ALERT, BEHAVIOR_WARNING, REVIEW_DUE, SYSTEM}, severity TEXT ∈ {INFO, WARNING, CRITICAL}, title TEXT, message TEXT, dedupe_key TEXT NULL, read_at DT NULL, resolved_at DT NULL, created_at. Index (user_id, resolved_at) |
| `audit_logs` | id, user_id FK NULL, action TEXT, entity TEXT, entity_id TEXT NULL, payload TEXT (JSON), ip TEXT NULL, user_agent TEXT NULL, created_at |

**Settings keys** (per-user, JSON values): `target_allocation` `{"VOO":0.70,"QQQ":0.30}`, `rebalance_threshold_pct` `3.0`, `rebalance_frequency_months` `6`, `max_cash_drag_pct` `5.0`, `expected_return_pct` `10.0`, `salary_growth_pct` `3.0`, `monthly_contribution_myr` `2500`, `risk_free_rate_pct` `3.0`, `theme` `"dark"`, `default_currency` `"MYR"`, `openai_api_key` `""` (write-only via API — never returned in full, masked `sk-...abc4`), `openai_model` `"gpt-4o-mini"`, `fx_source` `"auto"`, `notifications_email_enabled` `false`.

**Seed** (`init_db.py`, idempotent): roles; admin user from env (`ADMIN_EMAIL`/`ADMIN_USERNAME`/`ADMIN_PASSWORD` — required on first run); 7 default goals (RM50k, 100k, 300k, 500k, 1M, 3M, 5M); default IPS rules (§7.8); default settings; one default asset `Moomoo MY` (INVESTMENT, USD, is_auto_portfolio=true).

## 7. Domain Logic (normative formulas)

### 7.1 Ledger replay (services/portfolio.py)
Process user transactions ordered by `(date, id)`. Running state: `cash_usd`, per-ticker `{shares, cost_basis}` (average-cost method), `realized_gain`, `dividends_total`, `fees_total`, `net_deposits_usd`, `net_deposits_myr`.

- DEPOSIT: `cash += amount`; `net_deposits_usd += amount`; `net_deposits_myr += amount × fx_rate(txn)` (fx_rate falls back to latest stored rate ≤ date).
- WITHDRAWAL: reverse of deposit.
- BUY (requires ticker, shares>0, price≥0): `cost = shares×price + fee`; `cash -= cost`; `position.shares += shares`; `position.cost_basis += cost`.
- SELL: must not exceed held shares at that point (raise ValidationFailed listing the date) ; `avg = cost_basis/shares`; `proceeds = shares×price − fee`; `realized += proceeds − shares×avg`; `cash += proceeds`; `cost_basis -= shares×avg`; `position.shares -= shares`.
- DIVIDEND (ticker required): `cash += amount`; `dividends_total += amount`.
- FEE: `cash -= amount`; `fees_total += amount`.
- INTEREST: `cash += amount`.

Outputs: holdings list (ticker, shares, avg_cost, cost_basis), cash, realized, dividends, fees, net deposits. Market value uses latest price ≤ as-of date. `unrealized = market_value − cost_basis`. `total_pnl = unrealized + realized + dividends − fees_total`. `weight_i = value_i / (Σvalue + cash)` . `pnl_pct = total_pnl / net_deposits_usd` (0 if no deposits).

### 7.2 Value series
For each calendar day from first txn to today: replay state as of day; value_usd(d) = Σ shares×close(ticker, last≤d) + cash(d); value_myr(d) = value_usd(d) × fx(last≤d). External flows F(d) = deposits−withdrawals on d (USD).

### 7.3 Returns
- **XIRR / MWRR**: cash flows from investor perspective: each DEPOSIT → −amount at date, WITHDRAWAL → +amount, terminal +value_usd(today). Newton from 0.1, max 100 iters; fallback bisection on [−0.9999, 10]. MYR variant converts each flow at fx(date) and terminal at fx(today). Return `null` if <2 flows or sign-uniform.
- **TWRR**: daily chain-link `r_d = V_d / (V_{d−1} + F_d) − 1` (skip days where denominator = 0); `TWRR_cum = Π(1+r_d) − 1`; annualized `(1+TWRR_cum)^(365.25/days) − 1` (only when days ≥ 30, else null). CAGR displayed = annualized TWRR.
- **FX decomposition**: `r_total_myr = XIRR_myr`, `r_invest = XIRR_usd`, `r_fx = (1+r_total_myr)/(1+r_invest) − 1`. (Example: 9% & 5% → 14.45% total.)
- **Volatility**: stdev(daily r_d)×√252. **Sharpe**: (annualized TWRR − rf)/volatility (rf from settings). **Max drawdown**: on TWRR growth index `Π(1+r_d)`: `min(idx/runningMax − 1)`. **Best/worst month**: chain-linked monthly TWRR. **Rolling returns**: trailing-12M TWRR at each month end (needs ≥12 months).

### 7.4 Rebalance engine (services/rebalance.py)
Inputs: holdings+prices (latest or user-supplied), cash_usd, extra_cash_usd (planned contribution, default 0), targets, threshold_pct, allow_fractional (default true, 4dp shares).
1. `investable = Σvalues + cash + extra_cash`; drift_i = weight_i − target_i (weights vs Σvalues + cash deployable... compute current weights over Σvalues + cash).
2. If max|drift| ≤ threshold and cash_drag ≤ max_cash_drag → `status: "NO_ACTION"`, empty orders, message.
3. **CASH_ONLY attempt**: deployable = cash + extra_cash (keep `cash_buffer_usd` = 0 default). target_value_i = target_i × investable; buy_i = target_value_i − value_i. If all buy_i ≥ −0.005 → orders are BUYs only: shares = floor(buy_i/price_i, 4dp). status `CASH_ONLY`.
4. Else **SELL_REQUIRED**: delta_i = target_value_i − value_i; SELL where negative, BUY where positive.
Output: orders `[{ticker, side: BUY|SELL, shares, price, est_amount}]`, projected post-trade weights & leftover cash, human steps array ("1. Buy 2.4 VOO ≈ $1,380", …), status. Priority documented: cash → future contributions → sell only if necessary.

### 7.5 Projection engine
Monthly compounding: `m = (1+r_annual)^(1/12) − 1`. Contribution starts at `monthly_contribution` (MYR), grows by `salary_growth_pct` every 12 months (optionally `business_growth_pct` added to growth). For each scenario r ∈ {8, 10, 12, custom...}: simulate months 1..240 from `current_net_worth`; report yearly values and snapshot years {1,3,5,10,20}.

### 7.6 Goal ETA
Using base scenario (expected_return_pct + monthly_contribution from settings, current total net worth MYR): first month where projected NW ≥ target → ETA date + months remaining; `progress_pct = clamp(NW/target×100, 0, 100)`. If already achieved → achieved (auto-set `achieved_at` when net worth summary computed, fire GOAL_ACHIEVED notification).

### 7.7 Net worth
`total = Σ latest entry per active asset (converted to MYR via latest fx; LIABILITY subtracts) `; auto-portfolio asset uses live computed portfolio value (MYR) instead of manual entries. History: union of entry dates (+ month-end portfolio values) → series; monthly change = vs last calendar month-end; yearly = vs Dec 31 prior year.

### 7.8 IPS rules (seeded defaults)
`TARGET_ALLOCATION` `{"VOO":0.70,"QQQ":0.30}` · `ALLOWED_TICKERS` `["VOO","QQQ"]` · `REBALANCE_FREQUENCY_MONTHS` `6` · `REBALANCE_THRESHOLD_PCT` `3` · `MIN_HOLDING_PERIOD_YEARS` `10` (informational; sells flagged, not blocked) · `NO_INDIVIDUAL_STOCKS` `true` · `NO_OPTIONS` `true` · `NO_LEVERAGE` `true` · `MAX_CASH_DRAG_PCT` `5`.
`services/ips.py: check_compliance(db, user) -> list[IpsViolation {rule_type, severity, message, evidence}]` — e.g., holding ticker ∉ allowed, drift > threshold, sells within min holding period (vs first BUY date of ticker). `validate_action(action) -> violations` used by CIO and (advisory) transaction create.

### 7.9 Behavior protection (services/behavior.py)
Deterministic flags `{code, severity, title, message, evidence}`:
- `OVERTRADING`: ≥3 BUY/SELL within any rolling 7 days (last 30 days window).
- `PERFORMANCE_CHASING`: BUY of a ticker already overweight by >2pp at txn date (last 90 days).
- `EXCESSIVE_CASH`: cash weight > max_cash_drag_pct.
- `ALLOCATION_DRIFT`: any |drift| > threshold.
- `IPS_VIOLATION`: from §7.8.
- `CONTRIBUTION_GAP`: no DEPOSIT in 45+ days (INFO).

### 7.10 Health score (deterministic, 0–100)
Start 100: −4 per pp of max drift beyond threshold (cap 20); −2 per pp cash drag beyond max (cap 15); −10 per active IPS violation (cap 30); −10 if OVERTRADING; −5 if PERFORMANCE_CHASING; −5 if CONTRIBUTION_GAP; −10 if rebalance review overdue (> frequency + 1 month since later of last rebalance-pair-of-trades or first txn). Clamp [0,100]. Bands: ≥90 Excellent / ≥75 Good / ≥60 Needs Attention / <60 At Risk. `next_review_date` = last rebalance anchor + 6 months (KL time).

### 7.11 Notifications evaluation (services/notifications.py)
`evaluate(db, user)`: compute behavior flags + goal achievements + review due; for each, upsert by `dedupe_key` (e.g. `DRIFT:2026-06`, `GOAL:3`, `REVIEW:2026-12-31`) — create if no unresolved one exists; auto-resolve (`resolved_at`) those whose condition cleared. Called after transaction mutations, imports, and on `POST /notifications/evaluate`.

## 8. API Contract (prefix `/api/v1`)

Auth: cookies `wos_access` (JWT, 30 min) + `wos_refresh` (opaque, hashed in `auth_sessions`; 30 d if remember else 1 d). All endpoints require auth except login, refresh, password-reset, health. Admin-only marked Ⓐ. Pagination responses: `{items: [...], total, page, page_size}`. Errors: `{detail: string, code?: string}`.

```
POST /auth/login            {identifier, password, remember:bool} → UserOut (+cookies)
POST /auth/refresh          (cookie) → UserOut (rotates refresh)
POST /auth/logout           → {ok:true} (revokes session, clears cookies)
GET  /auth/me               → UserOut {id,email,username,role,base_currency,created_at,last_login_at}
POST /auth/password-reset/request {email} → {ok:true} (token logged server-side; SMTP later)
POST /auth/password-reset/confirm {token,new_password} → {ok:true}
GET  /admin/users Ⓐ · POST /admin/users Ⓐ {email,username,password,role} · PATCH /admin/users/{id} Ⓐ · DELETE /admin/users/{id} Ⓐ
GET  /admin/audit-logs Ⓐ   ?page&page_size&user_id&action → paginated AuditLogOut

GET  /transactions          ?type&ticker&search&date_from&date_to&sort(date|amount|ticker|type)&order(asc|desc)&page&page_size → paginated TransactionOut
POST /transactions          TransactionIn {date,type,ticker?,shares?,price?,amount,fee?,fx_rate?,notes?} → TransactionOut (+ ips_warnings: string[])
GET/PATCH/DELETE /transactions/{id}
GET  /portfolio/summary     → PortfolioSummary {as_of, fx_rate, value_usd, value_myr, cash_usd, cash_myr,
                              net_deposits_usd, net_deposits_myr, unrealized_gain, realized_gain, dividends_total,
                              fees_total, total_pnl, total_pnl_pct, cash_weight_pct,
                              holdings: [{ticker, shares, avg_cost, cost_basis, price, price_date, value_usd,
                                          weight_pct, target_weight_pct, drift_pp, unrealized_gain, unrealized_pct}]}
GET  /portfolio/history     ?range(3m|6m|1y|3y|all) → {points:[{date, value_usd, value_myr, net_deposits_usd, cash_usd}]}
POST /rebalance/preview     {prices?: {VOO?:number,QQQ?:number}, extra_cash_usd?, threshold_pct?} → RebalancePlan (§7.4)
GET  /analytics/metrics     → {cagr, xirr_usd, xirr_myr, twrr_cum, twrr_annualized, mwrr, fx_return,
                               investment_return, total_return_myr, volatility, sharpe, max_drawdown,
                               best_month:{month,return_pct}|null, worst_month:{...}|null, as_of} (all pct as decimals e.g. 0.0945, null when insufficient data)
GET  /analytics/series      ?type(value|contributions|allocation|pnl|rolling|drawdown)&range → {type, points:[...]}
       value: {date,value_usd,value_myr,net_deposits_usd} · contributions: {month,deposits_usd,withdrawals_usd,net_myr}
       allocation: {date,weights:{VOO,QQQ,CASH}} · pnl: {date,unrealized,realized_cum,total_pnl}
       rolling: {month,return_12m} · drawdown: {date,drawdown}
GET  /fx                    ?page... → paginated {date,rate,source} · POST /fx {date,rate} · POST /fx/refresh → {date,rate,source}
GET  /prices                ?ticker → latest per ticker [{ticker,date,close,source}] · POST /prices {ticker,date,close} · POST /prices/refresh → {updated:{VOO:n,QQQ:n}, latest:{...}}
GET  /assets · POST /assets {name,category,currency} · PATCH/DELETE /assets/{id}
GET  /networth/summary      → {total_myr, total_usd, by_category:[{category,total_myr,weight_pct}],
                               assets:[{id,name,category,currency,latest_value,latest_value_myr,latest_date,is_auto_portfolio}],
                               change_1m:{abs_myr,pct}|null, change_1y:{abs_myr,pct}|null, as_of}
GET  /networth/history      ?range → {points:[{date,total_myr,by_category:{...}}]}
POST /networth/entries      {date, entries:[{asset_id,value,note?}]} → {ok, count} (bulk upsert for a date)
GET  /networth/entries      ?asset_id → entries list
GET  /goals                 → [{id,name,target_amount,progress_pct,current_net_worth,eta_date|null,months_remaining|null,achieved_at,target_date,is_default,sort_order}]
POST /goals · PATCH/DELETE /goals/{id}
POST /projections           {current_net_worth?, monthly_contribution?, salary_growth_pct?, scenarios?: number[] (default [8,10,12]), years?: number (default 20)}
                            → {scenarios:[{annual_return_pct, yearly:[{year,date,value}], snapshots:{y1,y3,y5,y10,y20}}], assumptions:{...}}
GET  /ips                   → {rules:[IpsRuleOut], compliance:[IpsViolation], last_checked}
PUT  /ips                   {rules:[{rule_type,value,is_active}]} → same as GET
POST /cio/analyze           → CioReport (creates ai_reports row)
GET  /cio/reports           ?page → paginated CioReportMeta · GET /cio/reports/{id} → CioReport
   CioReport: {id, created_at, source, model, health_score, health_band, next_review_date,
               sections: {portfolio_health: string, allocation_status: string, cash_efficiency: string,
                          goal_progress: string, behavioral: string},
               recommended_actions: [{action, rationale, ips_compliant: bool, violation?: string}],
               no_action_items: string[], snapshot: {...key numbers used}}
GET  /behavior/flags        → {flags:[{code,severity,title,message,evidence}], generated_at}
GET  /notifications         ?unread_only → [{id,type,severity,title,message,read_at,resolved_at,created_at}]
POST /notifications/{id}/read · POST /notifications/read-all · POST /notifications/evaluate → {created:n, resolved:n}
POST /import/preview        multipart file= (csv) + form preset(auto|moomoo_trades|moomoo_funds|generic), mapping? (JSON col→field)
                            → {columns:[...], preset_detected, rows:[{row_number, parsed:TransactionIn|null, errors:[...], duplicate:bool, raw:{...}}], summary:{total,valid,invalid,duplicates}}
POST /import/commit         {rows:[TransactionIn+import_hash]} → {imported:n, skipped:n}
GET  /settings              → {settings:{key:value...}} (openai_api_key masked)
PUT  /settings              {settings:{key:value...}} → same (validates known keys/types)
GET  /health                → {status:"ok", version, db:"ok"}
```

## 9. Auth Design

- bcrypt (cost 12). JWT HS256 (`SECRET_KEY` env), claims `{sub: user_id, role, exp, iat, type:"access"}`.
- Refresh: 48-byte urlsafe token; SHA256 hash stored in `auth_sessions`; rotation on every refresh; revocation on logout. Cookies HttpOnly, `SameSite=Lax`, `Secure` when `ENV=production`, refresh cookie `Path=/api/v1/auth`.
- Rate limit: login & password-reset 10/min/IP; global API 240/min/IP. In-memory sliding window (document Redis swap in MAINTENANCE.md). Return 429 with `Retry-After`.
- RBAC: `require_admin` dependency. Non-admin sees only own data (all queries filter `user_id`).
- Password reset: token printed to server log + stored hashed (15 min TTL). SMTP integration is a roadmap item.

## 10. Frontend Design

**Theme (tailwind.config.ts extend)**: `bg #0A0E14`, `surface #111722`, `surface2 #1A2230`, `border #232D3F`, `text #E6EAF2`, `muted #8B94A7`, `accent #6366F1` (indigo-500 family), `gain #10B981`, `loss #F87171`, `warn #F59E0B`. Radius `xl` cards, soft shadows, font Inter (Google Fonts link in index.html, system fallback). Dark is the only theme in v1 (`theme` setting reserved).

**Navigation**: Desktop ≥`lg`: fixed left sidebar (14 items, grouped: Overview / Wealth / Portfolio / Intelligence / System). Mobile: top bar (logo, notifications bell with unread badge, avatar) + fixed bottom nav with 5 tabs: Home(/), Portfolio(/portfolio), Net Worth(/networth), CIO(/cio), More(opens MoreDrawer bottom-sheet with remaining pages). Add `pb-20` to main content on mobile so bottom nav never covers content.

**Routes** (App.tsx, all lazy with skeleton fallback): `/login` public; protected within AppShell: `/` Dashboard, `/portfolio`, `/transactions`, `/networth`, `/goals`, `/projections`, `/analytics`, `/rebalance`, `/cio`, `/ips`, `/import`, `/settings`, `/notifications`, `*` NotFound.

**Pages (key content)**
- **Dashboard**: hero stat (portfolio value USD + MYR + total P&L), stat grid (net worth, cash, net deposits, unrealized/realized), value area chart (range switch), allocation donut w/ target ring + drift badges, health score gauge card (links /cio), active warnings strip (behavior flags), recent transactions (5), next review date card.
- **Portfolio**: holdings table (mobile: cards) per §8 summary; allocation donut current vs target; drift bars with ±threshold color (within=gain, beyond=warn/loss); cash row.
- **Transactions**: filter bar (type chips, ticker, search, date range), sortable paginated table/card list, add/edit dialog (dynamic fields by type), delete confirm, IPS warning toasts on save.
- **NetWorth**: total hero + 1m/1y change, stacked area history by category, assets list grouped by category with latest values, "Update values" dialog (bulk entries for a date), asset CRUD.
- **Goals**: milestone timeline (vertical progress), per-goal cards: progress bar, %, ETA date, time remaining; add/edit custom goals.
- **Projections**: inputs form (prefilled from settings), multi-scenario line chart (8/10/12/custom), snapshot table (1/3/5/10/20y), wealth milestones intersection markers.
- **Analytics**: metrics grid (CAGR, XIRR, TWRR, MWRR, Sharpe, Vol, MaxDD, best/worst month), FX decomposition card (Investment + FX = Total, the +9%/+5%/+14.45% layout), charts: pnl, contributions (monthly bars), allocation history, rolling 12m, drawdown.
- **Rebalance**: current vs target bars, drift status banner (NO_ACTION = calm "Nothing to do — discipline wins"), inputs (extra cash, price overrides), plan steps list with exact shares, post-trade allocation preview, cash-first priority explanation.
- **Cio**: health score gauge + band, "Run Analysis" button, REAL TALK report sections, recommended actions list with IPS-compliance badges (violations flagged red), no-action items with ✓, report history list.
- **Ips**: policy statement document-style view, rules editor, live compliance checklist.
- **ImportPage**: stepper Upload → Map → Preview → Commit; dropzone; preset select + column mapping table; preview with error/duplicate badges; commit summary.
- **SettingsPage**: sections Profile (username/email/password change), Strategy (target alloc sliders summing 100, threshold, frequency, contribution), Market Data (fx source, manual fx/price entry, refresh buttons), AI (OpenAI key masked, model), Notifications, Danger zone (export data JSON).
- **NotificationsPage**: list grouped Unresolved/Resolved, severity icons, mark read/all.
- **Login**: centered card, identifier+password, remember-me switch, brand mark "WealthOS — Discipline beats intelligence."

**UI kit** (`components/ui/`, hand-rolled shadcn-style, no Radix): Button (variants default/secondary/ghost/destructive/outline; sizes sm/md/lg/icon), Card(+Header/Title/Description/Content/Footer), Input, Label, Textarea, Select (styled native), Badge (variants), Tabs, Dialog (portal, focus trap, Esc/overlay close), Drawer (bottom sheet), Table primitives, Skeleton, Spinner, Switch, Progress, Stat (label/value/delta), EmptyState, ErrorState, ConfirmDialog, toast system (`stores/toast.ts` + `Toaster` container), PageHeader.
**Charts** (`components/charts/`): shared `chartTheme.ts` (grid #232D3F, axis #8B94A7, tooltip dark card); ValueAreaChart, AllocationDonut (current ring + target inner ring + center label), NetWorthChart (stacked), ContributionBars, ProjectionChart, RollingReturnsChart, DrawdownChart, PnlChart. All `ResponsiveContainer` height 240–320, mobile-friendly tick counts.

## 11. Market Data Sources (services/market_data.py)

- **Prices**: stooq CSV `https://stooq.com/q/d/l/?s={ticker_lower}.us&i=d` (no key). Parse Date,Close. `refresh_prices` upserts missing dates for VOO & QQQ (and any held tickers). Timeout 10 s; on failure raise `MarketDataError` → endpoint returns 502 with friendly detail; app continues with stored data.
- **FX**: frankfurter `https://api.frankfurter.app/latest?from=USD&to=MYR` primary; fallback `https://open.er-api.com/v6/latest/USD`. Manual entry always available. History backfill: frankfurter `/{start}..{end}?from=USD&to=MYR`.
- All HTTP via httpx with explicit timeouts; never called implicitly during page loads — only via explicit `/refresh` endpoints (and CLI seed helper).

## 12. AI CIO (services/cio.py)

1. Build deterministic snapshot: summary, metrics, drift per ticker, cash drag, goals progress, behavior flags, IPS compliance, last rebalance anchor, next review date, monthly contribution plan, health score (§7.10 — computed in code, NEVER by the LLM).
2. If OpenAI key configured: chat.completions with `response_format json_object`; system prompt = institutional PM persona + full IPS + philosophy ("recommending nothing is success") + exact output JSON schema (sections/recommended_actions/no_action_items). Temperature 0.2. Parse → validate each recommended action via `ips.validate_action` (code-side); non-compliant actions get `ips_compliant:false` + violation text.
3. No key / API error → rules-based narrative generator produces the same CioReport shape from snapshot (templated, numbers-first, e.g. "Allocation drift 1.3% — within 3% policy. No action required. Continue RM2,500 monthly into VOO.").
4. Persist to `ai_reports`. Frontend renders identically for both sources (badge shows source).

## 13. Moomoo Import (services/importer.py)

CSV (utf-8/utf-8-sig, comma or semicolon). Presets map columns→canonical fields {date,type,ticker,shares,price,amount,fee,currency,notes}: `moomoo_trades` (Filled Time/Time, Stock Code/Symbol, Direction/Side→BUY/SELL, Filled Qty, Avg Price/Filled Price, Filled Amount, Commission/Fee, Currency), `moomoo_funds` (Time, Type→DEPOSIT/WITHDRAWAL/DIVIDEND/FEE/INTEREST keyword match, Amount, Currency, Remark), `generic` (date,type,ticker,shares,price,amount,fee,notes). `auto` detects by header match score. Custom mapping overrides. Ticker normalization: strip exchange suffix, uppercase ("VOO.US"→"VOO"). Dates: try ISO, `DD/MM/YYYY`, `MM/DD/YYYY` w/ disambiguation, datetime→date. Amount derivation: trades missing amount → shares×price. Validation per row (type-specific required fields); `import_hash = sha256(f"{user_id}|{date}|{type}|{ticker}|{shares}|{price}|{amount}")[:32]`; duplicate = hash exists in DB or earlier in file. Commit inserts valid+selected rows in one transaction, audits, triggers notifications evaluate.

## 14. Notifications & triggers

After: transaction create/update/delete, import commit, fx/price refresh, settings change (strategy keys) → `notifications.evaluate`. Bell badge = unread count (`GET /notifications?unread_only=true` length, polled by React Query every 60 s).

## 15. Security

Bcrypt(12); JWT 30 min; rotating hashed refresh tokens; HttpOnly+SameSite=Lax+Secure cookies; rate limiting; RBAC; per-user data isolation enforced in every service query; pydantic validation everywhere (Decimal coercion, enum types, date parsing); CSV upload max 2 MB & text-only parse; secrets only via env (.env never committed); OpenAI key stored per-user, masked on read; audit logs for all mutations; uniform 401/403; no SQL string interpolation (ORM only); CORS allowlist from env (`FRONTEND_ORIGIN`).

## 16. Testing (backend/tests)

pytest; fixtures in `conftest.py`: in-memory/tmp SQLite engine per test, `client` (TestClient with overridden `get_db`), `auth_client` (logged-in user), `admin_client`, `seeded_portfolio` (deterministic txn set with known expected numbers). Suites: `test_auth.py` (login/refresh/rotation/logout/rate-limit/rbac), `test_transactions.py` (CRUD/filters/oversell rejection), `test_portfolio_engine.py` (replay math incl. avg-cost, realized on partial sell, dividends, fees — exact Decimal assertions), `test_analytics.py` (XIRR known case, TWRR vs hand-computed, drawdown, fx decomposition 9/5/14.45), `test_rebalance.py` (NO_ACTION, CASH_ONLY exact shares, SELL_REQUIRED), `test_projection.py`, `test_networth_goals.py`, `test_behavior_ips.py`, `test_import.py` (preset parse, duplicates, commit), `test_notifications.py`. Target ≥90% on `app/services` + `app/utils`.

## 17. Environment Variables (.env.example)

```
ENV=development                # development|production
SECRET_KEY=change-me-64-chars
DATABASE_URL=sqlite:///./data/wealthos.db
FRONTEND_ORIGIN=http://localhost:5173
ADMIN_EMAIL=admin@example.com
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-strong
ACCESS_TOKEN_MINUTES=30
REFRESH_DAYS_REMEMBER=30
REFRESH_DAYS_DEFAULT=1
OPENAI_API_KEY=                # optional; per-user setting overrides
OPENAI_MODEL=gpt-4o-mini
LOG_LEVEL=INFO
```

## 18. Decision Log

1. **No `holdings` table** — Module 1 mandates zero duplicated data; holdings are a pure function of the ledger. A stored table would drift. (Spec's table list is satisfied by the derived `/portfolio/summary.holdings` read model.)
2. **Single `users` + `roles` tables** (not separate admin table) — standard RBAC, future multi-user ready.
3. **Average cost basis** — matches Moomoo MY display and spec's "Average Cost".
4. **Recharts over Chart.js** — first-class React composition; spec allows either.
5. **Hand-rolled shadcn-style kit** — zero Radix dependency risk, full visual control, same aesthetic.
6. **Tailwind 3.4 (not 4)** — stable classic config; upgrade is roadmap.
7. **Decimal-as-TEXT in SQLite** via TypeDecorator — exact money math; NUMERIC on PostgreSQL.
8. **Deterministic health score & IPS validation in code** — the LLM writes narrative only; numbers and compliance never come from the model.
9. **goal_progress derived** from net-worth history + projection, not stored.
10. **External APIs only on explicit refresh** — no hidden network calls, offline-friendly.
11. **Async SQLAlchemy stack** (owner directive 2026-06-11): `AsyncSession` + aiosqlite; all services and endpoints async.
12. **NUMERIC(18,4) precision** (owner directive, non-negotiable): `Money` TypeDecorator renders NUMERIC(18,4) on PostgreSQL and exact TEXT-backed Decimal on SQLite; all binds quantized to 4dp; floats never touch money, shares, or FX values. Rate *solving* (XIRR root-finding) may use float internally on dimensionless rates only.
13. **Pure-Decimal math engine, no NumPy/Pandas** — NumPy is float64-based, which conflicts with constraint 12; pure Decimal satisfies the high-precision requirement exactly.
14. **Phase 1 auth = JWT bearer** (login + dependency injection); cookie access/refresh rotation, remember-me, and rate limiting land in Phase 2 (§9 remains the end-state contract).
15. **Phase 1 schema subset** (owner directive): `users` (role TEXT column ∈ {admin,user} instead of a roles join table — simpler, RBAC-sufficient), `transactions` (fields per §6 as amended), `net_worth_entries` (standalone: entry_date, category, label, amount_myr, is_liability), `ips_rules` (ONE policy row per user: target_weights JSON, drift_threshold_pct, rebalance_frequency_months, min_holding_period_years, allowed_symbols JSON, no_individual_stocks/no_options/no_leverage flags), `audit_logs` (+ event_type/severity for behavioral flags). Remaining tables land in their feature phases.
16. **Execution & life-cycle domain is first-class** (owner directive 2026-06-11) — see §19. WealthOS is a *Wealth Execution Operating System*, not a tracker. The portfolio ledger is one input among several; the system's primary output is a single Action Status decision.
17. **Cash is ledger-first too** — `cash_accounts` + `cash_movements` (MYR side, pre-broker); account balances and deployable surplus are derived from the movement ledger, never stored. GXBank is a modeled account, not a free-text note.
18. **Net Worth is the top aggregate; portfolio is a subset** — `/networth/summary` aggregates live investment NAV (MYR) + live cash balances + manual assets − liabilities. `/portfolio/summary` remains the investment read-model feeding it.
19. **Single Unified Execution Window Engine** (owner refinement 2026-06-11): there is exactly ONE scheduler, not a quarterly schedule plus an independent rebalance schedule. A `DEPLOYMENT_WINDOW` opens every `deployment_interval_months` (default 3) from an anchor; a window is *also* a `REBALANCE_WINDOW` every `rebalance_interval_months` (default 6, validated to be an integer multiple of the deployment interval). **REBALANCE_WINDOW ⊂ DEPLOYMENT_WINDOW** by construction. Default outcome: deploy at Mar/Jun/Sep/Dec, rebalance at Jun/Dec. One function `classify_window(date)` is the single source of truth used by the cycle, action-status, and execution engines (§19.6).
20. **IPS three-tier enforcement** (owner refinement 2026-06-11): per-rule `enforcement_level ∈ {INFO, WARN, BLOCK}`. **INFO** = log only (audit, no Action-Status effect beyond record). **WARN** = surfaces in Action Status and reduces compliance score but *allows* execution. **BLOCK** = rejects the action with 422 — reserved ONLY for forbidden asset classes (leverage, options, non-allowed/illegal instruments). Behavioral/policy rules (drift, min-holding, cash drag) are WARN/INFO, never BLOCK. Every BLOCK event is audited (`AuditLog event_type='IPS_ALERT' severity='CRITICAL'`); a supplied, audited `override=true` is the only bypass.
21. **Wealth Operating Cycle state and Action Status are DERIVED, logged, not authoritatively stored** — current state is a pure function of (date vs windows, deployable cash, drift, active intents/plans); `cycle_state_log` records transitions for history/auditing only.
22. **Phase 2 re-scoped to domain + execution logic only** (owner directive) — no frontend, charts, analytics-metrics, or AI work in Phase 2; those shift to later phases (see PHASES.md).
23. **Cash model separation** (owner refinement 2026-06-11): `cash_accounts`/`cash_movements` are the **operational execution system** (GXBank, savings — drives deployable surplus, buffer, deployment queue). `net_worth_cash_snapshot` is a separate **reporting-layer** capture used by Net Worth history. Net Worth is a *reporting aggregate that references* cash; it does **not** replace or own the operational cash system. The two layers stay distinct (operational truth vs reporting snapshot).
24. **Canonical pipeline is locked** (owner confirmation 2026-06-11) — see §19.9. The architecture must preserve: `ledger → valuation → drift`; `cash → deployable surplus`; `net worth = portfolio + cash + liabilities`; `execution plan → action status`; `cycle state → system state machine`. **Action Status is the single most important output: `DO_NOTHING | REVIEW | REBALANCE_NOW`.**

## 19. WealthOS Execution & Life-Cycle Domain (Phase 2)

This section defines the layers that turn portfolio tracking into wealth *execution*. All services are async, ledger-first, pure-Decimal, and per-user isolated, consistent with §5. New tables land in Alembic migration `0002`.

### 19.0 Layer dependency graph (read top-down)
```
 (P1) transactions ─► ledger ─► valuation ─► drift
 (P2) cash_movements ─► cash.balances ─► deployable_surplus       (operational system)
        valuation(MYR) + cash (reporting view + net_worth_cash_snapshot) + net_worth_entries(manual, −liabilities)
                                   └─► networth.summary        (§19.4  reporting aggregate; Portfolio ⊂ Net Worth)
        ips_rules ─► ips_enforcement.validate / compliance_score   (§19.5  wraps txn-create, execution, AI)
        deployable + drift + rebalance(P1) + ips_enforcement
                                   └─► execution.generate_plan ─► execution_plans ─► deployment_intents (§19.6, §19.1)
        f(date∕windows, deployable, drift, active intents∕plans)
                                   └─► cycle.current_state ─► cycle_state_log               (§19.2)
        f(cycle, drift, ips_compliance, behavior(P1), cash readiness, windows)
                                   └─► action_status.compute  =  DO_NOTHING | REVIEW_REQUIRED | REBALANCE_NOW   (§19.3)
```

### 19.1 Cash Buffer System (`services/cash.py`, `services/deployment.py`)
Models the MYR side of the flow **salary → GXBank (accumulate) → transfer to Moomoo (FX) → broker DEPOSIT → BUY**.

Tables:
- `cash_accounts`: id, user_id FK, name, account_type ∈ {GXBANK, SAVINGS, EMERGENCY_FUND, BUSINESS, BROKER_CASH_MYR, OTHER}, currency default `MYR`, **is_buffer_source** BOOL (counts toward deployable investment cash — GXBank yes, emergency fund no), **target_buffer_myr** Money default 0 (minimum kept, never deployable), annual_interest_pct Money default 0 (GXBank daily-interest, informational), sort_order, is_archived, created_at, updated_at.
- `cash_movements` (ledger-first): id, user_id FK, account_id FK, movement_date DATE, movement_type ∈ {INFLOW, OUTFLOW, INTEREST, TRANSFER_OUT_TO_BROKER, TRANSFER_IN, ADJUSTMENT}, amount_myr Money (stored positive; sign applied by type), counterparty_account_id FK NULL (cash↔cash transfers), linked_transaction_id FK NULL (a TRANSFER_OUT_TO_BROKER that maps to a broker DEPOSIT), notes, created_at. Index (user_id, account_id, movement_date).
- `deployment_intents` (**the deployment queue**): id, user_id FK, source_account_id FK NULL, amount_myr Money, trigger ∈ {THRESHOLD, MANUAL, WINDOW}, status ∈ {QUEUED, PLANNED, EXECUTED, CANCELLED}, target_window_date DATE NULL, execution_plan_id FK NULL, notes, created_at, updated_at.

Derived (never stored):
- `balance(account) = Σ(INFLOW + INTEREST + TRANSFER_IN) − Σ(OUTFLOW + TRANSFER_OUT_TO_BROKER) ± ADJUSTMENT`, as-of any date.
- `deployable_surplus_myr = max(0, Σ balance(a) for a where is_buffer_source − Σ target_buffer_myr for those a)`.
- `buffer_fill_ratio = clamp(Σ buffer_source balance / Σ target_buffer_myr, 0, ∞)`.
- **Deployment-readiness state** (used by §19.2/§19.3): `READY` when `deployable_surplus_myr ≥ min_deploy_threshold_myr` (settings/IPS, default RM1,500 — a meaningful Moomoo buy), else `ACCUMULATING`.

Deployment queue ops (`services/deployment.py`): `enqueue(trigger, amount, window?)`; auto-enqueue a THRESHOLD intent when surplus first crosses the threshold (idempotent per open window); `attach_plan(intent, execution_plan)`; `execute(intent)` (mark EXECUTED, optionally emit the matching broker DEPOSIT + cash TRANSFER_OUT_TO_BROKER); `cancel`.

### 19.2 Wealth Operating Cycle Engine (`services/cycle.py`)
State machine over `{ACCUMULATION, READY_TO_DEPLOY, DEPLOYMENT, REBALANCE_WINDOW}`, evaluated continuously. Current state is **derived**; transitions append to `cycle_state_log` (id, user_id, state, entered_at, context JSON, created_at).

Deterministic precedence (first match wins). All window questions are answered by the single `classify_window(date)` of the Unified Execution Window Engine (§19.6) — the cycle engine never schedules independently:
1. **REBALANCE_WINDOW** — `classify_window(today) == REBALANCE` (a rebalance window is, by construction, also a deployment window), **or** a scheduled rebalance is overdue (a rebalance window has fully passed since the last EXECUTED rebalance plan / anchor while drift was beyond threshold).
2. **DEPLOYMENT** — an active `deployment_intent` (QUEUED/PLANNED) or an unexecuted `execution_plan` (DRAFT/APPROVED) exists, **or** `classify_window(today) == DEPLOYMENT` with `deployable_surplus ≥ threshold`.
3. **READY_TO_DEPLOY** — `deployable_surplus_myr ≥ min_deploy_threshold_myr` (cash ready, no open window / not yet acting).
4. **ACCUMULATION** — default; cash building below threshold, no open window, no pending plan.

`current_state(db, user, today=kl_today())` returns `{state, since, context{deployable, drift_max_pp, open_window, next_window_date, active_intents, last_rebalance_date}}` and writes a log row only when the state changes from the latest log entry.

### 19.3 Action Status Engine (`services/action_status.py`) — the single system-wide signal
First-class layer consumed by API now (and UI + AI later). Output ∈ `{DO_NOTHING, REVIEW_REQUIRED, REBALANCE_NOW}`:
- **REBALANCE_NOW** — `state == REBALANCE_WINDOW` and (`max|drift| > threshold` or scheduled rebalance overdue). The only state that asks the user to actively trade/sell.
- **REVIEW_REQUIRED** — any of: `READY_TO_DEPLOY`/`DEPLOYMENT` with deployable cash; an active IPS violation (any enforcement level); a behavior flag ≥ WARNING (§7.9 / P1 `behavior.py`); `max|drift| > 0.7 × threshold` (approaching); next window within `review_lead_days` (default 14); a DRAFT execution plan awaiting approval.
- **DO_NOTHING** — none of the above: accumulating, within policy, no window, no flags. *The disciplined default — the system is explicitly designed to return this and to treat it as success (philosophy §1).*

Returns `{status, headline, reasons: [{code, message, severity}], primary_action, next_window_date, next_rebalance_date, compliance_score, cycle_state, signals: {deployable_myr, max_drift_pp, cash_drag_pp, behavior_flag_count, ips_violation_count}, computed_at}`. Pure/deterministic; no LLM.

### 19.4 Net Worth Engine expansion (`services/networth.py`) — REPORTING aggregate
**Portfolio is a subset of Net Worth. Net Worth is a reporting layer that *references* the operational cash system — it does not replace it** (Decision Log 23). The operational truth for cash lives in `cash_accounts`/`cash_movements` (§19.1); Net Worth reads a cash figure for reporting and snapshots it for history. Canonical aggregation (all in MYR via latest FX):
```
net_worth_myr =  investment_myr            (= portfolio NAV USD × FX, live from valuation §7.1)
               + cash_myr                  (reporting view of operational cash, §19.1 balances, by account_type)
               + other_assets_myr          (net_worth_entries, non-cash asset categories)
               − liabilities_myr           (net_worth_entries, LIABILITY category)
```
`summary` returns `total_net_worth_myr`, `breakdown` by category `{INVESTMENT, CASH, EMERGENCY_FUND, BUSINESS, OTHER_ASSET, LIABILITY}` each with `{amount_myr, weight_pct, source: live|manual}`, the portfolio sub-object (NAV USD+MYR, holdings count), deployable surplus, and month/year change when history exists.

**Cash separation (reporting vs operational):**
- `cash_accounts`/`cash_movements` remain the sole operational system (deployable surplus, buffer, deployment queue). Net Worth never mutates them.
- `net_worth_cash_snapshots` (reporting layer): id, user_id FK, snapshot_date DATE, total_cash_myr Money, breakdown TEXT JSON `{account_type: amount_myr}`, source TEXT ∈ {auto, manual}, created_at; UNIQUE(user_id, snapshot_date). For "now", the summary reads live operational balances; for **history**, it reads/writes these snapshots (a month-end snapshot captures the operational cash position so historical net worth is reconstructable independently of later cash movements). `net_worth_entries.CASH` stays available for purely manual/external cash the user doesn't model as an operational account — it is additive, not a replacement, and de-duplicated against snapshot/operational cash in the summary.

### 19.5 IPS Enforcement Engine (`services/ips_enforcement.py`) — active three-tier layer
Extends Phase-1 stored IPS into enforcement. **Three tiers** (Decision Log 20): `INFO` (audit/log only, no execution effect), `WARN` (surfaces in Action Status + lowers compliance score, but **allows** execution), `BLOCK` (rejects with 422 — **reserved for forbidden asset classes only**: leverage, options, non-allowed/illegal instruments). Behavioral/policy rules are never BLOCK.

`ips_rules` gains per-rule level columns each ∈ {INFO, WARN, BLOCK}:
- `enforce_forbidden_assets` default **BLOCK**, `enforce_leverage` default **BLOCK**, `enforce_options` default **BLOCK** (the only BLOCK-eligible rules),
- `enforce_drift` default **WARN**, `enforce_min_holding` default **WARN**, `enforce_cash_drag` default **INFO**.
Plus engine config: `min_deploy_threshold_myr` Money default 1500, `review_lead_days` INT default 14, and the Unified Window Engine config (§19.6).
A `BLOCK` level is only honored for the forbidden-asset/leverage/options rules; if an operator sets a behavioral rule to BLOCK it is clamped to WARN (validated) so policy drift can never hard-block the user's own ledger.
- `validate_action(db, user, action, override=False) -> EnforcementVerdict {allowed: bool, max_level, violations: [{rule_type, level, message, evidence}]}`. `action` kinds: `TRANSACTION` (BUY/SELL — symbol allowed? options/leverage? sell within min-holding?), `EXECUTION_PLAN` (every order compliant), `AI_RECOMMENDATION` (Phase 6 — each recommended action through the same gate). `allowed = False` iff there is a BLOCK-level violation and not `override`.
- **Enforcement points**: transaction-create and execution-plan-approve call `validate_action`. A BLOCK without override ⇒ `ValidationFailed` (422) listing violations. INFO/WARN violations never block: they are recorded and returned as `warnings`, feeding Action Status. Every BLOCK outcome (and every override) writes `AuditLog(event_type='IPS_ALERT', severity='CRITICAL')`.
- `compliance_score(db, user) -> 0..100`: start 100; −15 per active BLOCK-level violation, −7 per WARN-level, −2 per INFO-level, −3 per approaching-threshold; clamp [0,100].
- `alerts(db, user) -> [IpsAlert]` persisted to `audit_logs` (`IPS_ALERT`) and surfaced via Action Status reasons; the dedicated `notifications` table arrives in Phase 6.

### 19.6 Unified Execution Window Engine + plan generation (`services/execution.py`)
**One deterministic scheduler — no separate quarterly and rebalance schedules** (Decision Log 19). Config (in `ips_rules`): `execution_anchor_month` INT default 3, `deployment_interval_months` INT default 3, `rebalance_interval_months` INT default 6 (validated: positive integer multiple of the deployment interval), `execution_window_days` INT default 21.

Single source of truth `classify_window(date) -> None | DEPLOYMENT | REBALANCE`:
- A **DEPLOYMENT_WINDOW** opens on day 1 of every month `M` where `(M − execution_anchor_month) mod deployment_interval_months == 0`, and stays open `execution_window_days`. (Defaults → Mar 1 / Jun 1 / Sep 1 / Dec 1, each open 21 days.)
- Such a window is also a **REBALANCE_WINDOW** when `(M − execution_anchor_month) mod rebalance_interval_months == (rebalance_interval_months − deployment_interval_months)` — i.e. every 2nd deployment window. (Defaults → Jun & Dec.) By construction **REBALANCE ⊂ DEPLOYMENT**.
- Returns `REBALANCE` if both hold (rebalance windows permit deploy *and* sells/correction), else `DEPLOYMENT` if inside a deployment window, else `None`.

Helpers all derive from `classify_window`: `next_window(today) -> (date, kind)`, `current_open_window(today) -> Window | None`, `is_rebalance(window)`. The cycle (§19.2) and action-status (§19.3) engines call these — they never compute windows themselves.

`generate_execution_plan(db, user, prices, fx_rate, kind?) -> ExecutionPlan` (persisted to `execution_plans`; `kind` defaults from `classify_window(today)`):
- `execution_plans`: id, user_id FK, window_date DATE, plan_kind ∈ {DEPLOY, REBALANCE, DEPLOY_AND_REBALANCE}, status ∈ {DRAFT, APPROVED, EXECUTED, SKIPPED, EXPIRED}, cash_deployed_myr/_usd Money, fx_rate_used Money, allocation_before JSON, allocation_after JSON, orders JSON `[{symbol, side, quantity(4dp), unit_price_usd, est_amount_usd, est_amount_myr}]`, steps JSON, ips_compliant BOOL, ips_violations JSON, created_at, executed_at NULL.
- **DEPLOY** (deploy-only window): deploy `deployable_surplus` (÷ FX → USD) toward 70/30 using the Phase-1 rebalance CASH_ONLY path — buys only, floored to 4dp, never overspends (the invariant verified in Phase 1).
- **REBALANCE / DEPLOY_AND_REBALANCE** (rebalance window): full Phase-1 rebalance (cash → contributions → sell-last) producing the **allocation correction** with exact buy/sell share quantities; deployable cash folded in first so selling is genuinely last-resort.
- Every generated plan is run through `ips_enforcement.validate_action(EXECUTION_PLAN)`; non-compliant orders are flagged (and blocked on approve per enforcement level). Approve → links/creates a `deployment_intent`; execute → marks EXECUTED and (optionally) emits broker transactions + cash movements.

### 19.7 API additions (prefix `/api/v1`, all JWT-guarded, per-user)
```
GET/POST /cash/accounts · PATCH/DELETE /cash/accounts/{id}
GET/POST /cash/movements (?account_id,date_from,date_to,page) · PATCH/DELETE /cash/movements/{id}
GET  /cash/summary                 → {accounts:[{...,balance_myr}], total_cash_myr, deployable_surplus_myr, buffer_fill_ratio, readiness}
GET/POST /deployment/queue (list / enqueue) · POST /deployment/{id}/cancel · POST /deployment/{id}/execute
GET  /cycle/state                  → current WealthCycleState (§19.2) + recent transitions
GET  /action-status                → ActionStatus (§19.3)  ◄ primary dashboard signal
GET  /networth/summary             → expanded aggregate (§19.4) · GET /networth/breakdown
GET  /ips · PUT /ips               → rules incl. enforcement levels (§19.5)
POST /ips/validate                 {action} → EnforcementVerdict
GET  /ips/compliance               → {score, violations[], alerts[]}
GET  /execution/windows            → {next_window_date, is_rebalance, schedule:[...]}
POST /execution/plan               {prices, fx_rate, kind?} → ExecutionPlan (DRAFT)
GET  /execution/plans (?status,page) · GET /execution/plans/{id}
POST /execution/plans/{id}/approve · POST /execution/plans/{id}/execute · POST /execution/plans/{id}/skip
```
Existing `/transactions` POST/PATCH now invokes `ips_enforcement` (blocking per level) in place of Phase-1 advisory-only warnings.

### 19.8 Testing additions
`test_cash.py` (balance derivation, deployable surplus with target buffers, transfer semantics), `test_cycle.py` (state precedence incl. window edges, overdue-rebalance, transition logging), `test_action_status.py` (each of the 3 outputs from constructed signal sets, DO_NOTHING default), `test_networth_expansion.py` (portfolio-as-subset aggregation, liabilities subtract, operational-vs-snapshot cash separation, no double-count), `test_ips_enforcement.py` (BLOCK rejects forbidden-asset/leverage/options BUY with 422; WARN & INFO allow with warning; behavioral rule set to BLOCK is clamped to WARN; override audited; three-tier compliance score), `test_execution.py` (`classify_window` boundaries: Mar/Jun/Sep/Dec deploy, Jun/Dec rebalance, REBALANCE⊂DEPLOYMENT, window-day edges; DEPLOY cash-only plan exact shares; REBALANCE allocation correction; plan validated by IPS), `test_deployment.py` (enqueue/threshold/cancel/execute, idempotent threshold enqueue). Target ≥90% on the new services.

### 19.9 Canonical Pipeline (LOCKED — Decision Log 24)
The architecture is approved on the condition that it preserves this pipeline exactly. Implementation must not break any link:
```
1.  ledger ──► valuation ──► drift                         (Phase 1, portfolio truth)
2.  cash (cash_movements) ──► deployable surplus           (§19.1, operational)
3.  net worth = portfolio + cash + other assets − liabilities   (§19.4, reporting aggregate; portfolio ⊂ net worth)
4.  execution plan ──► action status                       (a DRAFT/APPROVED plan and its drivers feed the decision)
5.  cycle state ──► system state machine                   (§19.2 derives the continuous state)
                              ▼
        ACTION STATUS  =  DO_NOTHING | REVIEW | REBALANCE_NOW   ◄── single most important output (§19.3)
```
Notes binding the implementation: cash separation is operational (cash_accounts) vs reporting (net_worth_cash_snapshot), never merged (DL 23); window logic is the one Unified Execution Window Engine (DL 19); enforcement is three-tier with BLOCK reserved for forbidden asset classes (DL 20). Action-Status enum values are `DO_NOTHING | REVIEW_REQUIRED | REBALANCE_NOW` with display labels "Do Nothing" / "Review" / "Rebalance Now" (`REVIEW` ≡ `REVIEW_REQUIRED`).
