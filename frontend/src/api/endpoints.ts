/**
 * One typed function per backend endpoint (DESIGN §8, §19.7), grouped by
 * domain. Components and hooks call these — never `fetch` or `apiClient`
 * directly. Paths, methods and query params mirror `backend/app/api/v1/*.py`
 * EXACTLY. Money/shares arrive as numbers (DESIGN §5); nothing is recomputed.
 */

import { apiClient } from "@/api/client";
import type {
  ActionStatusOut,
  BehaviorReportOut,
  CashAccountIn,
  CashAccountOut,
  CashAccountUpdate,
  CashMovementIn,
  CashMovementOut,
  CashMovementUpdate,
  CashSummaryOut,
  ComplianceOut,
  CycleStateOut,
  DeploymentExecuteIn,
  DeploymentIntentIn,
  DeploymentIntentOut,
  DeploymentStatus,
  EnforcementVerdictOut,
  ExecutionPlanApproveIn,
  ExecutionPlanIn,
  ExecutionPlanOut,
  ExecutionPlanStatus,
  HealthOut,
  IpsPolicyOut,
  IpsRuleIn,
  LoginIn,
  LoginOut,
  NetWorthBreakdownOut,
  NetWorthQueryIn,
  NetWorthSummaryOut,
  OkOut,
  Paginated,
  PasswordResetConfirmIn,
  PasswordResetRequestIn,
  RebalanceIn,
  RebalanceOut,
  TransactionIn,
  TransactionOut,
  TransactionType,
  TransactionUpdate,
  TransactionWithWarningsOut,
  UserOut,
  ValidateActionIn,
  ValuationIn,
  ValuationOut,
  WindowsOut,
} from "@/types/api";

/** Repeated `price=SYMBOL:USD` query params used by several read endpoints. */
export interface PricingQuery {
  /** USD price per held/target symbol, e.g. { VOO: 500.25, QQQ: 480.1 }. */
  prices?: Record<string, number>;
  /** USD->MYR rate. */
  fx_rate?: number;
  as_of?: string;
}

/**
 * The simple Record query path in the client cannot emit repeated keys, so
 * pricing-aware GETs build the search string directly. This produces a
 * fully-formed path with repeated price=SYMBOL:VALUE params, matching the
 * backend _prices_from_query parsing.
 */
