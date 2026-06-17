/**
 * Settings (DESIGN §20.8, §19.5). Sectioned, mobile-first surface for the parts
 * of WealthOS the user owns: the Investment Policy Statement (target allocation,
 * drift threshold, per-rule enforcement levels), the read-only account profile,
 * security actions (sign out + password-reset request), and manual market prices.
 *
 * The page MIRRORS the backend (§20.0): it edits stored policy/profile values and
 * passes them straight to the API. It NEVER recommends a target, infers a price,
 * or surfaces any forbidden trading/discovery affordance (§20.1). Target weights
 * are edited on the human 0–100 scale and converted to the backend's 0–1 ratios
 * on save; only changed fields are sent (the PUT is partial).
 */

import { useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  KeyRound,
  LineChart,
  LogOut,
  ShieldCheck,
  Target,
  UserRound,
} from "lucide-react";

import type { ApiError } from "@/api/client";
import { auth as authApi, ips as ipsApi } from "@/api/endpoints";
import {
  Badge,
  type BadgeTone,
} from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { useAuth } from "@/hooks/useAuth";
import { usePricing } from "@/hooks/usePricing";
import { fmtDateTime, fmtPp, fmtScore, fmtUSD } from "@/lib/format";
import { queryKeys, queryRoots } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";
import { toast } from "@/stores/toast";
import type {
  IpsEnforcementLevel,
  IpsPolicyOut,
  IpsRuleIn,
  IpsRuleOut,
  OkOut,
} from "@/types/api";

// --------------------------------------------------------------------------- //
// Static section metadata                                                      //
// --------------------------------------------------------------------------- //

/** The two index funds the IPS targets (the only allocation the policy holds). */
const TARGET_SYMBOLS = ["VOO", "QQQ"] as const;
type TargetSymbol = (typeof TARGET_SYMBOLS)[number];

/** Enforcement levels selectable for asset-class rules (BLOCK-eligible). */
const ASSET_LEVELS: readonly IpsEnforcementLevel[] = ["INFO", "WARN", "BLOCK"];
/**
 * Behavioral rules (drift) are clamped to at most WARN server-side (§19.5) —
 * the policy can never hard-block the user's own ledger, so BLOCK is omitted.
 */
const BEHAVIORAL_LEVELS: readonly IpsEnforcementLevel[] = ["INFO", "WARN"];

const ENFORCEMENT_TONE: Record<IpsEnforcementLevel, BadgeTone> = {
  INFO: "neutral",
  WARN: "warn",
  BLOCK: "loss",
};

// --------------------------------------------------------------------------- //
// Local form state for the editable IPS policy                                //
// --------------------------------------------------------------------------- //

interface IpsFormState {
  /** Target weights on the human 0–100 scale, keyed by symbol. */
  weights: Record<TargetSymbol, string>;
  driftThresholdPct: string;
  enforceDrift: IpsEnforcementLevel;
  enforceForbiddenAssets: IpsEnforcementLevel;
  enforceLeverage: IpsEnforcementLevel;
  enforceOptions: IpsEnforcementLevel;
}

/** Build editable form state from the stored policy (0–1 weights -> 0–100). */
function toFormState(rule: IpsRuleOut): IpsFormState {
  const weights = {} as Record<TargetSymbol, string>;
  for (const symbol of TARGET_SYMBOLS) {
    const ratio = rule.target_weights[symbol];
    weights[symbol] =
      ratio === undefined ? "0" : String(round2(ratio * 100));
  }
  return {
    weights,
    driftThresholdPct: String(rule.drift_threshold_pct),
    enforceDrift: rule.enforce_drift,
    enforceForbiddenAssets: rule.enforce_forbidden_assets,
    enforceLeverage: rule.enforce_leverage,
    enforceOptions: rule.enforce_options,
  };
}

/** Round to 2 decimals, avoiding floating-point display noise. */
function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Parse a user-entered numeric string; null when blank/non-finite. */
function parseNum(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const num = Number(trimmed);
  return Number.isFinite(num) ? num : null;
}

interface IpsFormErrors {
  weights?: string;
  driftThresholdPct?: string;
}

function validateForm(form: IpsFormState): IpsFormErrors {
  const errors: IpsFormErrors = {};

  let sum = 0;
  let weightsValid = true;
  for (const symbol of TARGET_SYMBOLS) {
    const num = parseNum(form.weights[symbol]);
    if (num === null || num < 0 || num > 100) {
      weightsValid = false;
      break;
    }
    sum += num;
  }
  if (!weightsValid) {
    errors.weights = "Enter a weight from 0 to 100 for each fund.";
  } else if (round2(sum) !== 100) {
    errors.weights = `Target weights must sum to 100% (currently ${round2(
      sum,
    )}%).`;
  }

  const drift = parseNum(form.driftThresholdPct);
  if (drift === null || drift <= 0 || drift > 100) {
    errors.driftThresholdPct =
      "Enter a drift threshold greater than 0 and up to 100.";
  }

  return errors;
}

