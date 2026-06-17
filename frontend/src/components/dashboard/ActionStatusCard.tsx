/**
 * Action Status hero (DESIGN §20.3 #1, §20.0). The largest, first-visible
 * surface on the Dashboard. It MIRRORS the backend decision verbatim — status,
 * headline, reasons and primary action all come from `ActionStatusOut`; the UI
 * never computes which status applies. DO_NOTHING is the calm green default
 * success state; REVIEW_REQUIRED is amber attention; REBALANCE_NOW is a
 * deliberate (not alarming) call-to-act linking to the Execution Center.
 */

import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ACTION_STATUS_META } from "@/lib/constants";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ActionStatusOut, ActionStatusValue } from "@/types/api";

export interface ActionStatusCardProps {
  data: ActionStatusOut;
}

/** Reason-severity tones never escalate the calm hero treatment on their own. */
const REASON_DOT: Record<string, string> = {
  INFO: "bg-muted",
  WARN: "bg-warn",
  BLOCK: "bg-loss",
};

export function ActionStatusCard({ data }: ActionStatusCardProps) {
  const navigate = useNavigate();
  const meta = ACTION_STATUS_META[data.status as ActionStatusValue];
  const Icon = meta.icon;
  const title = data.label || meta.label;
  // §20.3 / §20.9: the Execution-Center call-to-act is reserved for the
  // deliberate states (REBALANCE_NOW / REVIEW_REQUIRED). DO_NOTHING is a calm
  // success with no prompt — gate on status only (the backend always returns a
  // non-empty primary_action, so its length must never drive this).
  const showExecutionLink =
    data.status === "REBALANCE_NOW" || data.status === "REVIEW_REQUIRED";

  return (
    <Card
      className={cn("border-2 p-5 sm:p-7", meta.surfaceClass)}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-start gap-4">
          <div
            className={cn(
              "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-surface/70",
              meta.accentClass,
            )}
            aria-hidden
          >
            <Icon className="h-7 w-7" />
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-widest text-muted">
              Action Status
            </span>
            <h2
              className={cn(
                "text-2xl font-bold leading-tight sm:text-3xl",
                meta.accentClass,
              )}
            >
              {title}
            </h2>
          </div>
        </div>

        {data.headline ? (
          <p className="text-base font-medium leading-snug text-text">
            {data.headline}
          </p>
        ) : null}

        {data.reasons.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {data.reasons.map((reason) => (
              <li
                key={reason.code}
                className="flex items-start gap-2.5 text-sm text-muted"
              >
                <span
                  className={cn(
                    "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                    REASON_DOT[reason.severity] ?? "bg-muted",
                  )}
                  aria-hidden
                />
                <span className="leading-snug">{reason.message}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {data.primary_action ? (
          <div className="rounded-xl border border-border bg-surface/60 px-4 py-3">
            <span className="block text-xs font-semibold uppercase tracking-wide text-muted">
              Next action
            </span>
            <span className="mt-0.5 block text-sm font-medium text-text">
              {data.primary_action}
            </span>
          </div>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-xs text-muted">
            Updated {fmtDateTime(data.computed_at)}
          </span>
          {showExecutionLink ? (
            <Button
              size="md"
              className="w-full sm:w-auto"
              onClick={() => navigate("/execution")}
            >
              Open Execution Center
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
