/**
 * Execution Center (DESIGN §20.4) — "the operating room", the ONLY decision
 * surface. It answers "What exactly do I need to do next?" within 5 seconds.
 *
 * The UI MIRRORS the backend decision engine (DESIGN §20.0): the Action Status,
 * the execution plan (orders / shares / amounts), the allocation correction,
 * the required cash transfer and the IPS verdict are ALL computed server-side
 * and rendered verbatim here. The page never generates, infers, recomputes or
 * implies an investment decision — it only renders engine output and triggers
 * backend operations (generate / approve / execute / skip). No free-form
 * "what if", no recommendations, no forbidden discovery/trading surfaces
 * (DESIGN §20.1).
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Banknote,
  CheckCircle2,
  ClipboardList,
  ListChecks,
  ShieldCheck,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";

import type { ApiError } from "@/api/client";
import { execution } from "@/api/endpoints";
import { AllocationBar, type AllocationSegment } from "@/components/charts/AllocationBar";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { ACTION_STATUS_META, CYCLE_STATE_LABELS } from "@/lib/constants";
import {
  fmtDate,
  fmtDateTime,
  fmtMYR,
  fmtScore,
  fmtShares,
  fmtUSD,
} from "@/lib/format";
import { queryKeys, queryRoots } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";
import { useActionStatus } from "@/hooks/useActionStatus";
import { useExecutionPlans, useExecutionWindows } from "@/hooks/useExecution";
import { usePricing } from "@/hooks/usePricing";
import { toast } from "@/stores/toast";
import type {
  ActionStatusOut,
  ActionStatusValue,
  ExecutionOrder,
  ExecutionPlanOut,
  IpsViolationOut,
  WealthCycleState,
  WindowKind,
  WindowsOut,
} from "@/types/api";

// --------------------------------------------------------------------------- //
// Helpers (display-only; no financial value is derived here, DESIGN §20.0)    //
// --------------------------------------------------------------------------- //

const PLAN_KIND_LABELS: Record<string, string> = {
  DEPLOY: "Deploy cash",
  REBALANCE: "Rebalance",
  DEPLOY_AND_REBALANCE: "Deploy & rebalance",
};

const PLAN_STATUS_TONE: Record<string, BadgeTone> = {
  DRAFT: "neutral",
  APPROVED: "accent",
  EXECUTED: "gain",
  SKIPPED: "neutral",
  EXPIRED: "warn",
};

const SIDE_TONE: Record<string, BadgeTone> = {
  BUY: "accent",
  SELL: "warn",
};

/** Human labels for execution-window kinds (never leak the raw enum, §20.0). */
const WINDOW_KIND_LABELS: Record<WindowKind, string> = {
  DEPLOYMENT: "Deployment",
  REBALANCE: "Rebalance",
};

function windowKindLabel(kind: WindowKind | null | undefined): string {
  if (kind == null) return "—";
  return WINDOW_KIND_LABELS[kind] ?? kind;
}

function cycleStateLabel(state: string): string {
  return CYCLE_STATE_LABELS[state as WealthCycleState] ?? state;
}

const SEG_TONES: BadgeTone[] = ["accent", "gain", "warn", "loss", "neutral"];

function planKindLabel(kind: string): string {
  return PLAN_KIND_LABELS[kind] ?? kind;
}

function sideTone(side: string): BadgeTone {
  return SIDE_TONE[side.toUpperCase()] ?? "neutral";
}

/** The most recent plan is the one the engine wants the user to act on. */
function latestPlan(plans: readonly ExecutionPlanOut[]): ExecutionPlanOut | null {
  if (plans.length === 0) return null;
  return plans.reduce((newest, plan) =>
    plan.created_at >= newest.created_at ? plan : newest,
  );
}

/** Stable tone per symbol so before/after bars colour-match a symbol. */
function buildToneMap(symbols: readonly string[]): Record<string, BadgeTone> {
  const map: Record<string, BadgeTone> = {};
  symbols.forEach((symbol, i) => {
    map[symbol] = SEG_TONES[i % SEG_TONES.length] ?? "neutral";
  });
  return map;
}