/**
 * Diff the edited form against the stored policy and build a partial `IpsRuleIn`
 * with only the changed fields (the backend PUT merges unset fields, §19.5).
 */
function buildUpdate(form: IpsFormState, rule: IpsRuleOut): IpsRuleIn {
  const payload: IpsRuleIn = {};

  // Target weights -> 0–1 ratios; send the whole map if any weight changed.
  const nextWeights: Record<string, number> = {};
  let weightsChanged = false;
  for (const symbol of TARGET_SYMBOLS) {
    const pct = parseNum(form.weights[symbol]) ?? 0;
    const ratio = round4(pct / 100);
    nextWeights[symbol] = ratio;
    const prev = rule.target_weights[symbol];
    if (prev === undefined || round4(prev) !== ratio) weightsChanged = true;
  }
  // Also treat a removed/added symbol set as a change.
  if (
    Object.keys(rule.target_weights).length !== TARGET_SYMBOLS.length ||
    !TARGET_SYMBOLS.every((s) => s in rule.target_weights)
  ) {
    weightsChanged = true;
  }
  if (weightsChanged) payload.target_weights = nextWeights;

  const drift = parseNum(form.driftThresholdPct);
  if (drift !== null && drift !== rule.drift_threshold_pct) {
    payload.drift_threshold_pct = drift;
  }

  if (form.enforceDrift !== rule.enforce_drift) {
    payload.enforce_drift = form.enforceDrift;
  }
  if (form.enforceForbiddenAssets !== rule.enforce_forbidden_assets) {
    payload.enforce_forbidden_assets = form.enforceForbiddenAssets;
  }
  if (form.enforceLeverage !== rule.enforce_leverage) {
    payload.enforce_leverage = form.enforceLeverage;
  }
  if (form.enforceOptions !== rule.enforce_options) {
    payload.enforce_options = form.enforceOptions;
  }

  return payload;
}

/** Round to 4 decimals — the ratio precision the backend stores weights at. */
function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

function isEmptyUpdate(payload: IpsRuleIn): boolean {
  return Object.keys(payload).length === 0;
}

// --------------------------------------------------------------------------- //
// IPS policy query                                                             //
// --------------------------------------------------------------------------- //

function useIpsPolicy(): UseQueryResult<IpsPolicyOut, ApiError> {
  const { pricing } = usePricing();
  return useQuery<IpsPolicyOut, ApiError>({
    queryKey: queryKeys.ips.policy(pricing),
    queryFn: () => ipsApi.get(pricing),
    staleTime: 30_000,
  });
}

// --------------------------------------------------------------------------- //
// Page                                                                         //
// --------------------------------------------------------------------------- //

export default function Settings() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        description="Your policy, profile and account controls. Strategy is editable; balances and decisions stay with the engine."
      />
      <IpsSection />
      <AccountSection />
      <SecuritySection />
      <MarketPricesSection />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// IPS Rules + Target Allocation + Drift Threshold                              //
// --------------------------------------------------------------------------- //

