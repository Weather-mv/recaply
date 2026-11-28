import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Select } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";

interface UsageReply {
  totals: { jobs: number; failed: number; video_hours: number; sec_per_video_hour: number | null; usd: number; usd_per_video: number | null; clips: number };
  by_day: { day: string; jobs: number; usd: number }[];
  providers: Record<string, { kind: string; usd: number; calls: number; tokens_in: number; tokens_out: number; minutes: number }>;
  stages: { name: string; sec: number; cached: number; share: number }[];
  jobs: { id: string; title: string; video_min: number; processing_sec: number; usd: number; clips: number }[];
  publish?: Record<string, number>;
  prices_note: string;
}

const usd = (v: number | null) => (v == null ? "–" : v > 0 && v < 0.01 ? "<$0.01" : `$${v.toFixed(2)}`);
const dur = (s: number | null) => (s == null ? "–" : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-muted-foreground">
          <tr>{head.map((h, i) => <th key={h} className={`pb-2 font-medium ${i ? "text-right" : ""}`}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((r, i) => (
            <tr key={i} className="border-t border-white/[.06]">{r.map((c, j) => <td key={j} className={`py-2 ${j ? "text-right tabular-nums" : ""}`}>{c}</td>)}</tr>
          )) : <tr><td colSpan={head.length} className="py-3 text-muted-foreground">Nothing yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

const Panel = ({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) => (
  <section className="surface min-w-0 rounded-2xl p-4">
    <h2 className="mb-3 text-sm font-semibold">{title}</h2>
    {children}
    {note && <p className="mt-3 text-xs text-muted-foreground">{note}</p>}
  </section>
);

export default function Usage() {
  const [days, setDays] = useState("30");
  const [d, setD] = useState<UsageReply | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setD(null);
    api<UsageReply>(`/api/usage?days=${days}`)
      .then((r) => live && setD(r))
      .catch((e) => live && setError(e instanceof ApiError ? e.message : "Couldn't load usage."));
    return () => {
      live = false;
    };
  }, [days]);

  const t = d?.totals;
  const tiles: [string, string][] = t
    ? [
        [`${t.jobs}${t.failed ? ` (${t.failed} failed)` : ""}`, "videos analysed"],
        [`${t.video_hours} h`, "of video"],
        [dur(t.sec_per_video_hour), "processing per video hour"],
        [usd(t.usd), "estimated spend"],
        [usd(t.usd_per_video), "per video"],
        [String(t.clips), "clips found"],
      ]
    : [];
  const max = Math.max(1, ...(d?.by_day.map((x) => x.jobs) ?? [1]));

  return (
    <AppShell crumbs={[{ label: "Usage" }]}>
      <div className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Time and cost</h1>
            <p className="mt-1 text-muted-foreground">What your videos took to process, and the estimated spend.</p>
          </div>
          <Select value={days} onChange={(e) => setDays(e.target.value)} aria-label="Period" className="w-44">
            <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="365">Last year</option>
          </Select>
        </div>
        {error && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        {!d && !error && <div className="grid place-items-center py-16"><Loader2 className="size-7 animate-spin text-primary" /></div>}
        {d && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              {tiles.map(([v, l]) => (
                <div key={l} className="surface rounded-xl p-4"><p className="text-2xl font-bold tabular-nums">{v}</p><p className="mt-0.5 text-xs text-muted-foreground">{l}</p></div>
              ))}
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel title="Videos per day" note={d.by_day.length ? `${d.by_day[0].day} to ${d.by_day[d.by_day.length - 1].day}` : "No videos in this period."}>
                <div className="flex h-28 items-end gap-1" role="img" aria-label="Videos per day">
                  {d.by_day.map((x) => <div key={x.day} title={`${x.day}: ${x.jobs} video(s), ${usd(x.usd)}`} className="min-h-[2px] flex-1 rounded-t bg-gradient-to-t from-primary/60 to-primary" style={{ height: `${(100 * x.jobs) / max}%` }} />)}
                </div>
              </Panel>
              <Panel title="Where the cost goes" note={d.prices_note}>
                <Table head={["Provider", "Usage", "Est. cost"]} rows={Object.entries(d.providers).map(([k, v]) => [k, v.kind === "audio" ? `${v.minutes} min` : `${v.calls} calls, ${(v.tokens_in + v.tokens_out).toLocaleString()} tokens`, usd(v.usd)])} />
              </Panel>
              <Panel title="Where the time goes">
                <Table head={["Stage", "Share", "Time"]} rows={d.stages.slice(0, 12).map((s) => [
                  s.name + (s.cached ? " (cache)" : ""),
                  <div key={s.name} className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-white/[.07]"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(s.share * 100)}%` }} /></div>,
                  dur(s.sec),
                ])} />
              </Panel>
              {d.publish && <Panel title="Publishing"><Table head={["Status", "Items"]} rows={Object.entries(d.publish).map(([k, v]) => [k, v])} /></Panel>}
            </div>
            <Panel title="Jobs">
              <Table head={["Video", "Length", "Processing", "Cost", "Clips"]} rows={d.jobs.map((j) => [<Link key={j.id} to={`/p/${j.id}`} className="hover:text-primary">{j.title}</Link>, `${j.video_min} min`, dur(j.processing_sec), usd(j.usd), j.clips])} />
            </Panel>
          </>
        )}
      </div>
    </AppShell>
  );
}
