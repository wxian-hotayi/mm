/**
 * Cash Buffer page (DESIGN §20.6 — supports accumulation discipline).
 *
 * Surfaces the operational MYR cash system (§19.1): per-account balances, total
 * cash, the prominently-highlighted DEPLOYABLE SURPLUS, buffer fill + readiness,
 * and the cash-movement ledger. Account/movement editing is operational data
 * entry (explicitly allowed, §20.6). There is NO deployment decision here — that
 * lives only in the Execution Center (§20.4). Every financial figure is rendered
 * verbatim from the backend; the UI never recomputes a value (§20.0).
 */

import { useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  ArrowDownLeft,
  ArrowUpRight,
  Pencil,
  Plus,
  Receipt,
  Wallet,
} from "lucide-react";

import type { ApiError } from "@/api/client";
import { cash } from "@/api/endpoints";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Progress } from "@/components/ui/Progress";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Stat } from "@/components/ui/Stat";
import { Textarea } from "@/components/ui/Textarea";
import { useCashAccounts, useCashSummary } from "@/hooks/useCashBuffer";
import { READINESS_LABELS } from "@/lib/constants";
import { fmtDate, fmtMYR } from "@/lib/format";
import { queryKeys, queryRoots } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";
import { toast } from "@/stores/toast";
import type {
  CashAccountBalanceOut,
  CashAccountOut,
  CashAccountType,
  CashMovementOut,
  CashMovementType,
  ReadinessState,
} from "@/types/api";

// --------------------------------------------------------------------------- //
// Static display metadata                                                      //
// --------------------------------------------------------------------------- //

const ACCOUNT_TYPE_LABELS: Record<CashAccountType, string> = {
  GXBANK: "GXBank",
  SAVINGS: "Savings",
  EMERGENCY_FUND: "Emergency Fund",
  BUSINESS: "Business",
  BROKER_CASH_MYR: "Broker Cash (MYR)",
  OTHER: "Other",
};

const ACCOUNT_TYPE_OPTIONS: readonly CashAccountType[] = [
  "GXBANK",
  "SAVINGS",
  "EMERGENCY_FUND",
  "BUSINESS",
  "BROKER_CASH_MYR",
  "OTHER",
];

interface MovementTypeMeta {
  label: string;
  /** Sign of the cash effect, for the ledger color hint. */
  effect: "in" | "out" | "neutral";
}

const MOVEMENT_TYPE_META: Record<CashMovementType, MovementTypeMeta> = {
  INFLOW: { label: "Inflow", effect: "in" },
  INTEREST: { label: "Interest", effect: "in" },
  TRANSFER_IN: { label: "Transfer In", effect: "in" },
  OUTFLOW: { label: "Outflow", effect: "out" },
  TRANSFER_OUT_TO_BROKER: { label: "Transfer to Broker", effect: "out" },
  ADJUSTMENT: { label: "Adjustment", effect: "neutral" },
};

const MOVEMENT_TYPE_OPTIONS: readonly CashMovementType[] = [
  "INFLOW",
  "OUTFLOW",
  "INTEREST",
  "TRANSFER_OUT_TO_BROKER",
  "TRANSFER_IN",
  "ADJUSTMENT",
];

const READINESS_TONE: Record<ReadinessState, BadgeTone> = {
  READY: "gain",
  ACCUMULATING: "accent",
};

