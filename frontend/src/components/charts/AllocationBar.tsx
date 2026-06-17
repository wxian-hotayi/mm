import { cn } from "@/lib/utils";
import type { ToneKey } from "@/lib/constants";

export interface AllocationSegment {
  label: string;
  /** Current weight on the 0–100 scale (from the backend; never computed here). */
  weightPct: number;
  /** Target weight on the 0–100 scale (e.g. 70 / 30). */
  targetPct?: number;
  tone?: ToneKey;
}

export interface AllocationBarProps {
  segments: readonly AllocationSegment[];
  className?: string;
}

const SEG_COLORS: Record<ToneKey, string> = {
  neutral: "bg-muted",
  accent: "bg-accent",
  gain: "bg-gain",
  loss: "bg-loss",
  warn: "bg-warn",
};

const DEFAULT_TONES: ToneKey[] = ["accent", "gain", "warn", "loss", "neutral"];

/**
 * Explanatory allocation bar (DESIGN §20.1 — charts may only explain an existing
 * backend figure, never invite trading). Renders current weights with an optional
 * target tick per segment. All values are supplied by the caller; nothing is
 * computed or recommended here.
 */
export function AllocationBar({ segments, className }: AllocationBarProps) {
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-surface2"
        role="img"
        aria-label="Current allocation"
      >
        {segments.map((seg, i) => {
          const tone = seg.tone ?? DEFAULT_TONES[i % DEFAULT_TONES.length]!;
          return (
            <div
              key={seg.label}
              className={cn("h-full", SEG_COLORS[tone])}
              style={{ width: `${Math.max(0, Math.min(100, seg.weightPct))}%` }}
            />
          );
        })}
      </div>
      <ul className="flex flex-col gap-1.5">
        {segments.map((seg, i) => {
          const tone = seg.tone ?? DEFAULT_TONES[i % DEFAULT_TONES.length]!;
          return (
            <li
              key={seg.label}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <span className="flex items-center gap-2 text-muted">
                <span
                  className={cn("h-2.5 w-2.5 rounded-sm", SEG_COLORS[tone])}
                  aria-hidden
                />
                {seg.label}
              </span>
              <span className="tabular-nums text-text">
                {seg.weightPct.toFixed(1)}%
                {seg.targetPct != null ? (
                  <span className="ml-1 text-muted">
                    / {seg.targetPct.toFixed(0)}%
                  </span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
