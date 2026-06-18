/**
 * Portfolio — STRICTLY READ-ONLY (DESIGN §20.5, §20.0).
 *
 * Mirrors the backend valuation engine: holdings, allocation (current vs the
 * IPS target, e.g. 70/30), per-symbol drift shown as a *fact* (within / beyond
 * policy), cost basis, and NAV. It renders backend figures VERBATIM and never
 * recomputes a financial value, never recommends, suggests, or hints at any
 * buy/sell. Drift is informational only — the *decision* about it lives solely
 * in Action Status / the Execution Center.
 *
 * Phase 3 has no market feed: prices + USD->MYR rate are entered manually via a
 * Dialog (DESIGN §20.1 manual entry, NOT a quotes feed). Until prices are set,
 * holdings cannot be valued and the page shows a calm "enter prices" affordance.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PiggyBank, RefreshCw } from "lucide-react";

import type { ApiError } from "@/api/client";
import { ips as ipsApi } from "@/api/endpoints";
import { AllocationBar, type AllocationSegment } from "@/components/charts/AllocationBar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { usePricing } from "@/hooks/usePricing";
import { usePortfolioValuation } from "@/hooks/usePortfolio";
import { queryKeys } from "@/lib/queryKeys";
import {
  fmtDateTime,
  fmtMYR,
  fmtPct,
  fmtPp,
  fmtShares,
  fmtUSD,
  gainClass,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { HoldingOut, IpsPolicyOut, ValuationOut } from "@/types/api";

//--------------------------------------------------------------------------- //
// Manual price entry                                                           //
// --------------------------------------------------------------------------- //

interface PriceDialogProps {
  open: boolean;
  onClose: () => void;
  initialPrices: Record<string, number>;
  initialFxRate: number | null;
  onSubmit: (prices: Record<string, number>, fxRate: number) => void;
}

/** Returns a finite, positive number parsed from an input, else null. */
function parsePositive(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const num = Number(trimmed);
  return Number.isFinite(num) && num > 0 ? num : null;
}

