import { cn } from "@/lib/utils";

/** Tiny line graph of the last few readings (0-100). Decorative: the number next to it carries the value. */
export function Sparkline({ values, className, stroke = "hsl(var(--primary))" }: { values: number[]; className?: string; stroke?: string }) {
  const w = 120;
  const h = 32;
  if (values.length < 2) return <svg viewBox={`0 0 ${w} ${h}`} className={cn("h-8 w-full", className)} aria-hidden="true" />;
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => [i * step, h - 2 - (Math.min(100, Math.max(0, v)) / 100) * (h - 4)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const id = `sg-${stroke.replace(/\W/g, "")}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-8 w-full", className)} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={stroke} stopOpacity=".35" />
          <stop offset="1" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w},${h} L0,${h} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** A progress ring with the value in the middle. */
export function Ring({ value, size = 56, label, color = "hsl(var(--primary))" }: { value: number; size?: number; label?: string; color?: string }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.min(100, Math.max(0, value));
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v)}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(0 0% 100% / .08)" strokeWidth="5" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - v / 100)} className="transition-[stroke-dashoffset] duration-700" />
      </svg>
      <span className="absolute text-xs font-bold tabular-nums">{Math.round(v)}%</span>
    </div>
  );
}

/** A status dot; `pulse` makes it breathe (recording, waiting). */
export function StatusDot({ tone, pulse, className }: { tone: "green" | "red" | "yellow" | "blue" | "gray"; pulse?: boolean; className?: string }) {
  const color = { green: "bg-emerald-400 text-emerald-400", red: "bg-red-500 text-red-500", yellow: "bg-amber-400 text-amber-400", blue: "bg-primary text-primary", gray: "bg-zinc-500 text-zinc-500" }[tone];
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", color, pulse && "dot-live", className)} aria-hidden="true" />;
}