function IpsSection() {
  const query = useIpsPolicy();
  const queryClient = useQueryClient();

  const [form, setForm] = useState<IpsFormState | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  const rule = query.data?.rules;
  // Re-seed only when the stored policy actually changes (id + last update),
  // so a background refetch never clobbers in-progress edits.
  const ruleSignature = rule ? `${rule.id}:${rule.updated_at}` : null;
  const [seededFor, setSeededFor] = useState<string | null>(null);
  if (rule && ruleSignature !== null && ruleSignature !== seededFor) {
    setForm(toFormState(rule));
    setSeededFor(ruleSignature);
  }

  const mutation = useMutation<IpsPolicyOut, ApiError, IpsRuleIn>({
    mutationFn: (payload) => ipsApi.update(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.ips.policy(undefined), data);
      void queryClient.invalidateQueries({ queryKey: queryRoots.ips });
      void queryClient.invalidateQueries({ queryKey: queryRoots.actionStatus });
      // Drift threshold / target weights feed the cycle engine's rebalance
      // precedence (§19.2) and any valuation-derived drift view, so refresh
      // both — otherwise the Dashboard cycle card stays stale after a save.
      void queryClient.invalidateQueries({ queryKey: queryRoots.cycle });
      void queryClient.invalidateQueries({ queryKey: queryRoots.portfolio });
      setForm(toFormState(data.rules));
      setShowErrors(false);
      toast.success(
        "Policy updated",
        "Your IPS now drives the engine's checks.",
      );
    },
    onError: (error) => {
      toast.error("Could not save policy", error.detail);
    },
  });

  if (query.isLoading) {
    return <SectionSkeleton rows={4} />;
  }
  if (query.isError || !rule || !form) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Investment Policy</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorState
            title="Couldn't load your policy"
            message={query.error?.detail}
            onRetry={() => void query.refetch()}
          />
        </CardContent>
      </Card>
    );
  }

  const errors = validateForm(form);
  const update = buildUpdate(form, rule);
  const dirty = !isEmptyUpdate(update);
  const hasErrors = Object.keys(errors).length > 0;
  const compliance = query.data?.compliance;

  const setWeight = (symbol: TargetSymbol, value: string) =>
    setForm((prev) =>
      prev ? { ...prev, weights: { ...prev.weights, [symbol]: value } } : prev,
    );

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    if (hasErrors) {
      toast.warn("Check the highlighted fields", "Fix the errors and try again.");
      return;
    }
    if (!dirty) return;
    mutation.mutate(update);
  };

  const handleReset = () => {
    setForm(toFormState(rule));
    setShowErrors(false);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle className="flex items-center gap-2">
              <Target className="h-4 w-4 text-accent" aria-hidden />
              Investment Policy
            </CardTitle>
            <CardDescription>
              Your target mix and the limits the engine enforces on every action.
            </CardDescription>
          </div>
          {compliance ? (
            <Stat
              label="Compliance"
              value={fmtScore(compliance.score)}
              className="items-end text-right"
            />
          ) : null}
        </div>
      </CardHeader>

      <form onSubmit={handleSubmit} noValidate>
        <CardContent className="flex flex-col gap-6">
          {/* Target allocation */}
          <fieldset className="flex flex-col gap-3">
            <legend className="text-sm font-medium text-text">
              Target allocation
            </legend>
            <p className="text-xs text-muted">
              Long-term target weights. They must sum to 100%.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {TARGET_SYMBOLS.map((symbol) => {
                const id = `ips-weight-${symbol}`;
                return (
                  <div key={symbol} className="flex flex-col gap-1.5">
                    <Label htmlFor={id}>{symbol} target weight</Label>
                    <div className="relative">
                      <Input
                        id={id}
                        type="number"
                        inputMode="decimal"
                        min={0}
                        max={100}
                        step="0.01"
                        className="pr-9"
                        value={form.weights[symbol]}
                        invalid={showErrors && Boolean(errors.weights)}
                        onChange={(e) => setWeight(symbol, e.target.value)}
                      />
                      <span
                        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted"
                        aria-hidden
                      >
                        %
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
            {showErrors && errors.weights ? (
              <FieldError>{errors.weights}</FieldError>
            ) : null}
          </fieldset>

          {/* Drift threshold */}
          <fieldset className="flex flex-col gap-3">
            <legend className="text-sm font-medium text-text">
              Drift threshold
            </legend>
            <p className="text-xs text-muted">
              How far a holding may drift from target before the engine flags a
              rebalance.
            </p>
            <div className="flex max-w-[12rem] flex-col gap-1.5">
              <Label htmlFor="ips-drift">Threshold</Label>
              <div className="relative">
                <Input
                  id="ips-drift"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  max={100}
                  step="0.1"
                  className="pr-10"
                  value={form.driftThresholdPct}
                  invalid={showErrors && Boolean(errors.driftThresholdPct)}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev
                        ? { ...prev, driftThresholdPct: e.target.value }
                        : prev,
                    )
                  }
                />
                <span
                  className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted"
                  aria-hidden
                >
                  pp
                </span>
              </div>
            </div>
            {showErrors && errors.driftThresholdPct ? (
              <FieldError>{errors.driftThresholdPct}</FieldError>
            ) : null}
            <p className="text-xs text-muted">
              Current stored threshold: {fmtPp(rule.drift_threshold_pct)}.
            </p>
          </fieldset>

          {/* Enforcement levels */}
          <fieldset className="flex flex-col gap-3">
            <legend className="text-sm font-medium text-text">
              Enforcement levels
            </legend>
            <p className="text-xs text-muted">
              How strictly each rule is applied. INFO notes it, WARN warns you,
              BLOCK prevents the action. Drift is advisory and capped at WARN.
            </p>
            <div className="flex flex-col gap-3">
              <EnforcementRow
                id="enforce-forbidden"
                label="Individual stocks / forbidden assets"
                value={form.enforceForbiddenAssets}
                levels={ASSET_LEVELS}
                onChange={(level) =>
                  setForm((prev) =>
                    prev ? { ...prev, enforceForbiddenAssets: level } : prev,
                  )
                }
              />
              <EnforcementRow
                id="enforce-leverage"
                label="Leverage"
                value={form.enforceLeverage}
                levels={ASSET_LEVELS}
                onChange={(level) =>
                  setForm((prev) =>
                    prev ? { ...prev, enforceLeverage: level } : prev,
                  )
                }
              />
              <EnforcementRow
                id="enforce-options"
                label="Options"
                value={form.enforceOptions}
                levels={ASSET_LEVELS}
                onChange={(level) =>
                  setForm((prev) =>
                    prev ? { ...prev, enforceOptions: level } : prev,
                  )
                }
              />
              <EnforcementRow
                id="enforce-drift"
                label="Allocation drift (advisory)"
                value={form.enforceDrift}
                levels={BEHAVIORAL_LEVELS}
                onChange={(level) =>
                  setForm((prev) =>
                    prev ? { ...prev, enforceDrift: level } : prev,
                  )
                }
              />
            </div>
          </fieldset>
        </CardContent>

        <CardFooter className="flex-col items-stretch gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={handleReset}
            disabled={!dirty || mutation.isPending}
          >
            Discard changes
          </Button>
          <Button
            type="submit"
            loading={mutation.isPending}
            disabled={!dirty || (showErrors && hasErrors)}
          >
            Save policy
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

interface EnforcementRowProps {
  id: string;
  label: string;
  value: IpsEnforcementLevel;
  levels: readonly IpsEnforcementLevel[];
  onChange: (level: IpsEnforcementLevel) => void;
}

function EnforcementRow({
  id,
  label,
  value,
  levels,
  onChange,
}: EnforcementRowProps) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface2/50 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-2">
        <Label htmlFor={id} className="text-sm">
          {label}
        </Label>
        <Badge tone={ENFORCEMENT_TONE[value]} dot>
          {value}
        </Badge>
      </div>
      <Select
        id={id}
        className="sm:max-w-[8.5rem]"
        value={value}
        onChange={(e) => onChange(e.target.value as IpsEnforcementLevel)}
      >
        {levels.map((level) => (
          <option key={level} value={level}>
            {level}
          </option>
        ))}
      </Select>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Account Settings                                                            //
// --------------------------------------------------------------------------- //

function AccountSection() {
  const { user, isLoading } = useAuth();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserRound className="h-4 w-4 text-accent" aria-hidden />
          Account
        </CardTitle>
        <CardDescription>Your profile, as held by the system.</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : user ? (
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Username" value={user.username} />
            <Stat label="Role" value={<span className="capitalize">{user.role}</span>} />
            <Stat
              label="Email"
              value={<span className="break-all text-base">{user.email}</span>}
            />
            <Stat label="Base currency" value={user.base_currency} />
          </div>
        ) : (
          <EmptyState
            icon={UserRound}
            title="Profile unavailable"
            description="Your account details could not be loaded."
          />
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Security Settings                                                           //
// --------------------------------------------------------------------------- //

function SecuritySection() {
  const { user, logout } = useAuth();
  const [confirmSignOut, setConfirmSignOut] = useState(false);

  const resetMutation = useMutation<OkOut, ApiError, string>({
    mutationFn: (email) => authApi.passwordResetRequest({ email }),
    onSuccess: () => {
      toast.success(
        "Reset link issued",
        "If the email is registered, a password-reset link has been sent.",
      );
    },
    onError: (error) => {
      toast.error("Could not request reset", error.detail);
    },
  });

  const handleSignOut = () => {
    logout.mutate(undefined, {
      onSettled: () => setConfirmSignOut(false),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-accent" aria-hidden />
          Security
        </CardTitle>
        <CardDescription>
          Your session is carried by a secure, browser-managed cookie — no token
          is stored in the app.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface2/50 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2">
            <KeyRound className="mt-0.5 h-4 w-4 text-muted" aria-hidden />
            <div className="flex flex-col gap-0.5">
              <p className="text-sm font-medium text-text">Password</p>
              <p className="text-xs text-muted">
                We issue a reset link to your email rather than changing it
                in-app.
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            loading={resetMutation.isPending}
            disabled={!user?.email}
            onClick={() => {
              if (user?.email) resetMutation.mutate(user.email);
            }}
          >
            Reset password
          </Button>
        </div>

        <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface2/50 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2">
            <LogOut className="mt-0.5 h-4 w-4 text-muted" aria-hidden />
            <div className="flex flex-col gap-0.5">
              <p className="text-sm font-medium text-text">Session</p>
              <p className="text-xs text-muted">
                Sign out to end this session on this device.
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            loading={logout.isPending}
            onClick={() => setConfirmSignOut(true)}
          >
            Sign out
          </Button>
        </div>
      </CardContent>

      <ConfirmDialog
        open={confirmSignOut}
        title="Sign out?"
        description="You'll need to sign in again to access WealthOS on this device."
        confirmLabel="Sign out"
        cancelLabel="Stay signed in"
        loading={logout.isPending}
        onConfirm={handleSignOut}
        onClose={() => setConfirmSignOut(false)}
      />
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Market Prices (manual, §20.1 manual entry — NOT a quotes feed)              //
// --------------------------------------------------------------------------- //

function MarketPricesSection() {
  const { prices, fxRate, updatedAt, hasPricing, setPricing } = usePricing();

  const initial = useMemo(
    () => ({
      voo: prices.VOO !== undefined ? String(prices.VOO) : "",
      qqq: prices.QQQ !== undefined ? String(prices.QQQ) : "",
      fx: fxRate !== null ? String(fxRate) : "",
    }),
    [prices.VOO, prices.QQQ, fxRate],
  );

  const [draft, setDraft] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);

  // Re-seed the draft when the persisted store changes (e.g. set elsewhere).
  useEffect(() => {
    setDraft(initial);
    setShowErrors(false);
  }, [initial]);

  const voo = parseNum(draft.voo);
  const qqq = parseNum(draft.qqq);
  const fx = parseNum(draft.fx);
  const fxError = fx === null || fx <= 0;
  const priceError =
    (voo === null || voo <= 0) || (qqq === null || qqq <= 0);
  const invalid = fxError || priceError;

  const handleSave = (event: React.FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    if (invalid || voo === null || qqq === null || fx === null) {
      toast.warn("Check the prices", "Enter a positive price for each fund and the FX rate.");
      return;
    }
    setPricing({ VOO: voo, QQQ: qqq }, fx);
    toast.success("Prices saved", "Valuations across WealthOS will use these.");
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <LineChart className="h-4 w-4 text-accent" aria-hidden />
          Market prices
        </CardTitle>
        <CardDescription>
          Enter the latest prices from your broker. WealthOS never fetches quotes
          — you control these manually.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!hasPricing ? (
          <EmptyState
            icon={LineChart}
            title="Enter latest prices"
            description="Add VOO and QQQ prices and the USD→MYR rate so holdings can be valued. Until then, investment is reported as 0."
          />
        ) : (
          <p className="text-xs text-muted">
            Last updated {fmtDateTime(updatedAt)}.
          </p>
        )}

        <form onSubmit={handleSave} noValidate className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="price-voo">VOO price (USD)</Label>
              <Input
                id="price-voo"
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                placeholder="0.00"
                value={draft.voo}
                invalid={showErrors && (voo === null || voo <= 0)}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, voo: e.target.value }))
                }
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="price-qqq">QQQ price (USD)</Label>
              <Input
                id="price-qqq"
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                placeholder="0.00"
                value={draft.qqq}
                invalid={showErrors && (qqq === null || qqq <= 0)}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, qqq: e.target.value }))
                }
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="price-fx">USD → MYR rate</Label>
              <Input
                id="price-fx"
                type="number"
                inputMode="decimal"
                min={0}
                step="0.0001"
                placeholder="0.0000"
                value={draft.fx}
                invalid={showErrors && fxError}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, fx: e.target.value }))
                }
              />
            </div>
          </div>
          {showErrors && invalid ? (
            <FieldError>
              Enter a positive price for each fund and a positive FX rate.
            </FieldError>
          ) : null}

          {hasPricing ? (
            <div className="grid grid-cols-3 gap-3 rounded-xl border border-border bg-surface2/50 p-3">
              <Stat label="VOO" value={fmtUSD(prices.VOO ?? null)} />
              <Stat label="QQQ" value={fmtUSD(prices.QQQ ?? null)} />
              <Stat
                label="FX"
                value={
                  fxRate !== null ? fxRate.toFixed(4) : "—"
                }
              />
            </div>
          ) : null}

          <div className="flex justify-end">
            <Button type="submit">Save prices</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Shared small pieces                                                          //
// --------------------------------------------------------------------------- //

function FieldError({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-medium text-loss" role="alert">
      {children}
    </p>
  );
}

function SectionSkeleton({ rows }: { rows: number }) {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-40" />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className={cn("h-11", i === 0 && "h-16")} />
        ))}
      </CardContent>
    </Card>
  );
}
