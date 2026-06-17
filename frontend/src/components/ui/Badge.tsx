import { cn } from "@/lib/utils";
import type { ToneKey } from "@/lib/constants";

export type BadgeTone = ToneKey;

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Render a leading status dot (status-indicator style). */
  dot?: boolean;
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "border-border bg-surface2 text-muted",
  accent: "border-accent/40 bg-accent/10 text-accent",
  gain: "border-gain/40 bg-gain/10 text-gain",
  loss: "border-loss/40 bg-loss/10 text-loss",
  warn: "border-warn/40 bg-warn/10 text-warn",
};

const DOT_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-muted",
  accent: "bg-accent",
  gain: "bg-gain",
  loss: "bg-loss",
  warn: "bg-warn",
};

/** Compact pill for status / category labels (also a status indicator with `dot`). */
export function Badge({
  className,
  tone = "neutral",
  dot = false,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        TONE_CLASSES[tone],
        className,
      )}
      {...props}
    >
      {dot ? (
        <span
          className={cn("h-1.5 w-1.5 rounded-full", DOT_CLASSES[tone])}
          aria-hidden
        />
      ) : null}
      {children}
    </span>
  );
}
