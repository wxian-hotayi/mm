import { useEffect } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { type ToastVariant, useToastStore } from "@/stores/toast";

const VARIANT_META: Record<
  ToastVariant,
  { icon: LucideIcon; accent: string; border: string }
> = {
  default: { icon: Info, accent: "text-muted", border: "border-border" },
  success: { icon: CheckCircle2, accent: "text-gain", border: "border-gain/40" },
  warn: { icon: AlertTriangle, accent: "text-warn", border: "border-warn/40" },
  error: { icon: XCircle, accent: "text-loss", border: "border-loss/40" },
};

/** Auto-dismiss timer for a single toast. */
function ToastItem({ id }: { id: string }) {
  const toast = useToastStore((s) => s.toasts.find((t) => t.id === id));
  const dismiss = useToastStore((s) => s.dismiss);

  useEffect(() => {
    if (!toast || toast.duration <= 0) return;
    const handle = window.setTimeout(() => dismiss(id), toast.duration);
    return () => window.clearTimeout(handle);
  }, [id, toast, dismiss]);

  if (!toast) return null;
  const meta = VARIANT_META[toast.variant];
  const Icon = meta.icon;

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto flex w-full items-start gap-3 rounded-xl border bg-surface px-4 py-3 shadow-pop animate-slide-up",
        meta.border,
      )}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", meta.accent)} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-medium text-text">{toast.title}</p>
        {toast.description ? (
          <p className="break-words text-sm text-muted">{toast.description}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => dismiss(id)}
        aria-label="Dismiss"
        className="-mr-1 flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted hover:text-text"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

/** Fixed toast viewport. Mount once near the app root. */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] mx-auto flex w-full max-w-sm flex-col gap-2 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
      {toasts.map((t) => (
        <ToastItem key={t.id} id={t.id} />
      ))}
    </div>
  );
}
