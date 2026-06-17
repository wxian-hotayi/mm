/**
 * TypeScript interfaces mirroring the WealthOS Phase 1+2 backend API shapes
 * EXACTLY (DESIGN §8, §19.7). The backend serializes all money/shares/rates as
 * JSON numbers via `to_float` (DESIGN §5), so every money field here is a
 * `number` — the UI renders these verbatim and NEVER recomputes financial
 * values client-side (DESIGN §20.0). String-typed numeric fields (e.g. the
 * Action Status `signals`) are kept as `string` to match the backend exactly.
 *
 * Source of truth: `backend/app/schemas/*.py` + `backend/app/models/*.py`.
 */

// --------------------------------------------------------------------------- //
// Common                                                                       //
// --------------------------------------------------------------------------- //

/** Standard paginated response envelope (`schemas/common.py::Paginated`). */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** Error body returned by the backend error handlers (DESIGN §8). */
export interface ApiErrorBody {
  detail: string;
  code?: string;
}

// --------------------------------------------------------------------------- //
// Auth (schemas/auth.py)                                                        //
// --------------------------------------------------------------------------- //

/** `POST /auth/login` request. `identifier` is an email OR a username. */
export interface LoginIn {
  identifier: string;
  password: string;
  remember: boolean;
}

/** Issued JWT access token (`TokenOut`). */
export interface TokenOut {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

/** Public view of a user account (`UserOut`). */
export interface UserOut {
  id: number;
  email: string;
  username: string;
  role: string;
  base_currency: string;
  created_at: string;
  last_login_at: string | null;
}

/** `POST /auth/login` + `POST /auth/refresh` response (`LoginOut`). */
export interface LoginOut {
  token: TokenOut;
  user: UserOut;
}

/** Trivial acknowledgement payload (`OkOut`). */
export interface OkOut {
  ok: boolean;
}

/** `POST /auth/password-reset/request` request. */
export interface PasswordResetRequestIn {
  email: string;
}

/** `POST /auth/password-reset/confirm` request. */
export interface PasswordResetConfirmIn {
  token: string;
  new_password: string;
}

// --------------------------------------------------------------------------- //
// Transactions (schemas/transaction.py, models/transaction.py)                 //
// --------------------------------------------------------------------------- //

export type TransactionType =
  | "DEPOSIT"
  | "WITHDRAWAL"
  | "BUY"
  | "SELL"
  | "DIVIDEND"
  | "FEE";

/**
 * `POST /transactions` request (`TransactionIn`). Trades (BUY/SELL) require
 * `asset_symbol` + `quantity` + `unit_price_usd` and MUST NOT send `amount_usd`
 * or `total_amount_myr` (server-derived). Cash events (DEPOSIT/WITHDRAWAL/FEE)
 * require exactly one of `amount_usd | total_amount_myr` and no symbol;
 * DIVIDEND requires `asset_symbol` + exactly one amount. `fx_rate_recorded` is
 * always required and positive.
 */
export interface TransactionIn {
  transaction_type: TransactionType;
  transaction_date: string;
  asset_symbol?: string | null;
  quantity?: number | null;
  unit_price_usd?: number | null;
  fee_usd?: number;
  amount_usd?: number | null;
  total_amount_myr?: number | null;
  fx_rate_recorded: number;
  notes?: string;
}

/** Partial update payload (`TransactionUpdate`); unset fields keep values. */
export interface TransactionUpdate {
  transaction_type?: TransactionType;
  transaction_date?: string;
  asset_symbol?: string | null;
  quantity?: number | null;
  unit_price_usd?: number | null;
  fee_usd?: number;
  amount_usd?: number | null;
  total_amount_myr?: number | null;
  fx_rate_recorded?: number;
  notes?: string;
}

/** Stored transaction row plus derived USD amount (`TransactionOut`). */
export interface TransactionOut {
  id: number;
  transaction_type: string;
  transaction_date: string;
  asset_symbol: string | null;
  quantity: number | null;
  unit_price_usd: number | null;
  fee_usd: number;
  fx_rate_recorded: number;
  total_amount_myr: number;
  amount_usd: number;
  notes: string;
  created_at: string;
  updated_at: string;
}

/**
 * `POST /transactions` response (`TransactionWithWarningsOut`): the stored row
 * plus behavior warnings and INFO/WARN IPS warnings (BLOCK rejects with 422).
 */
export interface TransactionWithWarningsOut extends TransactionOut {
  behavior_warnings: string[];
  ips_warnings: string[];
}

// --------------------------------------------------------------------------- //
// Portfolio: valuation + rebalance (schemas/portfolio.py)                      //
// --------------------------------------------------------------------------- //

/** `POST /portfolio/valuation` request (`ValuationIn`). */
export interface ValuationIn {
  /** USD price per symbol (uppercase keys). */
  prices: Record<string, number>;
  fx_rate: number;
  as_of?: string | null;
}

/** One priced holding (`HoldingOut`). */
export interface HoldingOut {
  symbol: string;
  quantity: number;
  avg_cost_usd: number;
  cost_basis_usd: number;
  price_usd: number;
  market_value_usd: number;
  unrealized_usd: number;
  unrealized_pct: number | null;
  /** Weight on the 0–100 scale. */
  weight_pct: number | null;
}

/** Full portfolio valuation (`ValuationOut`). */
export interface ValuationOut {
  holdings: HoldingOut[];
  cash_usd: number;
  nav_usd: number;
  nav_myr: number;
  fx_rate: number;
  cash_weight_pct: number | null;
  unrealized_usd: number;
  realized_usd: number;
  dividends_usd: number;
  fees_usd: number;
  total_pnl_usd: number;
  total_pnl_pct: number | null;
  net_deposits_usd: number;
  net_deposits_myr: number;
}

/** `POST /portfolio/rebalance` request (`RebalanceIn`). */
export interface RebalanceIn {
  prices: Record<string, number>;
  fx_rate: number;
  additional_cash_usd?: number;
  threshold_pct?: number | null;
}

/** One rebalance order (`RebalanceOrderOut`). `side` is "BUY" | "SELL". */
export interface RebalanceOrderOut {
  symbol: string;
  side: string;
  quantity: number;
  unit_price_usd: number;
  est_amount_usd: number;
  est_amount_myr: number;
}

/** Rebalance plan (`RebalanceOut`). `status` ∈ NO_ACTION|CASH_ONLY|SELL_REQUIRED. */
export interface RebalanceOut {
  status: string;
  orders: RebalanceOrderOut[];
  steps: string[];
  /** Pre-trade weights on the 0–100 scale, incl. "CASH". */
  current_weights: Record<string, number>;
  /** Post-trade weights on the 0–100 scale, incl. "CASH". */
  post_trade_weights: Record<string, number>;
  leftover_cash_usd: number;
  max_drift_pp: number;
  priority_note: string;
  message: string;
}

// --------------------------------------------------------------------------- //
// Behavior report (schemas/behavior.py) — GET /analytics/behavior              //
// --------------------------------------------------------------------------- //

/** One deterministic behavior flag (`BehaviorFlagOut`). */
export interface BehaviorFlagOut {
  code: string;
  severity: string;
  title: string;
  message: string;
  evidence: Record<string, unknown>;
}

/** Trade-frequency statistics over trailing 30 days (`TradeStatsOut`). */
export interface TradeStatsOut {
  trades_30d: number;
  buys_30d: number;
  sells_30d: number;
  max_trades_in_7d: number;
}

/** One previously recorded behavior flag from the audit log. */
export interface BehaviorHistoryOut {
  code: string;
  severity: string;
  title: string;
  message: string;
  created_at: string;
}

/** Full behavior report (`BehaviorReportOut`). */
export interface BehaviorReportOut {
  flags: BehaviorFlagOut[];
  trade_stats: TradeStatsOut;
  recent_history: BehaviorHistoryOut[];
  generated_at: string;
}

// --------------------------------------------------------------------------- //
// Cash buffer system (schemas/cash.py, models/cash.py) — §19.1                 //
// --------------------------------------------------------------------------- //

export type CashAccountType =
  | "GXBANK"
  | "SAVINGS"
  | "EMERGENCY_FUND"
  | "BUSINESS"
  | "BROKER_CASH_MYR"
  | "OTHER";

export type CashMovementType =
  | "INFLOW"
  | "OUTFLOW"
  | "INTEREST"
  | "TRANSFER_OUT_TO_BROKER"
  | "TRANSFER_IN"
  | "ADJUSTMENT";

/** Deployment readiness state (`cash.py::ReadinessState`). */
export type ReadinessState = "READY" | "ACCUMULATING";

/** `POST /cash/accounts` request (`CashAccountIn`). */
export interface CashAccountIn {
  name: string;
  account_type: CashAccountType;
  currency?: string;
  is_buffer_source?: boolean;
  target_buffer_myr?: number;
  annual_interest_pct?: number;
  sort_order?: number;
}

/** `PATCH /cash/accounts/{id}` request (`CashAccountUpdate`). */
export interface CashAccountUpdate {
  name?: string;
  account_type?: CashAccountType;
  is_buffer_source?: boolean;
  target_buffer_myr?: number;
  annual_interest_pct?: number;
  sort_order?: number;
}

/** A cash account as stored (`CashAccountOut`). */
export interface CashAccountOut {
  id: number;
  name: string;
  account_type: string;
  currency: string;
  is_buffer_source: boolean;
  target_buffer_myr: number;
  annual_interest_pct: number;
  sort_order: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

/** A cash account plus its derived MYR balance (`CashAccountBalanceOut`). */
export interface CashAccountBalanceOut extends CashAccountOut {
  balance_myr: number;
}

/** `POST /cash/movements` request (`CashMovementIn`). */
export interface CashMovementIn {
  account_id: number;
  movement_type: CashMovementType;
  amount_myr: number;
  movement_date: string;
  counterparty_account_id?: number | null;
  linked_transaction_id?: number | null;
  notes?: string;
}

/** `PATCH /cash/movements/{id}` request (`CashMovementUpdate`). */
export interface CashMovementUpdate {
  movement_type?: CashMovementType;
  amount_myr?: number;
  movement_date?: string;
  notes?: string;
}

/** A stored cash movement row (`CashMovementOut`). */
export interface CashMovementOut {
  id: number;
  account_id: number;
  movement_date: string;
  movement_type: string;
  amount_myr: number;
  counterparty_account_id: number | null;
  linked_transaction_id: number | null;
  notes: string;
  created_at: string;
}

/** Derived cash position (`CashSummaryOut`) — `GET /cash/summary`. */
export interface CashSummaryOut {
  accounts: CashAccountBalanceOut[];
  total_cash_myr: number;
  deployable_surplus_myr: number;
  buffer_fill_ratio: number | null;
  readiness: ReadinessState;
  as_of: string;
}

// --------------------------------------------------------------------------- //
// Deployment queue (schemas/deployment.py, models/deployment.py) — §19.1       //
// --------------------------------------------------------------------------- //

export type DeploymentTrigger = "THRESHOLD" | "MANUAL" | "WINDOW";
export type DeploymentStatus = "QUEUED" | "PLANNED" | "EXECUTED" | "CANCELLED";

/** `POST /deployment/queue` request (`DeploymentIntentIn`). */
export interface DeploymentIntentIn {
  trigger?: DeploymentTrigger;
  amount_myr: number;
  source_account_id?: number | null;
  target_window_date?: string | null;
  notes?: string;
}

/** Optional body for `POST /deployment/{id}/execute` (`DeploymentExecuteIn`). */
export interface DeploymentExecuteIn {
  emit_movement?: boolean;
  source_account_id?: number | null;
  movement_date?: string | null;
  fx_rate?: number | null;
  notes?: string;
}

/** A queued deployment intent as stored (`DeploymentIntentOut`). */
export interface DeploymentIntentOut {
  id: number;
  source_account_id: number | null;
  amount_myr: number;
  trigger: string;
  status: string;
  target_window_date: string | null;
  execution_plan_id: number | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

// --------------------------------------------------------------------------- //
// Net Worth (schemas/networth.py) — §19.4                                      //
// --------------------------------------------------------------------------- //

/** `POST /networth/summary` pricing body (`NetWorthQueryIn`). */
export interface NetWorthQueryIn {
  prices?: Record<string, number> | null;
  fx_rate?: number | null;
  as_of?: string | null;
}

/** The investment subset of Net Worth (`PortfolioSubsetOut`). */
export interface PortfolioSubsetOut {
  nav_usd: number;
  nav_myr: number;
  holdings_count: number;
  fx_rate: number | null;
  priced: boolean;
}

/** One Net Worth category line (`BreakdownItemOut`). `source` ∈ live|manual. */
export interface BreakdownItemOut {
  category: string;
  amount_myr: number;
  /** Weight on the 0–100 scale. */
  weight_pct: number | null;
  source: string;
}

/** Absolute (MYR) + percentage change vs a baseline (`NetWorthChangeOut`). */
export interface NetWorthChangeOut {
  abs_myr: number;
  pct: number | null;
}

/** Full Net Worth reporting aggregate (`NetWorthSummaryOut`). */
export interface NetWorthSummaryOut {
  as_of: string;
  total_net_worth_myr: number;
  investment_myr: number;
  cash_myr: number;
  other_assets_myr: number;
  liabilities_myr: number;
  breakdown: BreakdownItemOut[];
  portfolio: PortfolioSubsetOut;
  deployable_surplus_myr: number;
  change_1m: NetWorthChangeOut | null;
  change_1y: NetWorthChangeOut | null;
}

/** `GET /networth/breakdown` response (`NetWorthBreakdownOut`). */
export interface NetWorthBreakdownOut {
  as_of: string;
  total_net_worth_myr: number;
  breakdown: BreakdownItemOut[];
}

// --------------------------------------------------------------------------- //
// Cycle (schemas/cycle.py, models/cycle.py) — §19.2                            //
// --------------------------------------------------------------------------- //

export type WealthCycleState =
  | "ACCUMULATION"
  | "READY_TO_DEPLOY"
  | "DEPLOYMENT"
  | "REBALANCE_WINDOW";

/** One logged wealth-cycle transition (`CycleTransitionOut`). */
export interface CycleTransitionOut {
  id: number;
  state: string;
  entered_at: string;
  context: Record<string, unknown>;
  created_at: string;
}

/** Derived current cycle state + recent transitions (`CycleStateOut`). */
export interface CycleStateOut {
  state: string;
  since: string;
  context: Record<string, unknown>;
  recent_transitions: CycleTransitionOut[];
}

// --------------------------------------------------------------------------- //
// Action Status (schemas/action_status.py) — §19.3, the primary signal         //
// --------------------------------------------------------------------------- //

export type ActionStatusValue =
  | "DO_NOTHING"
  | "REVIEW_REQUIRED"
  | "REBALANCE_NOW";

/** One driver behind the Action Status decision (`ReasonOut`). */
export interface ActionStatusReason {
  code: string;
  message: string;
  severity: string;
}

/**
 * Action Status `signals` (`schemas/action_status.py`): Decimals are emitted as
 * strings; `max_drift_pp` / `cash_drag_pp` are `null` when unpriced.
 */
export interface ActionStatusSignals {
  deployable_myr: string;
  max_drift_pp: string | null;
  cash_drag_pp: string | null;
  behavior_flag_count: number;
  ips_violation_count: number;
}

/** The single system-wide Action Status decision (`ActionStatusOut`). */
export interface ActionStatusOut {
  status: ActionStatusValue;
  /** Display label: "Do Nothing" | "Review" | "Rebalance Now". */
  label: string;
  headline: string;
  reasons: ActionStatusReason[];
  primary_action: string;
  next_window_date: string;
  next_rebalance_date: string | null;
  compliance_score: number;
  cycle_state: string;
  signals: ActionStatusSignals;
  computed_at: string;
}

// --------------------------------------------------------------------------- //
// IPS enforcement (schemas/ips.py, models/ips.py) — §19.5                      //
// --------------------------------------------------------------------------- //

export type IpsEnforcementLevel = "INFO" | "WARN" | "BLOCK";

/** The user's IPS policy with enforcement levels + window config (`IpsRuleOut`). */
export interface IpsRuleOut {
  id: number;
  /** Target weights on the 0–1 scale (e.g. { VOO: 0.7, QQQ: 0.3 }). */
  target_weights: Record<string, number>;
  drift_threshold_pct: number;
  rebalance_frequency_months: number;
  min_holding_period_years: number;
  allowed_symbols: string[];
  no_individual_stocks: boolean;
  no_options: boolean;
  no_leverage: boolean;
  max_cash_drag_pct: number;
  enforce_forbidden_assets: IpsEnforcementLevel;
  enforce_leverage: IpsEnforcementLevel;
  enforce_options: IpsEnforcementLevel;
  enforce_drift: IpsEnforcementLevel;
  enforce_min_holding: IpsEnforcementLevel;
  enforce_cash_drag: IpsEnforcementLevel;
  min_deploy_threshold_myr: number;
  review_lead_days: number;
  execution_anchor_month: number;
  deployment_interval_months: number;
  rebalance_interval_months: number;
  execution_window_days: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** `PUT /ips` partial update payload (`IpsRuleIn`). */
export interface IpsRuleIn {
  target_weights?: Record<string, number> | null;
  drift_threshold_pct?: number | null;
  rebalance_frequency_months?: number | null;
  min_holding_period_years?: number | null;
  allowed_symbols?: string[] | null;
  no_individual_stocks?: boolean | null;
  no_options?: boolean | null;
  no_leverage?: boolean | null;
  max_cash_drag_pct?: number | null;
  enforce_forbidden_assets?: IpsEnforcementLevel | null;
  enforce_leverage?: IpsEnforcementLevel | null;
  enforce_options?: IpsEnforcementLevel | null;
  enforce_drift?: IpsEnforcementLevel | null;
  enforce_min_holding?: IpsEnforcementLevel | null;
  enforce_cash_drag?: IpsEnforcementLevel | null;
  min_deploy_threshold_myr?: number | null;
  review_lead_days?: number | null;
  execution_anchor_month?: number | null;
  deployment_interval_months?: number | null;
  rebalance_interval_months?: number | null;
  execution_window_days?: number | null;
  is_active?: boolean | null;
}

/** One IPS violation (`IpsViolationOut`). */
export interface IpsViolationOut {
  rule_type: string;
  level: IpsEnforcementLevel;
  message: string;
  evidence: Record<string, unknown>;
}

/** Verdict for a validated action (`EnforcementVerdictOut`). */
export interface EnforcementVerdictOut {
  allowed: boolean;
  max_level: IpsEnforcementLevel | null;
  violations: IpsViolationOut[];
  warnings: IpsViolationOut[];
}

/** IPS compliance report (`ComplianceOut`) — `GET /ips/compliance`. */
export interface ComplianceOut {
  score: number;
  violations: IpsViolationOut[];
  alerts: IpsViolationOut[];
}

/** `GET` / `PUT /ips` response (`IpsPolicyOut`). */
export interface IpsPolicyOut {
  rules: IpsRuleOut;
  compliance: ComplianceOut;
}

/**
 * `POST /ips/validate` request (`ValidateActionIn`, extra fields allowed). For
 * a TRANSACTION supply `type` + a symbol; for EXECUTION_PLAN supply `orders`.
 */
export interface ValidateActionIn {
  kind?: string;
  override?: boolean;
  type?: TransactionType;
  asset_symbol?: string;
  symbol?: string;
  ticker?: string;
  orders?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

// --------------------------------------------------------------------------- //
// Execution windows + plans (schemas/execution.py, models/execution.py) — §19.6 //
// --------------------------------------------------------------------------- //

export type ExecutionPlanKind = "DEPLOY" | "REBALANCE" | "DEPLOY_AND_REBALANCE";
export type ExecutionPlanStatus =
  | "DRAFT"
  | "APPROVED"
  | "EXECUTED"
  | "SKIPPED"
  | "EXPIRED";
/** Window classification (`execution.py`): "DEPLOYMENT" | "REBALANCE". */
export type WindowKind = "DEPLOYMENT" | "REBALANCE";

/** One order inside an execution plan (decoded from the plan's JSON column). */
export interface ExecutionOrder {
  symbol: string;
  side: string;
  quantity: number;
  unit_price_usd: number;
  est_amount_usd: number;
  est_amount_myr: number;
}

/** `POST /execution/plan` request (`ExecutionPlanIn`). */
export interface ExecutionPlanIn {
  prices: Record<string, number>;
  fx_rate: number;
  kind?: ExecutionPlanKind | null;
}

/** Optional body for `POST /execution/plans/{id}/approve`. */
export interface ExecutionPlanApproveIn {
  override?: boolean;
}

/** A stored execution plan with JSON columns decoded (`ExecutionPlanOut`). */
export interface ExecutionPlanOut {
  id: number;
  window_date: string;
  plan_kind: string;
  status: string;
  cash_deployed_myr: number;
  cash_deployed_usd: number;
  fx_rate_used: number | null;
  /** { symbol: weight_pct } before the plan executes. */
  allocation_before: Record<string, number>;
  /** { symbol: weight_pct } after the plan executes. */
  allocation_after: Record<string, number>;
  orders: ExecutionOrder[];
  steps: string[];
  ips_compliant: boolean;
  ips_violations: IpsViolationOut[];
  created_at: string;
  executed_at: string | null;
}

/** One upcoming execution window in the schedule preview (`WindowScheduleItem`). */
export interface WindowScheduleItem {
  open_date: string;
  kind: WindowKind;
}

/** The unified execution-window schedule (`WindowsOut`) — `GET /execution/windows`. */
export interface WindowsOut {
  today: string;
  open_window: boolean;
  open_window_kind: WindowKind | null;
  next_window_date: string;
  next_window_kind: WindowKind;
  is_rebalance: boolean;
  schedule: WindowScheduleItem[];
}

// --------------------------------------------------------------------------- //
// Health (api/v1/health.py)                                                     //
// --------------------------------------------------------------------------- //

/** `GET /health` response. */
export interface HealthOut {
  status: string;
  version: string;
  db: string;
}
