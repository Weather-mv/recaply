import { Cpu, MemoryStick, MonitorCog } from "lucide-react";
import { Ring, Sparkline } from "@/components/ui/viz";
import { useSystem } from "@/hooks/data";
import { cn } from "@/lib/utils";

function colorFor(v: number): string {
  return v > 90 ? "#f87171" : v > 70 ? "#fbbf24" : "hsl(var(--primary))";
}

function Row({ icon: Icon, label, value, sub, history }: { icon: typeof Cpu; label: string; value: number | null; sub: string; history: number[] }) {
  const unknown = value == null;
  return (
    <div className="flex items-center gap-3">
      <Ring value={value ?? 0} label={`${label} load`} color={colorFor(value ?? 0)} />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 text-sm font-medium"><Icon className="size-3.5 text-muted-foreground" /> {label}</p>
        <p className="truncate text-xs text-muted-foreground">{unknown ? "Not available on this PC" : sub}</p>
      </div>
      <div className={cn("w-20 shrink-0", unknown && "opacity-30")}><Sparkline values={history} stroke={colorFor(value ?? 0)} /></div>
    </div>
  );
}

/** CPU / RAM / GPU of this PC as rings with a short history, refreshed every 3 seconds. */
export function SystemWidget({ className }: { className?: string }) {
  const { data, history, error } = useSystem();
  return (
    <section className={cn("surface space-y-4 rounded-2xl p-4", className)} aria-label="System status">
      <h2 className="flex items-center justify-between text-sm font-semibold">
        System
        <span className="inline-flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", error ? "bg-zinc-500" : "bg-emerald-400")} /> {error ? "Offline" : "Live"}
        </span>
      </h2>
      <Row icon={Cpu} label="CPU" value={data?.cpu ?? null} sub="Processor load" history={history.cpu} />
      <Row icon={MemoryStick} label="RAM" value={data?.ram?.percent ?? null} sub={data?.ram ? `${data.ram.used_gb} of ${data.ram.total_gb} GB` : ""} history={history.ram} />
      <Row icon={MonitorCog} label="GPU" value={data?.gpu?.percent ?? null} sub={data?.gpu ? `${data.gpu.name} · ${data.gpu.used_gb}/${data.gpu.total_gb} GB` : ""} history={history.gpu} />
    </section>
  );
}