/** Backend allocation map ({ symbol: weight_pct }) → AllocationBar segments. */
function toSegments(
  allocation: Record<string, number>,
  tones: Record<string, BadgeTone>,
): AllocationSegment[] {
  return Object.entries(allocation).map(([label, weightPct]) => ({
    label,
    weightPct,
    tone: tones[label] ?? "neutral",
  }));
}

// --------------------------------------------------------------------------- //
// Action Status banner (compact mirror of the primary signal, DESIGN §20.3)   //
// --------------------------------------------------------------------------- //

function StatusBanner({ status }: { status: ActionStatusOut }) {
  const meta = ACTION_STATUS_META[status.status as ActionStatusValue];
  const Icon = meta.icon;
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-2xl border p-4",
        meta.surfaceClass,
      )}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", meta.accentClass)} aria-hidden />
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("text-sm font-semibold", meta.accentClass)}>
            {status.label}
          </span>
          <Badge tone={meta.tone} dot>
            {cycleStateLabel(status.cycle_state)}
          </Badge>
        </div>
        <p className="text-sm font-medium text-text">{status.headline}</p>
        <p className="text-sm text-muted">{status.primary_action}</p>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Window context — when the engine expects the next action (DESIGN §20.4)     //
// --------------------------------------------------------------------------- //

