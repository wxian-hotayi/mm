/**
 * Dashboard — the behavior-first home (DESIGN §20.3). STRICT vertical priority:
 *   (1) Action Status hero  (2) Net Worth  (3) Cash Buffer  (4) Execution Plan
 *   (5) Cycle State  (6) Portfolio Snapshot (informational, smallest, last).
 *
 * Every figure is rendered verbatim from the backend (DESIGN §20.0); the UI
 * never generates, infers or implies an investment decision. Phase 3 has no
 * market feed: pricing comes from the manual prices store (usePricing); when
 * unset, pricing-aware widgets show a calm "enter prices" affordance instead of
 * fabricating values. No forbidden surfaces (§20.1).
 */

import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, PiggyBank, Wallet } from "lucide-react";

import { AllocationBar, type AllocationSegment } from "@/components/charts/AllocationBar";
import { ActionStatusCard } from "@/components/dashboard/ActionStatusCard";
import { PricingPrompt } from "@/components/dashboard/PricingPrompt";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import type { ApiError } from "@/api/client";
import { ips } from "@/api/endpoints";
import { useActionStatus } from "@/hooks/useActionStatus";
import { useCashSummary } from "@/hooks/useCashBuffer";
import { useCycleState } from "@/hooks/useCycle";
import { useExecutionPlans } from "@/hooks/useExecution";
import { useNetWorthSummary } from "@/hooks/useNetWorth";
import { usePortfolioValuation } from "@/hooks/usePortfolio";
import { usePricing } from "@/hooks/usePricing";
import {
  CYCLE_STATE_LABELS,
  READINESS_LABELS,
  type ToneKey,
} from "@/lib/constants";
import { fmtMYR, fmtPct, fmtPp, fmtShares, fmtUSD, gainClass } from "@/lib/format";
import { queryKeys } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";
import type {
  ExecutionPlanOut,
  IpsPolicyOut,
  NetWorthChangeOut,
  ReadinessState,
  WealthCycleState,
} from "@/types/api";

/** Short, plain-language meaning for each wealth-cycle state (§19.2). */
const CYCLE_STATE_HINTS: Record<WealthCycleState, string> = {
  ACCUMULATION: "Keep building your cash buffer — there is nothing to deploy yet.",
  READY_TO_DEPLOY: "Your surplus has reached the threshold and is ready to invest.",
  DEPLOYMENT: "A deployment window is open — review the execution plan.",
  REBALANCE_WINDOW: "A rebalance window is open — review the execution plan.",
};

const CYCLE_STATE_TONE: Record<WealthCycleState, ToneKey> = {
  ACCUMULATION: "neutral",
  READY_TO_DEPLOY: "accent",
  DEPLOYMENT: "accent",
  REBALANCE_WINDOW: "warn",
};

/** Plans the Dashboard surfaces as "actionable" (§20.3 #4). */
const ACTIONABLE_PLAN_STATUSES = new Set(["DRAFT", "APPROVED"]);

function SectionSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { hasPricing, pricing, valuationInput } = usePricing();

  const actionStatus = useActionStatus(pricing);
  const netWorth = useNetWorthSummary(pricing);
  const cash = useCashSummary();
  const plans = useExecutionPlans();
  const cycle = useCycleState(pricing);
  const valuation = usePortfolioValuation(valuationInput);
  const policy = useQuery<IpsPolicyOut, ApiError>({
    queryKey: queryKeys.ips.policy(),
    queryFn: () => ips.get(),
    staleTime: 60_000,
  });

  const actionablePlan = (plans.data ?? []).find((plan) =>
    ACTIONABLE_PLAN_STATUSES.has(plan.status),
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Home"
        description="Your one signal, then the facts behind it."
      />

      {/* (1) ACTION STATUS — hero, full width, above the fold */}
      {actionStatus.isLoading ? (
        <Card className="p-5 sm:p-7">
          <SectionSkeleton lines={4} />
        </Card>
      ) : actionStatus.isError ? (
        <ErrorState
          title="Couldn't load your action status"
          message={actionStatus.error.message}
          onRetry={() => void actionStatus.refetch()}
        />
      ) : actionStatus.data ? (
        <ActionStatusCard data={actionStatus.data} />
      ) : null}

      {/* Secondary widgets: single column on mobile, 2-col from lg */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* (2) NET WORTH */}
        <Card>
          <CardHeader>
            <CardTitle>Net Worth</CardTitle>
            <CardDescription>Your total wealth across all accounts.</CardDescription>
          </CardHeader>
          <CardContent>
            {netWorth.isLoading ? (
              <SectionSkeleton />
            ) : netWorth.isError ? (
              <ErrorState
                message={netWorth.error.message}
                onRetry={() => void netWorth.refetch()}
              />
            ) : netWorth.data ? (
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted">
                    Total Net Worth
                  </span>
                  <span className="text-3xl font-bold tabular-nums text-text">
                    {fmtMYR(netWorth.data.total_net_worth_myr)}
                  </span>
                  <NetWorthChange change={netWorth.data.change_1m} />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Stat
                    label="Portfolio"
                    value={fmtMYR(netWorth.data.investment_myr)}
                    sub={
                      netWorth.data.portfolio.priced
                        ? `${fmtUSD(netWorth.data.portfolio.nav_usd)} USD`
                        : "Prices not set"
                    }
                  />
                  <Stat label="Cash" value={fmtMYR(netWorth.data.cash_myr)} />
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {/* (3) CASH BUFFER */}
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-2">
            <div className="flex flex-col gap-1">
              <CardTitle>Cash Buffer</CardTitle>
              <CardDescription>Your operational cash and deployable surplus.</CardDescription>
            </div>
            {cash.data ? (
              <ReadinessBadge readiness={cash.data.readiness as ReadinessState} />
            ) : null}
          </CardHeader>
          <CardContent>
            {cash.isLoading ? (
              <SectionSkeleton />
            ) : cash.isError ? (
              <ErrorState
                message={cash.error.message}
                onRetry={() => void cash.refetch()}
              />
            ) : cash.data ? (
              <div className="flex flex-col gap-4">
                <div className="rounded-xl border border-gain/30 bg-gain/10 px-4 py-3">
                  <span className="block text-xs font-semibold uppercase tracking-wide text-muted">
                    Deployable Surplus
                  </span>
                  <span className="mt-0.5 block text-2xl font-bold tabular-nums text-gain">
                    {fmtMYR(cash.data.deployable_surplus_myr)}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Stat label="Total Cash" value={fmtMYR(cash.data.total_cash_myr)} />
                  <Stat
                    label="Buffer Fill"
                    value={
                      cash.data.buffer_fill_ratio === null
                        ? "—"
                        : `${Math.round(cash.data.buffer_fill_ratio * 100)}%`
                    }
                  />
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {/* (4) EXECUTION PLAN — §20.3 #4: rendered ONLY when a plan exists.
            While loading/erroring we keep the section visible; once settled with
            no actionable plan, the card is omitted entirely to keep the
            behavior-first surface minimal. */}
        {plans.isLoading || plans.isError || actionablePlan ? (
          <Card>
            <CardHeader>
              <CardTitle>Execution Plan</CardTitle>
              <CardDescription>
                The latest plan from the Execution Center.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {plans.isLoading ? (
                <SectionSkeleton />
              ) : plans.isError ? (
                <ErrorState
                  message={plans.error.message}
                  onRetry={() => void plans.refetch()}
                />
              ) : actionablePlan ? (
                <ExecutionPlanSummary plan={actionablePlan} />
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        {/* (5) CYCLE STATE */}
        <Card>
          <CardHeader>
            <CardTitle>Wealth Cycle</CardTitle>
            <CardDescription>Where you are in the operating cycle.</CardDescription>
          </CardHeader>
          <CardContent>
            {cycle.isLoading ? (
              <SectionSkeleton lines={2} />
            ) : cycle.isError ? (
              <ErrorState
                message={cycle.error.message}
                onRetry={() => void cycle.refetch()}
              />
            ) : cycle.data ? (
              <CycleStateSummary state={cycle.data.state} />
            ) : null}
          </CardContent>
        </Card>
      </div>

      {/* (6) PORTFOLIO SNAPSHOT — informational only, smallest, LAST */}
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2">
          <div className="flex flex-col gap-1">
            <CardTitle>Portfolio Snapshot</CardTitle>
            <CardDescription>Allocation and drift — for information only.</CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/portfolio")}
            aria-label="Open portfolio"
          >
            View
            <ArrowRight className="h-4 w-4" aria-hidden />
          </Button>
        </CardHeader>
        <CardContent>
          {!hasPricing ? (
            <PricingPrompt description="Set your latest prices to see current allocation and drift." />
          ) : valuation.isLoading || policy.isLoading ? (
            <SectionSkeleton />
          ) : valuation.isError ? (
            <ErrorState
              message={valuation.error.message}
              onRetry={() => void valuation.refetch()}
            />
          ) : valuation.data ? (
            <PortfolioSnapshot
              segments={buildAllocationSegments(
                valuation.data.holdings,
                valuation.data.cash_weight_pct,
                policy.data?.rules.target_weights,
              )}
              maxDriftPp={driftFromActionStatus(actionStatus.data?.signals.max_drift_pp)}
            />
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * §20.3 #2: the Net Worth total is shown with its period change (1-month).
 * Both figures come straight from the API; `null` means there is not yet enough
 * history to compute a change, which we surface calmly rather than fabricating.
 */
function NetWorthChange({ change }: { change: NetWorthChangeOut | null }) {
  if (change === null) {
    return <span className="text-xs text-muted">No prior month to compare yet</span>;
  }
  return (
    <span className={cn("text-sm font-medium tabular-nums", gainClass(change.abs_myr))}>
      {fmtMYR(change.abs_myr)}
      {change.pct !== null ? ` · ${fmtPct(change.pct)}` : ""}
      <span className="ml-1 text-xs font-normal text-muted">past month</span>
    </span>
  );
}

function ReadinessBadge({ readiness }: { readiness: ReadinessState }) {
  const tone: ToneKey = readiness === "READY" ? "gain" : "neutral";
  return (
    <Badge tone={tone} dot>
      {READINESS_LABELS[readiness]}
    </Badge>
  );
}

function CycleStateSummary({ state }: { state: string }) {
  const known = state as WealthCycleState;
  const label = CYCLE_STATE_LABELS[known] ?? state;
  const hint = CYCLE_STATE_HINTS[known];
  const tone = CYCLE_STATE_TONE[known] ?? "neutral";
  return (
    <div className="flex flex-col gap-2">
      <Badge tone={tone} dot className="w-fit">
        {label}
      </Badge>
      {hint ? <p className="text-sm text-muted">{hint}</p> : null}
    </div>
  );
}

function ExecutionPlanSummary({ plan }: { plan: ExecutionPlanOut }) {
  const navigate = useNavigate();
  const steps = plan.steps.length > 0 ? plan.steps : deriveSteps(plan);
  const statusTone: ToneKey = plan.status === "APPROVED" ? "accent" : "neutral";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Badge tone="accent">{plan.plan_kind}</Badge>
        <Badge tone={statusTone}>{plan.status}</Badge>
      </div>
      {steps.length > 0 ? (
        <ol className="flex flex-col gap-1.5">
          {steps.map((step, i) => (
            <li key={`${i}-${step}`} className="flex gap-2 text-sm text-text">
              <span className="text-muted tabular-nums">{i + 1}.</span>
              <span className="leading-snug">{step}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted">Plan ready — open the Execution Center for details.</p>
      )}
      <Button
        variant="outline"
        size="md"
        className="w-full"
        onClick={() => navigate("/execution")}
      >
        Open Execution Center
        <ArrowRight className="h-4 w-4" aria-hidden />
      </Button>
    </div>
  );
}

function PortfolioSnapshot({
  segments,
  maxDriftPp,
}: {
  segments: readonly AllocationSegment[];
  maxDriftPp: number | null;
}) {
  if (segments.length === 0) {
    return (
      <EmptyHoldings />
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <AllocationBar segments={segments} />
      <div className="flex items-center justify-between border-t border-border pt-3 text-sm">
        <span className="text-muted">Max drift from target</span>
        <span className="font-medium tabular-nums text-text">{fmtPp(maxDriftPp)}</span>
      </div>
    </div>
  );
}

function EmptyHoldings() {
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-border bg-surface/50 px-6 py-8 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-surface2 text-muted">
        <PiggyBank className="h-5 w-5" aria-hidden />
      </div>
      <p className="text-sm text-muted">No holdings yet — record your first buy to begin.</p>
      <Button variant="outline" size="sm" onClick={() => navigate("/portfolio")}>
        <Wallet className="h-4 w-4" aria-hidden />
        View portfolio
      </Button>
    </div>
  );
}

/**
 * Map priced holdings + cash into AllocationBar segments, attaching the IPS
 * target weight (0–1 → 0–100) per symbol when a policy is loaded. All weights
 * come straight from the backend; nothing is recomputed here.
 */
function buildAllocationSegments(
  holdings: ReadonlyArray<{ symbol: string; weight_pct: number | null }>,
  cashWeightPct: number | null,
  targetWeights: Record<string, number> | undefined,
): AllocationSegment[] {
  const segments: AllocationSegment[] = [];
  for (const holding of holdings) {
    if (holding.weight_pct === null) continue;
    const target = targetWeights?.[holding.symbol];
    segments.push({
      label: holding.symbol,
      weightPct: holding.weight_pct,
      ...(target !== undefined ? { targetPct: target * 100 } : {}),
    });
  }
  if (cashWeightPct !== null && cashWeightPct > 0) {
    segments.push({ label: "Cash", weightPct: cashWeightPct, tone: "neutral" });
  }
  return segments;
}

/** Parse the Action Status `max_drift_pp` signal (string|null) into a number. */
function driftFromActionStatus(raw: string | null | undefined): number | null {
  if (raw === null || raw === undefined) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Fallback human steps from a plan's orders when `steps` is empty. */
function deriveSteps(plan: ExecutionPlanOut): string[] {
  return plan.orders.map(
    (order) =>
      `${order.side} ${fmtShares(order.quantity)} ${order.symbol} (${fmtMYR(order.est_amount_myr)})`,
  );
}
