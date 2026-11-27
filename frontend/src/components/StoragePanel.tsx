import { CheckCircle2, HardDrive, Loader2, Trash2, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { CleanupCounts, StorageReply } from "@/lib/types";
import { cn } from "@/lib/utils";

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

const dayLabel = (d: number) => (d === 0 ? "Never" : `After ${d} days`);
const freedText = (f: CleanupCounts) =>
  `${f.projects} project${f.projects === 1 ? "" : "s"} and ${f.caches} unused cache${f.caches === 1 ? "" : "s"}, ${fmtBytes(f.project_bytes + f.cache_bytes)}`;

/** Settings → Storage: what the data folder holds, keep-for-N-days, and "Clear old projects now". */
export function StoragePanel() {
  const [data, setData] = useState<StorageReply | null>(null);
  const [ask, setAsk] = useState(30);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async (days: number) => {
    try {
      const r = await api<StorageReply>(`/api/studio/storage?days=${days}`);
      setData(r);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Couldn't read the storage." });
    }
  }, []);
  useEffect(() => {
    void load(30);
  }, [load]);

  const saveDays = async (days: number) => {
    setBusy("days");
    setMsg(null);
    try {
      const r = await api<StorageReply>("/api/studio/storage", { json: { days } });
      setData(r);
      if (days) setAsk(days);
      setMsg({ ok: true, text: days ? `Finished projects are now deleted after ${days} days.` : "Projects are kept until you delete them." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Couldn't save." });
    } finally {
      setBusy("");
    }
  };

  const clean = async () => {
    const p = data?.preview;
    if (!p || !(p.projects || p.caches)) return;
    if (!window.confirm(`Delete ${freedText(p)}? Finished projects older than ${ask} days and their clips are removed for good.`)) return;
    setBusy("clean");
    setMsg(null);
    try {
      const r = await api<{ freed: CleanupCounts }>("/api/studio/storage/clean", { json: { days: ask } });
      setMsg({ ok: true, text: `Deleted ${freedText(r.freed)}.` });
      await load(ask);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "The clean-up failed." });
    } finally {
      setBusy("");
    }
  };

  const u = data?.usage;
  const p = data?.preview;
  return (
    <Card className="space-y-4 p-5">
      <h2 className="flex items-center gap-2 text-lg font-bold"><HardDrive className="size-5 text-primary" /> Storage</h2>
      {!data && !msg && <Loader2 className="size-5 animate-spin text-primary" />}
      {u && (
        <p className="text-sm">
          Clipforge is using <b>{fmtBytes(u.total)}</b>: projects {fmtBytes(u.projects)}, analysis cache {fmtBytes(u.cache)}
          {u.live ? `, live recordings ${fmtBytes(u.live)}` : ""}, other {fmtBytes(u.other)}.
        </p>
      )}
      {data && (
        <div className="space-y-2">
          <Label htmlFor="keep-days" className="text-base font-semibold text-foreground">Delete finished projects</Label>
          <select
            id="keep-days"
            className="h-10 w-full rounded-lg border bg-background px-3 text-sm sm:w-64"
            value={data.days}
            disabled={busy !== ""}
            onChange={(e) => void saveDays(Number(e.target.value))}
          >
            {data.choices.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
          </select>
          <p className="text-sm text-muted-foreground">Checked when the app starts and every 6 hours. Running projects, live recordings and videos you opened from elsewhere on this PC are never touched.</p>
        </div>
      )}
      {data && (
        <div className="space-y-2 border-t pt-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Label htmlFor="clean-days" className="mb-0 text-base font-semibold text-foreground">Clear projects older than</Label>
            <select
              id="clean-days"
              className="h-10 rounded-lg border bg-background px-3 text-sm"
              value={ask}
              disabled={busy !== ""}
              onChange={(e) => {
                const d = Number(e.target.value);
                setAsk(d);
                void load(d);
              }}
            >
              {data.choices.filter((d) => d > 0).map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
            <Button type="button" variant="destructive" onClick={clean} disabled={busy !== "" || !p || !(p.projects || p.caches)}>
              {busy === "clean" ? <Loader2 className="animate-spin" /> : <Trash2 />} Clear old projects
            </Button>
          </div>
          {p && <p className="text-sm text-muted-foreground">{p.projects || p.caches ? `This would free ${fmtBytes(p.project_bytes + p.cache_bytes)} (${freedText(p)}).` : `Nothing is older than ${ask} days.`}</p>}
        </div>
      )}
      {msg && (
        <p role="status" className={cn("flex items-start gap-2 text-sm font-medium", msg.ok ? "text-beat-payoff" : "text-destructive")}>
          {msg.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <TriangleAlert className="mt-0.5 size-4 shrink-0" />} {msg.text}
        </p>
      )}
    </Card>
  );
}
