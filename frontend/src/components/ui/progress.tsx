import { cn } from "@/lib/utils";

export function Progress({ value, className, indeterminate }: { value: number; className?: string; indeterminate?: boolean }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      className={cn("relative h-2 w-full overflow-hidden rounded-full bg-secondary", className)}
    >
      <div
        className={cn("h-full rounded-full bg-primary transition-[width] duration-500", indeterminate && "w-1/3 animate-pulse")}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
    </div>
  );
}
