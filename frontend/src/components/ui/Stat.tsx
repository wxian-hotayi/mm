import { cn } from "@/lib/utils";

export interface StatProps {
  label: string;
  /** Primary value, pre-formatted by the caller (the UI never recomputes). */
  value: React.ReactNode;
  /** Optional secondary line (e.g. MYR equivalent, period change). */
  sub?: React.ReactNode;
  /** Tailwind color class for the value (e.g. text-gain / text-loss). */
  valueClassName?: string;
  className?: string;
}

/** Label + value block used in stat grids. Values arrive pre-formatted. */
export function Stat({ label, value, sub, valueClassName, className }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-0.5", className)}>
      <span className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </span>
      <span className={cn("text-lg font-semibold tabular-nums text-text", valueClassName)}>
        {value}
      </span>
      {sub != null ? (
        <span className="text-xs text-muted tabular-nums">{sub}</span>
      ) : null}
    </div>
  );
}