const READINESS_HINT: Record<ReadinessState, string> = {
  READY: "Surplus has reached your deploy threshold.",
  ACCUMULATING: "Keep accumulating — discipline is working.",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function accountTypeLabel(value: string): string {
  return ACCOUNT_TYPE_LABELS[value as CashAccountType] ?? value;
}

function movementMeta(value: string): MovementTypeMeta {
  return MOVEMENT_TYPE_META[value as CashMovementType] ?? {
    label: value,
    effect: "neutral",
  };
}

// --------------------------------------------------------------------------- //
// Movements query (inline — no shared hook exists yet)                         //
// --------------------------------------------------------------------------- //

function useCashMovements(): UseQueryResult<CashMovementOut[], ApiError> {
  return useQuery<CashMovementOut[], ApiError>({
    queryKey: queryKeys.cash.movements(),
    queryFn: () => cash.listMovements(),
    staleTime: 30_000,
  });
}

// --------------------------------------------------------------------------- //
// Page                                                                         //
// --------------------------------------------------------------------------- //

export default function CashBuffer() {
  const queryClient = useQueryClient();

  const summaryQuery = useCashSummary();
  const accountsQuery = useCashAccounts();
  const movementsQuery = useCashMovements();

  const [accountForm, setAccountForm] = useState<
    { mode: "create" } | { mode: "edit"; account: CashAccountOut } | null
  >(null);
  const [movementOpen, setMovementOpen] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<CashAccountOut | null>(
    null,
  );

  const invalidateCash = () => {
    void queryClient.invalidateQueries({ queryKey: queryRoots.cash });
    void queryClient.invalidateQueries({ queryKey: queryRoots.networth });
    void queryClient.invalidateQueries({ queryKey: queryRoots.deployment });
    void queryClient.invalidateQueries({ queryKey: queryRoots.actionStatus });
    // The Wealth Operating Cycle state is a function of deployable surplus
    // (§19.2); a movement that crosses the deploy threshold changes it, so the
    // cycle cache must be refreshed too (parity with ExecutionCenter).
    void queryClient.invalidateQueries({ queryKey: queryRoots.cycle });
  };

  const archiveMutation = useMutation<CashAccountOut, ApiError, number>({
    mutationFn: (id) => cash.archiveAccount(id),
    onSuccess: () => {
      toast.success("Account archived", "Movement history is preserved.");
      setArchiveTarget(null);
      invalidateCash();
    },
    onError: (error) => toast.error("Could not archive account", error.detail),
  });

  const summary = summaryQuery.data;
  // Account balances come from the summary (derived server-side); the accounts
  // list query backs the edit forms and the movement-form account picker.
  const accounts = accountsQuery.data ?? [];
  const balanceByAccount = useMemo(() => {
    const map = new Map<number, CashAccountBalanceOut>();
    for (const row of summary?.accounts ?? []) map.set(row.id, row);
    return map;
  }, [summary]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Cash Buffer"
        description="Your operational cash and what's free to deploy."
        actions={
          <Button
            size="sm"
            variant="outline"
            onClick={() => setAccountForm({ mode: "create" })}
          >
            <Plus className="h-4 w-4" />
            Account
          </Button>
        }
      />

      <SummarySection query={summaryQuery} />

      <AccountsSection
        loading={accountsQuery.isLoading}
        error={accountsQuery.isError ? accountsQuery.error : null}
        accounts={accounts}
        balanceByAccount={balanceByAccount}
        onRetry={() => void accountsQuery.refetch()}
        onAdd={() => setAccountForm({ mode: "create" })}
        onEdit={(account) => setAccountForm({ mode: "edit", account })}
        onArchive={(account) => setArchiveTarget(account)}
      />

      <MovementsSection
        query={movementsQuery}
        accounts={accounts}
        canRecord={accounts.length > 0}
        onRecord={() => setMovementOpen(true)}
      />

      {accountForm ? (
        <AccountFormDialog
          initial={accountForm.mode === "edit" ? accountForm.account : null}
          onClose={() => setAccountForm(null)}
          onSaved={() => {
            setAccountForm(null);
            invalidateCash();
          }}
        />
      ) : null}

      <MovementFormDialog
        open={movementOpen}
        accounts={accounts}
        onClose={() => setMovementOpen(false)}
        onSaved={() => {
          setMovementOpen(false);
          invalidateCash();
        }}
      />

      <ConfirmDialog
        open={archiveTarget !== null}
        title="Archive this account?"
        description={
          archiveTarget
            ? `"${archiveTarget.name}" will be hidden. Its movement history is kept.`
            : undefined
        }
        confirmLabel="Archive"
        confirmVariant="destructive"
        loading={archiveMutation.isPending}
        onClose={() => {
          if (!archiveMutation.isPending) setArchiveTarget(null);
        }}
        onConfirm={() => {
          if (archiveTarget) archiveMutation.mutate(archiveTarget.id);
        }}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Summary section                                                              //
// --------------------------------------------------------------------------- //

function SummarySection({
  query,
}: {
  query: UseQueryResult<
    NonNullable<ReturnType<typeof useCashSummary>["data"]>,
    ApiError
  >;
}) {
  if (query.isLoading) {
    return (
      <Card>
        <CardContent className="flex flex-col gap-4 p-4 pt-4 sm:p-5 sm:pt-5">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-12 w-48" />
          <Skeleton className="h-2 w-full" />
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (query.isError || !query.data) {
    return (
      <ErrorState
        title="Couldn't load your cash position"
        message={query.error?.detail}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const summary = query.data;
  const readiness = summary.readiness as ReadinessState;
  // buffer_fill_ratio is a 0–1 ratio (backend §19.1); ×100 is display scaling
  // for the Progress bar, not a recomputed financial value.
  const fillRatio = summary.buffer_fill_ratio;
  const fillPct = fillRatio === null ? null : fillRatio * 100;
  const fillTone: BadgeTone = fillPct !== null && fillPct >= 100 ? "gain" : "accent";

  return (
    <Card className={cn("border-accent/30 bg-accent/5")}>
      <CardContent className="flex flex-col gap-5 p-4 pt-4 sm:p-5 sm:pt-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">
              Deployable surplus
            </span>
            <span className="text-3xl font-semibold tabular-nums text-accent sm:text-4xl">
              {fmtMYR(summary.deployable_surplus_myr)}
            </span>
            <span className="text-xs text-muted">
              {READINESS_HINT[readiness]}
            </span>
          </div>
          <Badge tone={READINESS_TONE[readiness]} dot>
            {READINESS_LABELS[readiness]}
          </Badge>
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Buffer fill</span>
            <span className="tabular-nums">
              {fillPct === null ? "No target set" : `${Math.round(fillPct)}%`}
            </span>
          </div>
          <Progress
            value={fillPct ?? 0}
            tone={fillTone}
            aria-label="Buffer fill ratio"
          />
        </div>

        <div className="grid grid-cols-2 gap-3 border-t border-border pt-4">
          <Stat label="Total cash" value={fmtMYR(summary.total_cash_myr)} />
          <Stat
            label="As of"
            value={fmtDate(summary.as_of)}
            valueClassName="text-base font-medium"
          />
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Accounts section                                                             //
// --------------------------------------------------------------------------- //

function AccountsSection({
  loading,
  error,
  accounts,
  balanceByAccount,
  onRetry,
  onAdd,
  onEdit,
  onArchive,
}: {
  loading: boolean;
  error: ApiError | null;
  accounts: CashAccountOut[];
  balanceByAccount: Map<number, CashAccountBalanceOut>;
  onRetry: () => void;
  onAdd: () => void;
  onEdit: (account: CashAccountOut) => void;
  onArchive: (account: CashAccountOut) => void;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-text">Accounts</h2>

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-28 w-full rounded-2xl" />
          <Skeleton className="h-28 w-full rounded-2xl" />
        </div>
      ) : error ? (
        <ErrorState
          title="Couldn't load accounts"
          message={error.detail}
          onRetry={onRetry}
        />
      ) : accounts.length === 0 ? (
        <EmptyState
          icon={Wallet}
          title="No cash accounts yet"
          description="Add GXBank or a savings account to start tracking your buffer."
          action={
            <Button size="sm" variant="outline" onClick={onAdd}>
              <Plus className="h-4 w-4" />
              Add account
            </Button>
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {accounts.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              balance={balanceByAccount.get(account.id)?.balance_myr ?? null}
              onEdit={() => onEdit(account)}
              onArchive={() => onArchive(account)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function AccountCard({
  account,
  balance,
  onEdit,
  onArchive,
}: {
  account: CashAccountOut;
  balance: number | null;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate font-medium text-text">
                {account.name}
              </span>
              {account.is_buffer_source ? (
                <Badge tone="accent">Buffer source</Badge>
              ) : null}
            </div>
            <span className="text-xs text-muted">
              {accountTypeLabel(account.account_type)} · {account.currency}
            </span>
          </div>
          <span className="shrink-0 text-lg font-semibold tabular-nums text-text">
            {fmtMYR(balance)}
          </span>
        </div>

        <div className="flex items-end justify-between gap-3 border-t border-border pt-3">
          <Stat
            label="Buffer target"
            value={fmtMYR(account.target_buffer_myr)}
            valueClassName="text-base font-medium"
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={onEdit}
              aria-label={`Edit ${account.name}`}
            >
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-loss hover:text-loss"
              onClick={onArchive}
              aria-label={`Archive ${account.name}`}
            >
              Archive
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Movements section                                                            //
// --------------------------------------------------------------------------- //

function MovementsSection({
  query,
  accounts,
  canRecord,
  onRecord,
}: {
  query: UseQueryResult<CashMovementOut[], ApiError>;
  accounts: CashAccountOut[];
  canRecord: boolean;
  onRecord: () => void;
}) {
  const accountNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const account of accounts) map.set(account.id, account.name);
    return map;
  }, [accounts]);

  // Newest first by movement date, then by id for same-day stability.
  const movements = useMemo(() => {
    const rows = query.data ?? [];
    return [...rows].sort((a, b) => {
      if (a.movement_date !== b.movement_date) {
        return a.movement_date < b.movement_date ? 1 : -1;
      }
      return b.id - a.id;
    });
  }, [query.data]);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-text">Cash movements</h2>
        <Button
          size="sm"
          variant="outline"
          onClick={onRecord}
          disabled={!canRecord}
        >
          <Plus className="h-4 w-4" />
          Record
        </Button>
      </div>

      {query.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full rounded-2xl" />
          <Skeleton className="h-20 w-full rounded-2xl" />
          <Skeleton className="h-20 w-full rounded-2xl" />
        </div>
      ) : query.isError ? (
        <ErrorState
          title="Couldn't load movements"
          message={query.error.detail}
          onRetry={() => void query.refetch()}
        />
      ) : movements.length === 0 ? (
        <EmptyState
          icon={Receipt}
          title="No cash movements recorded"
          description={
            canRecord
              ? "Record inflows, interest and transfers to keep your buffer accurate."
              : "Add a cash account first, then record movements here."
          }
          action={
            canRecord ? (
              <Button size="sm" variant="outline" onClick={onRecord}>
                <Plus className="h-4 w-4" />
                Record movement
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {movements.map((movement) => (
            <li key={movement.id}>
              <MovementCard
                movement={movement}
                accountName={accountNameById.get(movement.account_id) ?? null}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function MovementCard({
  movement,
  accountName,
}: {
  movement: CashMovementOut;
  accountName: string | null;
}) {
  const meta = movementMeta(movement.movement_type);
  const isOut = meta.effect === "out";
  const amountClass =
    meta.effect === "in"
      ? "text-gain"
      : meta.effect === "out"
        ? "text-loss"
        : "text-text";
  const Icon = isOut ? ArrowUpRight : ArrowDownLeft;
  const iconTone =
    meta.effect === "in"
      ? "bg-gain/10 text-gain"
      : meta.effect === "out"
        ? "bg-loss/10 text-loss"
        : "bg-surface2 text-muted";

  return (
    <Card>
      <CardContent className="flex items-start gap-3 p-3.5">
        <span
          className={cn(
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
            iconTone,
          )}
          aria-hidden
        >
          <Icon className="h-4 w-4" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-start justify-between gap-3">
            <span className="font-medium text-text">{meta.label}</span>
            <span
              className={cn(
                "shrink-0 font-semibold tabular-nums",
                amountClass,
              )}
            >
              {isOut ? "−" : ""}
              {fmtMYR(movement.amount_myr)}
            </span>
          </div>
          <span className="text-xs text-muted">
            {fmtDate(movement.movement_date)}
            {accountName ? ` · ${accountName}` : ""}
          </span>
          {movement.notes ? (
            <p className="mt-0.5 break-words text-xs text-muted">
              {movement.notes}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Account create / edit dialog                                                 //
// --------------------------------------------------------------------------- //

interface AccountFormState {
  name: string;
  accountType: CashAccountType;
  isBufferSource: boolean;
  targetBuffer: string;
}

function AccountFormDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial: CashAccountOut | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = initial !== null;
  const [form, setForm] = useState<AccountFormState>(() => ({
    name: initial?.name ?? "",
    accountType: (initial?.account_type as CashAccountType) ?? "GXBANK",
    isBufferSource: initial?.is_buffer_source ?? false,
    targetBuffer:
      initial && initial.target_buffer_myr > 0
        ? String(initial.target_buffer_myr)
        : "",
  }));

  const mutation = useMutation<CashAccountOut, ApiError, void>({
    mutationFn: () => {
      const trimmedName = form.name.trim();
      const target = form.targetBuffer.trim() === "" ? 0 : Number(form.targetBuffer);
      if (isEdit && initial) {
        return cash.updateAccount(initial.id, {
          name: trimmedName,
          account_type: form.accountType,
          is_buffer_source: form.isBufferSource,
          target_buffer_myr: target,
        });
      }
      return cash.createAccount({
        name: trimmedName,
        account_type: form.accountType,
        is_buffer_source: form.isBufferSource,
        target_buffer_myr: target,
      });
    },
    onSuccess: () => {
      toast.success(isEdit ? "Account updated" : "Account created");
      onSaved();
    },
    onError: (error) =>
      toast.error(
        isEdit ? "Could not update account" : "Could not create account",
        error.detail,
      ),
  });

  const targetValue = Number(form.targetBuffer);
  const targetInvalid =
    form.targetBuffer.trim() !== "" &&
    (!Number.isFinite(targetValue) || targetValue < 0);
  const nameInvalid = form.name.trim() === "";
  const canSubmit = !nameInvalid && !targetInvalid && !mutation.isPending;

  return (
    <Dialog
      open
      onClose={() => {
        if (!mutation.isPending) onClose();
      }}
      title={isEdit ? "Edit account" : "New cash account"}
      description="Operational cash record — balances derive from movements."
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            loading={mutation.isPending}
            disabled={!canSubmit}
          >
            {isEdit ? "Save changes" : "Create account"}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) mutation.mutate();
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="account-name">Name</Label>
          <Input
            id="account-name"
            value={form.name}
            invalid={nameInvalid}
            placeholder="e.g. GXBank"
            maxLength={120}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, name: e.target.value }))
            }
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="account-type">Type</Label>
          <Select
            id="account-type"
            value={form.accountType}
            onChange={(e) =>
              setForm((prev) => ({
                ...prev,
                accountType: e.target.value as CashAccountType,
              }))
            }
          >
            {ACCOUNT_TYPE_OPTIONS.map((type) => (
              <option key={type} value={type}>
                {ACCOUNT_TYPE_LABELS[type]}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="account-target">Buffer target (MYR)</Label>
          <Input
            id="account-target"
            type="number"
            inputMode="decimal"
            min={0}
            step="0.01"
            value={form.targetBuffer}
            invalid={targetInvalid}
            placeholder="0.00"
            onChange={(e) =>
              setForm((prev) => ({ ...prev, targetBuffer: e.target.value }))
            }
          />
          <p className="text-xs text-muted">
            The reserve kept in this account; it's never counted as deployable.
          </p>
        </div>

        <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface2 px-3 py-2.5">
          <span className="flex flex-col">
            <span className="text-sm font-medium text-text">
              Buffer source
            </span>
            <span className="text-xs text-muted">
              Counts toward your deployable surplus.
            </span>
          </span>
          <input
            type="checkbox"
            className="h-5 w-5 shrink-0 accent-accent"
            checked={form.isBufferSource}
            onChange={(e) =>
              setForm((prev) => ({
                ...prev,
                isBufferSource: e.target.checked,
              }))
            }
          />
        </label>
      </form>
    </Dialog>
  );
}

// --------------------------------------------------------------------------- //
// Record movement dialog                                                       //
// --------------------------------------------------------------------------- //

interface MovementFormState {
  accountId: string;
  movementType: CashMovementType;
  amount: string;
  date: string;
  notes: string;
}

function MovementFormDialog({
  open,
  accounts,
  onClose,
  onSaved,
}: {
  open: boolean;
  accounts: CashAccountOut[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const firstAccountId = accounts[0]?.id;
  const [form, setForm] = useState<MovementFormState>(() => ({
    accountId: firstAccountId !== undefined ? String(firstAccountId) : "",
    movementType: "INFLOW",
    amount: "",
    date: todayIso(),
    notes: "",
  }));

  const mutation = useMutation<CashMovementOut, ApiError, void>({
    mutationFn: () =>
      cash.createMovement({
        account_id: Number(form.accountId),
        movement_type: form.movementType,
        amount_myr: Number(form.amount),
        movement_date: form.date,
        notes: form.notes.trim() === "" ? undefined : form.notes.trim(),
      }),
    onSuccess: () => {
      toast.success("Movement recorded");
      setForm({
        accountId:
          firstAccountId !== undefined ? String(firstAccountId) : "",
        movementType: "INFLOW",
        amount: "",
        date: todayIso(),
        notes: "",
      });
      onSaved();
    },
    onError: (error) =>
      toast.error("Could not record movement", error.detail),
  });

  const amountValue = Number(form.amount);
  // ADJUSTMENT may be signed (correction); all other types require a positive amount.
  const amountInvalid =
    form.amount.trim() === "" ||
    !Number.isFinite(amountValue) ||
    amountValue === 0 ||
    (form.movementType !== "ADJUSTMENT" && amountValue < 0);
  const accountInvalid = form.accountId === "";
  const dateInvalid = form.date.trim() === "";
  const canSubmit =
    !amountInvalid && !accountInvalid && !dateInvalid && !mutation.isPending;

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!mutation.isPending) onClose();
      }}
      title="Record cash movement"
      description="Operational entry — this records cash activity, not a deployment decision."
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            loading={mutation.isPending}
            disabled={!canSubmit}
          >
            Record
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) mutation.mutate();
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="movement-account">Account</Label>
          <Select
            id="movement-account"
            value={form.accountId}
            aria-invalid={accountInvalid || undefined}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, accountId: e.target.value }))
            }
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="movement-type">Type</Label>
          <Select
            id="movement-type"
            value={form.movementType}
            onChange={(e) =>
              setForm((prev) => ({
                ...prev,
                movementType: e.target.value as CashMovementType,
              }))
            }
          >
            {MOVEMENT_TYPE_OPTIONS.map((type) => (
              <option key={type} value={type}>
                {MOVEMENT_TYPE_META[type].label}
              </option>
            ))}
          </Select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="movement-amount">Amount (MYR)</Label>
            <Input
              id="movement-amount"
              type="number"
              inputMode="decimal"
              step="0.01"
              value={form.amount}
              invalid={form.amount.trim() !== "" && amountInvalid}
              placeholder="0.00"
              onChange={(e) =>
                setForm((prev) => ({ ...prev, amount: e.target.value }))
              }
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="movement-date">Date</Label>
            <Input
              id="movement-date"
              type="date"
              value={form.date}
              invalid={dateInvalid}
              max={todayIso()}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, date: e.target.value }))
              }
            />
          </div>
        </div>

        {form.movementType === "ADJUSTMENT" ? (
          <p className="text-xs text-muted">
            Adjustments may be negative to record a correction.
          </p>
        ) : null}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="movement-notes">Notes (optional)</Label>
          <Textarea
            id="movement-notes"
            value={form.notes}
            maxLength={2000}
            placeholder="e.g. Monthly salary, GXBank interest…"
            onChange={(e) =>
              setForm((prev) => ({ ...prev, notes: e.target.value }))
            }
          />
        </div>
      </form>
    </Dialog>
  );
}