function withPricingPath(base: string, pricing?: PricingQuery): string {
  if (!pricing) return base;
  const params = new URLSearchParams();
  if (pricing.fx_rate !== undefined) {
    params.append("fx_rate", String(pricing.fx_rate));
  }
  if (pricing.as_of !== undefined) params.append("as_of", pricing.as_of);
  if (pricing.prices) {
    for (const [symbol, value] of Object.entries(pricing.prices)) {
      params.append("price", `${symbol}:${value}`);
    }
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

// --------------------------------------------------------------------------- //
// Auth                                                                         //
// --------------------------------------------------------------------------- //
export const auth = {
  login: (payload: LoginIn) => apiClient.post<LoginOut>("/auth/login", payload),
  refresh: () => apiClient.post<LoginOut>("/auth/refresh"),
  logout: () => apiClient.post<OkOut>("/auth/logout"),
  me: (signal?: AbortSignal) => apiClient.get<UserOut>("/auth/me", undefined, signal),
  passwordResetRequest: (payload: PasswordResetRequestIn) =>
    apiClient.post<OkOut>("/auth/password-reset/request", payload),
  passwordResetConfirm: (payload: PasswordResetConfirmIn) =>
    apiClient.post<OkOut>("/auth/password-reset/confirm", payload),
} as const;

// --------------------------------------------------------------------------- //
// Transactions                                                                 //
// --------------------------------------------------------------------------- //
// Type aliases (not interfaces) so they satisfy the client's `Record<string,…>`
// query parameter type via TypeScript's implicit index signature.
export type TransactionListParams = {
  type?: TransactionType;
  symbol?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
  sort?: "transaction_date" | "total_amount_myr" | "asset_symbol" | "transaction_type";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export const transactions = {
  list: (params?: TransactionListParams) =>
    apiClient.get<Paginated<TransactionOut>>("/transactions", params),
  get: (id: number) => apiClient.get<TransactionOut>(`/transactions/${id}`),
  create: (payload: TransactionIn, override = false) =>
    apiClient.post<TransactionWithWarningsOut>(
      "/transactions",
      payload,
      override ? { override: true } : undefined,
    ),
  update: (id: number, payload: TransactionUpdate, override = false) =>
    apiClient.patch<TransactionOut>(
      `/transactions/${id}`,
      payload,
      override ? { override: true } : undefined,
    ),
  remove: (id: number) => apiClient.delete<void>(`/transactions/${id}`),
} as const;

// --------------------------------------------------------------------------- //
// Portfolio (valuation + rebalance)                                            //
// --------------------------------------------------------------------------- //
export const portfolio = {
  valuation: (payload: ValuationIn) =>
    apiClient.post<ValuationOut>("/portfolio/valuation", payload),
  rebalance: (payload: RebalanceIn) =>
    apiClient.post<RebalanceOut>("/portfolio/rebalance", payload),
} as const;

// --------------------------------------------------------------------------- //
// Behavior report                                                              //
// --------------------------------------------------------------------------- //
export type BehaviorParams = {
  fx_rate?: number;
  voo_price?: number;
  qqq_price?: number;
};

export const behavior = {
  report: (params?: BehaviorParams) =>
    apiClient.get<BehaviorReportOut>("/analytics/behavior", params),
} as const;

// --------------------------------------------------------------------------- //
// Cash buffer system                                                           //
// --------------------------------------------------------------------------- //
export type CashMovementListParams = {
  account_id?: number;
  date_from?: string;
  date_to?: string;
};

export const cash = {
  listAccounts: (includeArchived = false) =>
    apiClient.get<CashAccountOut[]>(
      "/cash/accounts",
      includeArchived ? { include_archived: true } : undefined,
    ),
  createAccount: (payload: CashAccountIn) =>
    apiClient.post<CashAccountOut>("/cash/accounts", payload),
  updateAccount: (id: number, payload: CashAccountUpdate) =>
    apiClient.patch<CashAccountOut>(`/cash/accounts/${id}`, payload),
  archiveAccount: (id: number) =>
    apiClient.delete<CashAccountOut>(`/cash/accounts/${id}`),
  listMovements: (params?: CashMovementListParams) =>
    apiClient.get<CashMovementOut[]>("/cash/movements", params),
  createMovement: (payload: CashMovementIn) =>
    apiClient.post<CashMovementOut>("/cash/movements", payload),
  updateMovement: (id: number, payload: CashMovementUpdate) =>
    apiClient.patch<CashMovementOut>(`/cash/movements/${id}`, payload),
  removeMovement: (id: number) =>
    apiClient.delete<void>(`/cash/movements/${id}`),
  summary: (asOf?: string) =>
    apiClient.get<CashSummaryOut>(
      "/cash/summary",
      asOf ? { as_of: asOf } : undefined,
    ),
} as const;

// --------------------------------------------------------------------------- //
// Deployment queue                                                             //
// --------------------------------------------------------------------------- //
export const deployment = {
  queue: (status?: DeploymentStatus) =>
    apiClient.get<DeploymentIntentOut[]>(
      "/deployment/queue",
      status ? { status } : undefined,
    ),
  enqueue: (payload: DeploymentIntentIn) =>
    apiClient.post<DeploymentIntentOut>("/deployment/queue", payload),
  cancel: (id: number) =>
    apiClient.post<DeploymentIntentOut>(`/deployment/${id}/cancel`),
  execute: (id: number, payload?: DeploymentExecuteIn) =>
    apiClient.post<DeploymentIntentOut>(`/deployment/${id}/execute`, payload),
} as const;

// --------------------------------------------------------------------------- //
// Net Worth                                                                    //
// --------------------------------------------------------------------------- //
export const networth = {
  summary: (pricing?: PricingQuery) =>
    apiClient.get<NetWorthSummaryOut>(withPricingPath("/networth/summary", pricing)),
  summaryWithBody: (payload: NetWorthQueryIn) =>
    apiClient.post<NetWorthSummaryOut>("/networth/summary", payload),
  breakdown: (pricing?: PricingQuery) =>
    apiClient.get<NetWorthBreakdownOut>(
      withPricingPath("/networth/breakdown", pricing),
    ),
} as const;

// --------------------------------------------------------------------------- //
// Cycle                                                                        //
// --------------------------------------------------------------------------- //
export const cycle = {
  state: (pricing?: PricingQuery) =>
    apiClient.get<CycleStateOut>(withPricingPath("/cycle/state", pricing)),
} as const;

// --------------------------------------------------------------------------- //
// Action Status — the primary dashboard signal (§19.3)                         //
// --------------------------------------------------------------------------- //
export const actionStatus = {
  get: (pricing?: PricingQuery) =>
    apiClient.get<ActionStatusOut>(withPricingPath("/action-status", pricing)),
} as const;

// --------------------------------------------------------------------------- //
// IPS policy + enforcement                                                     //
// --------------------------------------------------------------------------- //
export const ips = {
  get: (pricing?: PricingQuery) =>
    apiClient.get<IpsPolicyOut>(withPricingPath("/ips", pricing)),
  update: (payload: IpsRuleIn) => apiClient.put<IpsPolicyOut>("/ips", payload),
  validate: (payload: ValidateActionIn) =>
    apiClient.post<EnforcementVerdictOut>("/ips/validate", payload),
  compliance: (pricing?: PricingQuery) =>
    apiClient.get<ComplianceOut>(withPricingPath("/ips/compliance", pricing)),
} as const;

// --------------------------------------------------------------------------- //
// Execution windows + plans (§19.6)                                            //
// --------------------------------------------------------------------------- //
export const execution = {
  windows: (asOf?: string) =>
    apiClient.get<WindowsOut>("/execution/windows", asOf ? { as_of: asOf } : undefined),
  generatePlan: (payload: ExecutionPlanIn) =>
    apiClient.post<ExecutionPlanOut>("/execution/plan", payload),
  listPlans: (status?: ExecutionPlanStatus) =>
    apiClient.get<ExecutionPlanOut[]>(
      "/execution/plans",
      status ? { status } : undefined,
    ),
  getPlan: (id: number) => apiClient.get<ExecutionPlanOut>(`/execution/plans/${id}`),
  approvePlan: (id: number, payload?: ExecutionPlanApproveIn) =>
    apiClient.post<ExecutionPlanOut>(`/execution/plans/${id}/approve`, payload),
  executePlan: (id: number) =>
    apiClient.post<ExecutionPlanOut>(`/execution/plans/${id}/execute`),
  skipPlan: (id: number) =>
    apiClient.post<ExecutionPlanOut>(`/execution/plans/${id}/skip`),
} as const;

// --------------------------------------------------------------------------- //
// Health                                                                       //
// --------------------------------------------------------------------------- //
export const health = {
  check: () => apiClient.get<HealthOut>("/health"),
} as const;

/** Aggregated endpoint namespace for ergonomic imports. */
export const api = {
  auth,
  transactions,
  portfolio,
  behavior,
  cash,
  deployment,
  networth,
  cycle,
  actionStatus,
  ips,
  execution,
  health,
} as const;