function WindowContext({ windows }: { windows: WindowsOut }) {
  return (
    <Card>
      <CardContent className="grid grid-cols-2 gap-4 p-4 pt-4 sm:p-5 sm:pt-5">
        <Stat
          label="Window"
          value={
            windows.open_window ? (
              <span className="flex items-center gap-2">
                <Badge tone="accent" dot>
                  Open
                </Badge>
              </span>
            ) : (
              <Badge tone="neutral" dot>
                Closed
              </Badge>
            )
          }
          sub={
            windows.open_window
              ? `${windowKindLabel(windows.open_window_kind)} window`
              : "No window open today"
          }
        />
        <Stat
          label="Next window"
          value={fmtDate(windows.next_window_date)}
          sub={`${windowKindLabel(windows.next_window_kind)}${windows.is_rebalance ? " · rebalance" : ""}`}
        />
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Orders — table on ≥sm, cards on mobile (DESIGN §20 mobile-first)            //
// --------------------------------------------------------------------------- //

function OrdersTable({ orders }: { orders: readonly ExecutionOrder[] }) {
  return (
    <div className="hidden overflow-hidden rounded-xl border border-border sm:block">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-surface2 text-left text-xs uppercase tracking-wide text-muted">
            <th className="px-3 py-2 font-medium">Symbol</th>
            <th className="px-3 py-2 font-medium">Side</th>
            <th className="px-3 py-2 text-right font-medium">Quantity</th>
            <th className="px-3 py-2 text-right font-medium">Est. USD</th>
            <th className="px-3 py-2 text-right font-medium">Est. MYR</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order, i) => (
            <tr
              key={`${order.symbol}-${order.side}-${i}`}
              className="border-b border-border last:border-0"
            >
              <td className="px-3 py-2 font-medium text-text">{order.symbol}</td>
              <td className="px-3 py-2">
                <Badge tone={sideTone(order.side)}>{order.side}</Badge>
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-text">
                {fmtShares(order.quantity)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-text">
                {fmtUSD(order.est_amount_usd)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-text">
                {fmtMYR(order.est_amount_myr)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrderCards({ orders }: { orders: readonly ExecutionOrder[] }) {
  return (
    <ul className="flex flex-col gap-2 sm:hidden">
      {orders.map((order, i) => (
        <li
          key={`${order.symbol}-${order.side}-${i}`}
          className="rounded-xl border border-border bg-surface2/40 p-3"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-text">{order.symbol}</span>
            <Badge tone={sideTone(order.side)}>{order.side}</Badge>
          </div>
          <dl className="mt-2 grid grid-cols-3 gap-2 text-xs">
            <div className="flex flex-col gap-0.5">
              <dt className="text-muted">Quantity</dt>
              <dd className="tabular-nums text-text">{fmtShares(order.quantity)}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-muted">Est. USD</dt>
              <dd className="tabular-nums text-text">{fmtUSD(order.est_amount_usd)}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-muted">Est. MYR</dt>
              <dd className="tabular-nums text-text">{fmtMYR(order.est_amount_myr)}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------- //
// Required cash transfer — prominent (derived server-side, DESIGN §20.4)      //
// --------------------------------------------------------------------------- //

function CashTransfer({ plan }: { plan: ExecutionPlanOut }) {
  const hasTransfer = plan.cash_deployed_myr > 0;
  return (
    <div
      className={cn(
        "rounded-2xl border p-4",
        hasTransfer ? "border-accent/50 bg-accent/10" : "border-border bg-surface2/40",
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
            hasTransfer ? "bg-accent/15 text-accent" : "bg-surface2 text-muted",
          )}
        >
          <Banknote className="h-5 w-5" aria-hidden />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">
            Required cash transfer
          </span>
          {hasTransfer ? (
            <>
              <p className="text-lg font-semibold leading-tight text-text">
                Transfer {fmtMYR(plan.cash_deployed_myr)} to Moomoo
              </p>
              <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
                <span>GXBank</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                <span>Moomoo</span>
                <span className="tabular-nums">
                  · {fmtUSD(plan.cash_deployed_usd)}
                </span>
                {plan.fx_rate_used != null ? (
                  <span className="tabular-nums">
                    · FX {plan.fx_rate_used.toFixed(4)}
                  </span>
                ) : null}
              </p>
            </>
          ) : (
            <p className="text-sm text-muted">
              No cash transfer required for this plan.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// IPS compliance for the plan (verbatim verdict, DESIGN §19.5)                //
// --------------------------------------------------------------------------- //

function PlanCompliance({
  compliant,
  violations,
}: {
  compliant: boolean;
  violations: readonly IpsViolationOut[];
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border p-4",
        compliant ? "border-gain/40 bg-gain/10" : "border-warn/40 bg-warn/10",
      )}
    >
      <div className="flex items-center gap-2">
        {compliant ? (
          <ShieldCheck className="h-5 w-5 shrink-0 text-gain" aria-hidden />
        ) : (
          <ShieldAlert className="h-5 w-5 shrink-0 text-warn" aria-hidden />
        )}
        <span className="text-sm font-semibold text-text">
          {compliant ? "IPS compliant" : "IPS violations"}
        </span>
      </div>
      {violations.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-2">
          {violations.map((v, i) => (
            <li
              key={`${v.rule_type}-${i}`}
              className="flex flex-col gap-1 rounded-xl border border-border bg-surface p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-text">{v.rule_type}</span>
                <Badge tone={v.level === "BLOCK" ? "loss" : "warn"}>{v.level}</Badge>
              </div>
              <p className="text-sm text-muted">{v.message}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted">
          This plan satisfies every active IPS rule.
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// The execution plan card (DESIGN §20.4 core)                                 //
// --------------------------------------------------------------------------- //

function PlanCard({ plan }: { plan: ExecutionPlanOut }) {
  const statusTone = PLAN_STATUS_TONE[plan.status] ?? "neutral";

  const toneMap = useMemo(() => {
    const symbols = Array.from(
      new Set([
        ...Object.keys(plan.allocation_before),
        ...Object.keys(plan.allocation_after),
      ]),
    );
    return buildToneMap(symbols);
  }, [plan.allocation_before, plan.allocation_after]);

  const beforeSegments = useMemo(
    () => toSegments(plan.allocation_before, toneMap),
    [plan.allocation_before, toneMap],
  );
  const afterSegments = useMemo(
    () => toSegments(plan.allocation_after, toneMap),
    [plan.allocation_after, toneMap],
  );

  const hasAllocation =
    beforeSegments.length > 0 || afterSegments.length > 0;

  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <ClipboardList className="h-4 w-4 text-muted" aria-hidden />
            Execution plan
          </CardTitle>
          <div className="flex items-center gap-2">
            <Badge tone="neutral">{planKindLabel(plan.plan_kind)}</Badge>
            <Badge tone={statusTone}>{plan.status}</Badge>
          </div>
        </div>
        <p className="text-xs text-muted">
          Window {fmtDate(plan.window_date)} · created {fmtDateTime(plan.created_at)}
          {plan.executed_at ? ` · executed ${fmtDateTime(plan.executed_at)}` : ""}
        </p>
      </CardHeader>

      <CardContent className="flex flex-col gap-5">
        {/* Cash transfer — most prominent action signal */}
        <CashTransfer plan={plan} />

        {/* Human steps from the engine */}
        {plan.steps.length > 0 ? (
          <section className="flex flex-col gap-2">
            <h4 className="flex items-center gap-2 text-sm font-semibold text-text">
              <ListChecks className="h-4 w-4 text-muted" aria-hidden />
              Steps
            </h4>
            <ol className="flex flex-col gap-2">
              {plan.steps.map((step, i) => (
                <li key={i} className="flex items-start gap-2.5 text-sm text-text">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface2 text-xs font-medium tabular-nums text-muted">
                    {i + 1}
                  </span>
                  <span className="min-w-0">{step}</span>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {/* Orders */}
        <section className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-text">Orders</h4>
          {plan.orders.length > 0 ? (
            <>
              <OrdersTable orders={plan.orders} />
              <OrderCards orders={plan.orders} />
            </>
          ) : (
            <p className="rounded-xl border border-dashed border-border bg-surface2/40 px-3 py-4 text-center text-sm text-muted">
              No orders — this plan moves cash only.
            </p>
          )}
        </section>

        {/* Rebalance details: allocation correction before → after */}
        {hasAllocation ? (
          <section className="flex flex-col gap-3">
            <h4 className="flex items-center gap-2 text-sm font-semibold text-text">
              <SlidersHorizontal className="h-4 w-4 text-muted" aria-hidden />
              Allocation correction
            </h4>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface2/40 p-3">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Before
                </span>
                {beforeSegments.length > 0 ? (
                  <AllocationBar segments={beforeSegments} />
                ) : (
                  <p className="text-sm text-muted">—</p>
                )}
              </div>
              <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface2/40 p-3">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  After
                </span>
                {afterSegments.length > 0 ? (
                  <AllocationBar segments={afterSegments} />
                ) : (
                  <p className="text-sm text-muted">—</p>
                )}
              </div>
            </div>
          </section>
        ) : null}

        {/* IPS compliance for the plan */}
        <PlanCompliance
          compliant={plan.ips_compliant}
          violations={plan.ips_violations}
        />
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Action bar — backend operations only (generate / approve / execute / skip)  //
// --------------------------------------------------------------------------- //

type ConfirmKind = "execute" | "skip" | null;

interface ActionBarProps {
  plan: ExecutionPlanOut | null;
  canGenerate: boolean;
  generateHint: string | null;
  onGenerate: () => void;
  onApprove: () => void;
  onExecute: () => void;
  onSkip: () => void;
  generating: boolean;
  approving: boolean;
  executing: boolean;
  skipping: boolean;
}

function ActionBar({
  plan,
  canGenerate,
  generateHint,
  onGenerate,
  onApprove,
  onExecute,
  onSkip,
  generating,
  approving,
  executing,
  skipping,
}: ActionBarProps) {
  // A plan is actionable only while it has not yet been executed/skipped/expired.
  const isOpenPlan = plan?.status === "DRAFT" || plan?.status === "APPROVED";
  const canApprove = plan?.status === "DRAFT";
  const canExecute = plan?.status === "APPROVED";
  const anyPending = generating || approving || executing || skipping;

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4 pt-4 sm:p-5 sm:pt-5">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Button
            variant="default"
            onClick={onGenerate}
            disabled={!canGenerate || anyPending}
            loading={generating}
            className="w-full"
          >
            {plan ? "Regenerate plan" : "Generate plan"}
          </Button>
          <Button
            variant="secondary"
            onClick={onApprove}
            disabled={!canApprove || anyPending}
            loading={approving}
            className="w-full"
          >
            Approve
          </Button>
          <Button
            variant="default"
            onClick={onExecute}
            disabled={!canExecute || anyPending}
            loading={executing}
            className="w-full"
          >
            Execute
          </Button>
          <Button
            variant="outline"
            onClick={onSkip}
            disabled={!isOpenPlan || anyPending}
            loading={skipping}
            className="w-full"
          >
            Skip
          </Button>
        </div>
        {!canGenerate && generateHint ? (
          <p className="text-xs text-muted">{generateHint}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Page                                                                         //
// --------------------------------------------------------------------------- //

export default function ExecutionCenter() {
  const queryClient = useQueryClient();
  const { pricing, valuationInput, hasPricing } = usePricing();

  const statusQuery = useActionStatus(pricing);
  const windowsQuery = useExecutionWindows();
  const plansQuery = useExecutionPlans();

  const plan = useMemo(
    () => (plansQuery.data ? latestPlan(plansQuery.data) : null),
    [plansQuery.data],
  );

  const [confirm, setConfirm] = useState<ConfirmKind>(null);

  /** Invalidate every query root a plan transition can affect. */
  function invalidateAll(): void {
    for (const roots of [
      queryRoots.execution,
      queryRoots.actionStatus,
      queryRoots.cycle,
      queryRoots.cash,
      // approve() creates/transitions a DeploymentIntent (QUEUED→PLANNED) for
      // the window, so the deployment queue cache must be refreshed too.
      queryRoots.deployment,
    ]) {
      void queryClient.invalidateQueries({ queryKey: roots });
    }
  }

  const generate = useMutation<ExecutionPlanOut, ApiError, void>({
    mutationFn: () => {
      if (!valuationInput) {
        throw new Error("Enter latest prices before generating a plan.");
      }
      return execution.generatePlan({
        prices: valuationInput.prices,
        fx_rate: valuationInput.fx_rate,
      });
    },
    onSuccess: (result) => {
      queryClient.setQueryData(queryKeys.execution.plans(undefined), (prev) =>
        Array.isArray(prev)
          ? [result, ...(prev as ExecutionPlanOut[])]
          : [result],
      );
      invalidateAll();
      toast.success("Plan generated", planKindLabel(result.plan_kind));
    },
    onError: (error) => toast.error("Could not generate plan", error.message),
  });

  const approve = useMutation<ExecutionPlanOut, ApiError, number>({
    mutationFn: (id) => execution.approvePlan(id),
    onSuccess: (result) => {
      invalidateAll();
      toast.success("Plan approved", `Plan #${result.id} is ready to execute`);
    },
    onError: (error) => toast.error("Could not approve plan", error.message),
  });

  const executePlan = useMutation<ExecutionPlanOut, ApiError, number>({
    mutationFn: (id) => execution.executePlan(id),
    onSuccess: (result) => {
      invalidateAll();
      toast.success(
        "Plan marked executed",
        `Plan #${result.id} — now record the broker trades & cash transfer`,
      );
    },
    onError: (error) => toast.error("Could not execute plan", error.message),
  });

  const skipPlan = useMutation<ExecutionPlanOut, ApiError, number>({
    mutationFn: (id) => execution.skipPlan(id),
    onSuccess: (result) => {
      invalidateAll();
      toast.warn("Plan skipped", `Plan #${result.id} was skipped`);
    },
    onError: (error) => toast.error("Could not skip plan", error.message),
  });

  function handleConfirm(): void {
    if (!plan) {
      setConfirm(null);
      return;
    }
    if (confirm === "execute") executePlan.mutate(plan.id);
    if (confirm === "skip") skipPlan.mutate(plan.id);
    setConfirm(null);
  }

  // Loading: first render with no cached data.
  const isInitialLoading =
    statusQuery.isLoading || windowsQuery.isLoading || plansQuery.isLoading;

  // The page cannot function without windows + plans; surface those errors.
  const fatalError = windowsQuery.error ?? plansQuery.error;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Execution Center"
        description="The single place to act. Every figure comes straight from the engine."
      />

      {isInitialLoading ? (
        <div className="flex flex-col gap-5">
          <Skeleton className="h-24 w-full rounded-2xl" />
          <Skeleton className="h-20 w-full rounded-2xl" />
          <Skeleton className="h-64 w-full rounded-2xl" />
        </div>
      ) : fatalError ? (
        <ErrorState
          message={fatalError.message}
          onRetry={() => {
            void windowsQuery.refetch();
            void plansQuery.refetch();
          }}
        />
      ) : (
        <>
          {/* Action Status — compact banner */}
          {statusQuery.data ? (
            <StatusBanner status={statusQuery.data} />
          ) : statusQuery.error ? (
            <ErrorState
              title="Action Status unavailable"
              message={statusQuery.error.message}
              onRetry={() => void statusQuery.refetch()}
            />
          ) : null}

          {/* Window context */}
          {windowsQuery.data ? <WindowContext windows={windowsQuery.data} /> : null}

          {/* Pricing affordance — never fabricate prices (DESIGN §20.1) */}
          {!hasPricing ? (
            <Card className="border-warn/40 bg-warn/5">
              <CardContent className="p-4 pt-4 sm:p-5 sm:pt-5">
                <p className="text-sm font-medium text-text">
                  Enter latest prices to generate a plan
                </p>
                <p className="mt-1 text-sm text-muted">
                  Set your broker&apos;s latest USD prices and the USD&rarr;MYR rate
                  in Settings. Without them the engine cannot value holdings or
                  build an execution plan.
                </p>
              </CardContent>
            </Card>
          ) : null}

          {/* The plan, or a calm empty state */}
          {plan ? (
            <PlanCard plan={plan} />
          ) : (
            <EmptyState
              icon={CheckCircle2}
              title="No execution plan yet"
              description={
                hasPricing
                  ? "Generate a plan to see exactly what to do next. The engine decides the orders — you only execute."
                  : "Enter latest prices, then generate a plan to see exactly what to do next."
              }
            />
          )}

          {/* Compliance score footnote from the Action Status signal */}
          {statusQuery.data ? (
            <p className="text-center text-xs text-muted">
              IPS compliance score {fmtScore(statusQuery.data.compliance_score)}
            </p>
          ) : null}

          {/* Actions */}
          <ActionBar
            plan={plan}
            canGenerate={hasPricing}
            generateHint={
              hasPricing ? null : "Enter latest prices in Settings to enable plan generation."
            }
            onGenerate={() => generate.mutate()}
            onApprove={() => {
              if (plan) approve.mutate(plan.id);
            }}
            onExecute={() => setConfirm("execute")}
            onSkip={() => setConfirm("skip")}
            generating={generate.isPending}
            approving={approve.isPending}
            executing={executePlan.isPending}
            skipping={skipPlan.isPending}
          />
        </>
      )}

      <ConfirmDialog
        open={confirm === "execute"}
        title="Mark this plan executed?"
        description="This marks the plan as executed. Place the orders with your broker, then record the resulting transactions and the cash transfer yourself (Transactions / Cash Buffer). This cannot be undone."
        confirmLabel="Mark executed"
        confirmVariant="default"
        loading={executePlan.isPending}
        onConfirm={handleConfirm}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "skip"}
        title="Skip this plan?"
        description="The plan will be marked skipped and no transactions are recorded."
        confirmLabel="Skip plan"
        confirmVariant="outline"
        loading={skipPlan.isPending}
        onConfirm={handleConfirm}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
}