function PriceDialog({
  open,
  onClose,
  initialPrices,
  initialFxRate,
  onSubmit,
}: PriceDialogProps) {
  const [voo, setVoo] = useState<string>("");
  const [qqq, setQqq] = useState<string>("");
  const [fx, setFx] = useState<string>("");
  const [touched, setTouched] = useState<boolean>(false);

  // Seed the inputs from the current store values each time the dialog opens.
  const [seededFor, setSeededFor] = useState<boolean>(false);
  if (open && !seededFor) {
    const vooVal = initialPrices.VOO;
    const qqqVal = initialPrices.QQQ;
    setVoo(vooVal != null ? String(vooVal) : "");
    setQqq(qqqVal != null ? String(qqqVal) : "");
    setFx(initialFxRate != null ? String(initialFxRate) : "");
    setTouched(false);
    setSeededFor(true);
  }
  if (!open && seededFor) setSeededFor(false);

  const vooNum = parsePositive(voo);
  const qqqNum = parsePositive(qqq);
  const fxNum = parsePositive(fx);
  const valid = vooNum !== null && qqqNum !== null && fxNum !== null;

  const handleSubmit = (): void => {
    setTouched(true);
    if (vooNum === null || qqqNum === null || fxNum === null) return;
    onSubmit({ VOO: vooNum, QQQ: qqqNum }, fxNum);
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Update market prices"
      description="Enter your broker's latest prices and FX rate. This is a manual entry — WealthOS has no live quotes feed."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!valid && touched}>
            Save prices
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="price-voo">VOO price (USD)</Label>
          <Input
            id="price-voo"
            inputMode="decimal"
            placeholder="e.g. 500.25"
            value={voo}
            invalid={touched && vooNum === null}
            onChange={(e) => setVoo(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="price-qqq">QQQ price (USD)</Label>
          <Input
            id="price-qqq"
            inputMode="decimal"
            placeholder="e.g. 480.10"
            value={qqq}
            invalid={touched && qqqNum === null}
            onChange={(e) => setQqq(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="price-fx">USD &rarr; MYR rate</Label>
          <Input
            id="price-fx"
            inputMode="decimal"
            placeholder="e.g. 4.7150"
            value={fx}
            invalid={touched && fxNum === null}
            onChange={(e) => setFx(e.target.value)}
          />
        </div>
        {touched && !valid ? (
          <p className="text-sm text-loss">
            Enter a positive number for every field.
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}

// --------------------------------------------------------------------------- //
// Drift (informational, never an action prompt — DESIGN §20.5)                 //
// --------------------------------------------------------------------------- //

interface DriftRow {
  symbol: string;
  /** Current weight on the 0–100 scale (from HoldingOut.weight_pct). */
  currentPct: number;
  /** Target weight on the 0–100 scale (from the IPS policy / fallback). */
  targetPct: number;
  /** current − target, in percentage points (a comparison of two API figures). */
  driftPp: number;
  /** Whether the gap is beyond the IPS drift threshold. */
  beyond: boolean;
}

/** A calm bar that visualizes a drift figure as fact (no call-to-action). */
function DriftFigure({ row }: { row: DriftRow }) {
  // Scale the bar against the policy threshold so "beyond" reads at a glance;
  // purely a display transform of backend-supplied weights + threshold.
  const magnitude = Math.abs(row.driftPp);
  const tone = row.beyond ? "warn" : "gain";
  const fillClass = row.beyond ? "bg-warn" : "bg-gain";
  const widthPct = Math.min(100, (magnitude / 10) * 100);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-text">{row.symbol}</span>
        <div className="flex items-center gap-2">
          <span className={cn("text-sm tabular-nums", gainClass(row.driftPp))}>
            {fmtPp(row.driftPp)}
          </span>
          <Badge tone={tone}>{row.beyond ? "Beyond policy" : "Within policy"}</Badge>
        </div>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-surface2"
        role="img"
        aria-label={`${row.symbol} drift ${fmtPp(row.driftPp)}, ${
          row.beyond ? "beyond" : "within"
        } policy`}
      >
        <div
          className={cn("h-full rounded-full", fillClass)}
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <p className="text-xs text-muted tabular-nums">
        Current {row.currentPct.toFixed(1)}% &middot; Target {row.targetPct.toFixed(0)}%
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Holdings — table on lg, cards on mobile                                      //
// --------------------------------------------------------------------------- //

function HoldingCard({ h }: { h: HoldingOut }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface2/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-base font-semibold text-text">{h.symbol}</span>
        <Badge tone="neutral">{h.weight_pct != null ? `${h.weight_pct.toFixed(1)}%` : "—"}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Stat label="Quantity" value={fmtShares(h.quantity)} />
        <Stat label="Avg cost" value={fmtUSD(h.avg_cost_usd)} />
        <Stat label="Price" value={fmtUSD(h.price_usd)} />
        <Stat label="Market value" value={fmtUSD(h.market_value_usd)} />
        <Stat label="Cost basis" value={fmtUSD(h.cost_basis_usd)} />
        <Stat
          label="Unrealized"
          value={fmtUSD(h.unrealized_usd)}
          sub={fmtPct(h.unrealized_pct)}
          valueClassName={gainClass(h.unrealized_usd)}
        />
      </div>
    </div>
  );
}

function HoldingsTable({ holdings }: { holdings: readonly HoldingOut[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-surface2/50 text-left text-xs uppercase tracking-wide text-muted">
            <th className="px-4 py-3 font-medium">Symbol</th>
            <th className="px-4 py-3 text-right font-medium">Quantity</th>
            <th className="px-4 py-3 text-right font-medium">Avg cost</th>
            <th className="px-4 py-3 text-right font-medium">Cost basis</th>
            <th className="px-4 py-3 text-right font-medium">Price</th>
            <th className="px-4 py-3 text-right font-medium">Market value</th>
            <th className="px-4 py-3 text-right font-medium">Unrealized</th>
            <th className="px-4 py-3 text-right font-medium">Weight</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => (
            <tr key={h.symbol} className="border-b border-border last:border-0">
              <td className="px-4 py-3 font-medium text-text">{h.symbol}</td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {fmtShares(h.quantity)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {fmtUSD(h.avg_cost_usd)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {fmtUSD(h.cost_basis_usd)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {fmtUSD(h.price_usd)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {fmtUSD(h.market_value_usd)}
              </td>
              <td
                className={cn(
                  "px-4 py-3 text-right tabular-nums",
                  gainClass(h.unrealized_usd),
                )}
              >
                <span className="block">{fmtUSD(h.unrealized_usd)}</span>
                <span className="block text-xs">{fmtPct(h.unrealized_pct)}</span>
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-text">
                {h.weight_pct != null ? `${h.weight_pct.toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Loading skeleton                                                             //
// --------------------------------------------------------------------------- //

function PortfolioSkeleton() {
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4 sm:p-5">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex flex-col gap-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-6 w-28" />
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:p-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Page                                                                          //
// --------------------------------------------------------------------------- //

export default function Portfolio() {
  const { hasPricing, valuationInput, prices, fxRate, updatedAt, setPricing } =
    usePricing();
  const [dialogOpen, setDialogOpen] = useState<boolean>(false);

  const valuation = usePortfolioValuation(valuationInput);

  // IPS policy supplies the strategic target weights (0–1 scale) and the drift
  // threshold. Both come straight from the engine — we NEVER substitute an
  // invented target or threshold (§20.0); until the policy loads, the target
  // overlay and within/beyond classification are simply withheld.
  const ipsQuery = useQuery<IpsPolicyOut, ApiError>({
    queryKey: queryKeys.ips.policy(),
    queryFn: () => ipsApi.get(),
    staleTime: 5 * 60_000,
  });

  const targetWeights = ipsQuery.data?.rules.target_weights;
  const driftThresholdPp = ipsQuery.data?.rules.drift_threshold_pct;

  const updateButton = (
    <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
      <RefreshCw className="h-4 w-4" />
      Update market prices
    </Button>
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Portfolio"
        description="A read-only view of your holdings — facts only, no recommendations."
        actions={updateButton}
      />

      {updatedAt ? (
        <p className="-mt-2 text-xs text-muted">
          Prices last updated {fmtDateTime(updatedAt)}
        </p>
      ) : null}

      {!hasPricing ? (
        <EmptyState
          icon={PiggyBank}
          title="Holdings can't be valued yet"
          description="WealthOS has no live market feed. Enter your broker's latest VOO and QQQ prices and the USD→MYR rate to value your holdings."
          action={
            <Button onClick={() => setDialogOpen(true)}>
              <RefreshCw className="h-4 w-4" />
              Update market prices
            </Button>
          }
        />
      ) : valuation.isLoading ? (
        <PortfolioSkeleton />
      ) : valuation.isError ? (
        <ErrorState
          title="Couldn't load your portfolio"
          message={valuation.error.detail}
          onRetry={() => void valuation.refetch()}
        />
      ) : valuation.data ? (
        <PortfolioContent
          data={valuation.data}
          targetWeights={targetWeights}
          driftThresholdPp={driftThresholdPp}
        />
      ) : null}

      <PriceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        initialPrices={prices}
        initialFxRate={fxRate}
        onSubmit={setPricing}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Content (priced)                                                             //
// --------------------------------------------------------------------------- //

interface PortfolioContentProps {
  data: ValuationOut;
  /** IPS target weights on the 0–1 scale; `undefined` until the policy loads. */
  targetWeights: Record<string, number> | undefined;
  /**
   * IPS drift threshold on the 0–100 (pp) scale, for within/beyond coloring;
   * `undefined` until the policy loads (never substituted, §20.0).
   */
  driftThresholdPp: number | undefined;
}

function PortfolioContent({
  data,
  targetWeights,
  driftThresholdPp,
}: PortfolioContentProps) {
  const { holdings, nav_usd, nav_myr, cash_usd } = data;

  // Allocation segments: current weight (from the backend) vs IPS target. The
  // bar is purely explanatory of figures the API already produced (§20.1).
  const segments = useMemo<AllocationSegment[]>(() => {
    const rows: AllocationSegment[] = holdings.map((h) => {
      const target = targetWeights?.[h.symbol];
      return {
        label: h.symbol,
        weightPct: h.weight_pct ?? 0,
        ...(target != null ? { targetPct: target * 100 } : {}),
      };
    });
    if (data.cash_weight_pct != null && data.cash_weight_pct > 0) {
      rows.push({ label: "Cash", weightPct: data.cash_weight_pct, tone: "neutral" });
    }
    return rows;
  }, [holdings, targetWeights, data.cash_weight_pct]);

  // Per-symbol drift: a comparison of two backend-supplied weights, classified
  // against the IPS threshold. Fact only — never a prompt to act (§20.5). Both
  // the target weights and the threshold are engine-supplied; without them we
  // withhold the drift figure rather than invent a target/threshold (§20.0).
  const driftRows = useMemo<DriftRow[]>(() => {
    if (targetWeights === undefined || driftThresholdPp === undefined) return [];
    return holdings.map((h) => {
      const currentPct = h.weight_pct ?? 0;
      const target = targetWeights[h.symbol];
      const targetPct = target != null ? target * 100 : 0;
      const driftPp = currentPct - targetPct;
      return {
        symbol: h.symbol,
        currentPct,
        targetPct,
        driftPp,
        beyond: Math.abs(driftPp) > driftThresholdPp,
      };
    });
  }, [holdings, targetWeights, driftThresholdPp]);

  return (
    <div className="flex flex-col gap-5">
      {/* NAV header */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4 sm:p-5">
          <Stat
            label="NAV (USD)"
            value={fmtUSD(nav_usd)}
            sub={`${fmtMYR(nav_myr)} · FX ${data.fx_rate.toFixed(4)}`}
          />
          <Stat label="NAV (MYR)" value={fmtMYR(nav_myr)} />
          <Stat label="Cash (USD)" value={fmtUSD(cash_usd)} />
          <Stat
            label="Total P/L"
            value={fmtUSD(data.total_pnl_usd)}
            sub={fmtPct(data.total_pnl_pct)}
            valueClassName={gainClass(data.total_pnl_usd)}
          />
        </CardContent>
      </Card>

      {/* Holdings */}
      <Card>
        <CardHeader>
          <CardTitle>Holdings</CardTitle>
          <CardDescription>
            Each position priced at the figures you entered. Quantities to 4dp.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {holdings.length === 0 ? (
            <EmptyState
              icon={PiggyBank}
              title="No holdings yet"
              description="Recorded trades in the ledger will appear here once you hold a position."
            />
          ) : (
            <>
              <div className="hidden lg:block">
                <HoldingsTable holdings={holdings} />
              </div>
              <div className="flex flex-col gap-3 lg:hidden">
                {holdings.map((h) => (
                  <HoldingCard key={h.symbol} h={h} />
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Allocation + Drift */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Allocation</CardTitle>
            <CardDescription>Current weight vs your IPS target.</CardDescription>
          </CardHeader>
          <CardContent>
            {segments.length === 0 ? (
              <EmptyState
                icon={PiggyBank}
                title="Nothing to allocate"
                description="Allocation appears once you hold a position."
              />
            ) : (
              <AllocationBar segments={segments} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Drift</CardTitle>
            <CardDescription>
              How far each position sits from its target. Informational only — any
              decision lives in the Execution Center.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {driftThresholdPp === undefined ? (
              <EmptyState
                icon={PiggyBank}
                title="Policy not available"
                description="Drift is measured against your IPS target and threshold. It appears once your policy loads."
              />
            ) : driftRows.length === 0 ? (
              <EmptyState
                icon={PiggyBank}
                title="No drift to show"
                description="Drift appears once you hold a position."
              />
            ) : (
              <div className="flex flex-col gap-4">
                {driftRows.map((row) => (
                  <DriftFigure key={row.symbol} row={row} />
                ))}
                <p className="text-xs text-muted">
                  Policy threshold: {fmtPp(driftThresholdPp, { digits: 1 })}.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
