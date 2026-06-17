import { cn } from "@/lib/utils";

/** Pulsing placeholder shown while data loads. */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-lg bg-surface2", className)}
      aria-hidden
      {...props}
    />
  );
}
