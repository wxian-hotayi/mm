/**
 * Display formatters (DESIGN §5, §10). These format numbers the backend has
 * ALREADY computed — they never derive financial values (DESIGN §20.0). Money
 * arrives as numbers from the API boundary; MYR is the life currency, USD the
 * portfolio currency.
 */

import { format, formatDistanceToNowStrict, parseISO } from "date-fns";

/** Sign-aware Tailwind text-color class for a signed value. */
export function gainClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-muted";
  return value > 0 ? "text-gain" : "text-loss";
}

function toFiniteNumber(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

const MYR_FORMATTER = new Intl.NumberFormat("en-MY", {
  style: "currency",
  currency: "MYR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const USD_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Format a MYR amount, e.g. `RM2,500.00`. The placeholder is shown for nulls. */
export function fmtMYR(
  value: number | null | undefined,
  placeholder = "—",
): string {
  const num = toFiniteNumber(value);
  if (num === null) return placeholder;
  return MYR_FORMATTER.format(num);
}

/** Format a USD amount, e.g. `$1,380.42`. */
export function fmtUSD(
  value: number | null | undefined,
  placeholder = "—",
): string {
  const num = toFiniteNumber(value);
  if (num === null) return placeholder;
  return USD_FORMATTER.format(num);
}

/**
 * Format a percentage with an explicit sign. `value` is the percentage figure
 * on the same scale the backend returned: pass `scale: "ratio"` for 0–1
 * decimals (e.g. 0.0945 -> "+9.45%") or the default `"percent"` for 0–100
 * figures (e.g. 1.3 -> "+1.30%").
 */
export function fmtPct(
  value: number | null | undefined,
  options: { scale?: "ratio" | "percent"; digits?: number; placeholder?: string } = {},
): string {
  const { scale = "percent", digits = 2, placeholder = "—" } = options;
  const num = toFiniteNumber(value);
  if (num === null) return placeholder;
  const pct = scale === "ratio" ? num * 100 : num;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

/** Format a percentage-point figure with sign, e.g. drift `1.30 pp`. */
export function fmtPp(
  value: number | null | undefined,
  options: { digits?: number; placeholder?: string } = {},
): string {
  const { digits = 2, placeholder = "—" } = options;
  const num = toFiniteNumber(value);
  if (num === null) return placeholder;
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(digits)} pp`;
}

/**
 * Format a share quantity at up to 4dp, trimming trailing zeros (e.g.
 * `2.4` not `2.4000`, `10` not `10.0000`).
 */
export function fmtShares(
  value: number | null | undefined,
  placeholder = "—",
): string {
  const num = toFiniteNumber(value);
  if (num === null) return placeholder;
  const fixed = num.toFixed(4);
  return fixed.replace(/\.?0+$/, "");
}

function asDate(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const date = value instanceof Date ? value : parseISO(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Format an ISO date/datetime string as `d MMM yyyy` (e.g. `17 Jun 2026`). */
export function fmtDate(
  value: string | Date | null | undefined,
  placeholder = "—",
): string {
  const date = asDate(value);
  if (date === null) return placeholder;
  return format(date, "d MMM yyyy");
}

/** Format an ISO datetime as `d MMM yyyy, HH:mm`. */
export function fmtDateTime(
  value: string | Date | null | undefined,
  placeholder = "—",
): string {
  const date = asDate(value);
  if (date === null) return placeholder;
  return format(date, "d MMM yyyy, HH:mm");
}

/** Human-friendly relative time, e.g. `3 days ago` / `in 2 weeks`. */
export function fmtRelative(
  value: string | Date | null | undefined,
  placeholder = "—",
): string {
  const date = asDate(value);
  if (date === null) return placeholder;
  return formatDistanceToNowStrict(date, { addSuffix: true });
}

/** Format an integer compliance score on the 0–100 scale, e.g. `92 / 100`. */
export function fmtScore(value: number | null | undefined): string {
  const num = toFiniteNumber(value);
  if (num === null) return "—";
  return `${Math.round(num)} / 100`;
}
