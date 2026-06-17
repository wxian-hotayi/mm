import { forwardRef } from "react";

import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";

export type ButtonVariant =
  | "default"
  | "secondary"
  | "ghost"
  | "destructive"
  | "outline";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Show a spinner and disable interaction. */
  loading?: boolean;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  default:
    "bg-accent text-accent-fg hover:bg-accent-muted active:bg-accent-muted",
  secondary: "bg-surface2 text-text hover:bg-border active:bg-border",
  ghost: "bg-transparent text-text hover:bg-surface2 active:bg-surface2",
  destructive: "bg-loss/90 text-white hover:bg-loss active:bg-loss",
  outline:
    "border border-border bg-transparent text-text hover:bg-surface2 active:bg-surface2",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "h-9 px-3 text-sm gap-1.5",
  md: "h-11 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-base gap-2",
  icon: "h-11 w-11",
};

/** Touch-friendly button (min-h-11 for md/lg) with variants and a loading state. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { className, variant = "default", size = "md", loading = false, disabled, children, ...props },
    ref,
  ) {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex select-none items-center justify-center whitespace-nowrap rounded-xl font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          "disabled:pointer-events-none disabled:opacity-50",
          VARIANT_CLASSES[variant],
          SIZE_CLASSES[size],
          className,
        )}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading ? <Spinner size="sm" /> : null}
        {children}
      </button>
    );
  },
);
