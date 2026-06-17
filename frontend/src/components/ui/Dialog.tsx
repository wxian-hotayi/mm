import { useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: React.ReactNode;
  /** Footer actions (e.g. cancel / confirm buttons). */
  footer?: React.ReactNode;
  className?: string;
}

/**
 * Centered modal rendered in a portal. Closes on Esc and overlay click, locks
 * body scroll, and traps initial focus. No Radix dependency (Decision Log 5).
 * On small screens it sits near the bottom for thumb reach.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return;
    document.addEventListener("keydown", handleKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, handleKey]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="absolute inset-0 animate-fade-in bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          "relative z-10 max-h-[90dvh] w-full overflow-y-auto rounded-t-2xl border border-border bg-surface shadow-pop",
          "animate-slide-up sm:max-w-lg sm:rounded-2xl sm:animate-scale-in",
          "focus:outline-none",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-3 p-4 sm:p-5">
          <div className="flex flex-col gap-1">
            {title ? (
              <h2 className="text-base font-semibold text-text">{title}</h2>
            ) : null}
            {description ? (
              <p className="text-sm text-muted">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 -mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface2 hover:text-text"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="px-4 pb-4 sm:px-5 sm:pb-5">{children}</div>
        {footer ? (
          <div className="flex flex-col-reverse gap-2 border-t border-border p-4 sm:flex-row sm:justify-end sm:p-5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
