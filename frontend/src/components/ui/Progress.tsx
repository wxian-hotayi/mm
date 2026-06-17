import { cn } from "@/lib/utils";
import type { ToneKey } from "@/lib/constants";

export interface ProgressProps {
  /** 0–100. Clamped for safety; never derived here, only displayed. */
  value: number;
  tone?: ToneKey;
  className?: string;
  "aria-label"?: string;
}

const FILL_CLASSES: Record<ToneKey, string> = {
  neutral: "bg-muted",
  accent: "bg-accent",
  gain: "bg-gain",
  loss: "bg-loss",
  warn: "bg-warn",
};

/** Horizontal progress bar. `value` is a display figure provided by the caller. */
export function Progress({
  value,
  tone = "accent",
  className,
  "aria-label": ariaLabel,
}: ProgressProps) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className={cn("h-2 w-full overflow-hidden rounded-full bg-surface2", className)}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={ariaLabel}
    >
      <div
        className={cn("h-full rounded-full transition-all", FILL_CLASSES[tone])}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
