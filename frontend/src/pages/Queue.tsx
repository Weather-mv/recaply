import { AlertCircle, CheckCircle2, ListPlus, Loader2, RotateCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { useBoot } from "@/hooks/data";
import { api, ApiError } from "@/lib/api";
import type { JobState } from "@/lib/types";
import { ago, cn } from "@/lib/utils";

interface QueueJob {
  id: string;
  title: string;
  input: string;
  state: JobState;
  stage: string;
  progress: number;
  created: number;
  error: string | null;
}
interface PublishItem {
  id: number;
  title?: string;
  job_title?: string;
  platform: string;
  status: string;
  run_at: number;
  error?: string | null;
  attempts?: number;
  result?: { url?: string } | null;
}
interface QueueReply {
  jobs: QueueJob[];
  counts: Record<string, number>;
  publish: PublishItem[];
}
interface BatchReply {
  results: { input: string; job?: string; error?: string }[];
  started: number;
}

const TONE: Record<string, string> = {
  ready: "bg-emerald-500/10 text-emerald-400 ring-emerald-400/20",
  done: "bg-emerald-500/10 text-emerald-400 ring-emerald-400/20",
  running: "bg-primary/15 text-primary ring-primary/25",
  uploading: "bg-primary/15 text-primary ring-primary/25",
  queued: "bg-amber-400/10 text-amber-300 ring-amber-300/20",
  failed: "bg-red-500/10 text-red-400 ring-red-400/20",
  cancelled: "bg-zinc-500/10 text-zinc-400 ring-zinc-400/20",
};
const Pill = ({ s, children }: { s: string; children?: React.ReactNode }) => (
  <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset", TONE[s] ?? "bg-white/5 text-muted-foreground ring-white/10")}>{children ?? s}</span>
);

function useQueue() {
  const [data, setData] = useState<QueueReply | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await api<QueueReply>("/api/queue"));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach Clipforge.");
    }
  }, []);
  useEffect(() => {
    void load();
    const t = window.setInterval(load, 2500);
    return () => window.clearInterval(t);
  }, [load]);
  return { data, error, load };
}

function JobRow({ j, onChange, onError }: { j: QueueJob; onChange: () => void; onError: (m: string) => void }) {
  const run = async (path: string, method?: string) => {
    try {
      await api(path, { method: method ?? "POST" });
      onChange();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };
  const done = j.state === "ready" || j.state === "failed" || j.state === "cancelled";
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/[.06] px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <Link to={`/p/${j.id}`} className="line-clamp-1 font-medium hover:text-primary">{j.title}</Link>
        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
          {ago(j.created)} · {j.state === "running" || j.state === "queued" ? j.stage : j.error || j.stage}
        </p>
        {j.state === "running" && (
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[.07]">
            <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${Math.round(j.progress * 100)}%` }} />
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Pill s={j.state} />
        {j.state === "queued" && <Button size="sm" variant="secondary" onClick={() => run(`/api/queue/${j.id}/cancel`)}><X /> Cancel</Button>}
        {(j.state === "failed" || j.state === "cancelled") && <Button size="sm" variant="secondary" onClick={() => run(`/api/clipjob/${j.id}/retry`)}><RotateCw /> Retry</Button>}
        {done && (
          <Button size="sm" variant="ghost" aria-label={`Delete ${j.title}`} className="px-2 text-muted-foreground hover:text-destructive" onClick={() => window.confirm("Delete this job and its clips?") && run(`/api/clipjob/${j.id}`, "DELETE")}>
            <Trash2 />
          </Button>
        )}
      </div>
    </li>
  );
}

function PublishRow({ it, onChange }: { it: PublishItem; onChange: () => void }) {
  const act = async (a: string) => {
    try {
      await api(`/api/publish/${it.id}/${a}`, { method: "POST" });
    } finally {
      onChange();
    }
  };
  const when = new Date(it.run_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/[.06] px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="line-clamp-1 font-medium">{it.title || it.job_title} → {it.platform}</p>
        <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
          {it.status === "queued" ? "at " : ""}{when}
          {it.status === "done" && it.result?.url ? <> · <a href={it.result.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{it.result.url}</a></> : null}
          {it.error ? ` · ${it.error}` : ""}
          {it.attempts && it.status !== "done" ? ` (try ${it.attempts})` : ""}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Pill s={it.status === "uploading" ? "running" : it.status}>{it.status}</Pill>
        {(it.status === "queued" || it.status === "failed") && <Button size="sm" variant="secondary" onClick={() => act("cancel")}>Cancel</Button>}
        {(it.status === "failed" || it.status === "cancelled") && <Button size="sm" variant="secondary" onClick={() => act("retry")}>Retry</Button>}
      </div>
    </li>
  );
}

function BatchForm({ onStarted }: { onStarted: () => void }) {
  const { session } = useBoot();
  const box = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [profile, setProfile] = useState("auto");
  const [count, setCount] = useState("10");
  const [length, setLength] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ ok: boolean; started?: number; total?: number; bad: string[]; msg?: string } | null>(null);

  const start = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setOut(null);
    try {
      const d = await api<BatchReply>("/api/batch", { json: { inputs: text, options: { profile, count, length } } });
      setOut({ ok: true, started: d.started, total: d.results.length, bad: d.results.filter((r) => r.error).map((r) => `${r.input.slice(0, 60)}: ${r.error}`) });
      if (d.started) setText("");
      onStarted();
    } catch (e) {
      setOut({ ok: false, bad: [], msg: e instanceof ApiError ? e.message : "Something went wrong." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="surface space-y-3 rounded-2xl p-4 xl:sticky xl:top-20">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><ListPlus className="size-4 text-primary" /> Add many videos</h2>
      <textarea
        ref={box}
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-label="Links, one per line"
        spellCheck={false}
        placeholder={"One link per line" + (session?.desktop ? " (or a file path)" : "") + "\nhttps://www.youtube.com/watch?v=…\nhttps://www.twitch.tv/videos/…"}
        className="min-h-[9rem] w-full resize-y rounded-lg border border-input bg-white/[.03] p-3 text-sm outline-none placeholder:text-muted-foreground/70 focus-visible:border-primary/60"
      />
      <Select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Content type">
        <option value="auto">Content: auto-detect</option>
        {session?.profiles.filter((p) => !p.hidden).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
      </Select>
      <div className="grid grid-cols-2 gap-2">
        <Select value={count} onChange={(e) => setCount(e.target.value)} aria-label="Clips each">
          {[5, 10, 20].map((n) => <option key={n} value={n}>{n} clips each</option>)}
        </Select>
        <Select value={length} onChange={(e) => setLength(e.target.value)} aria-label="Clip length">
          <option value="auto">Length: auto</option><option value="short">15–30 s</option><option value="medium">30–60 s</option><option value="long">60–90 s</option>
        </Select>
      </div>
      <Button className="w-full" size="lg" disabled={busy || !text.trim()} onClick={start}>
        {busy ? <Loader2 className="animate-spin" /> : null} Start all
      </Button>
      {out && (
        <div role="status" className="space-y-1 text-sm">
          {out.ok ? (
            <p className="flex items-center gap-2 font-medium text-emerald-400"><CheckCircle2 className="size-4" /> Started {out.started} of {out.total}.</p>
          ) : (
            <p className="flex items-center gap-2 font-medium text-destructive"><AlertCircle className="size-4" /> {out.msg}</p>
          )}
          {out.bad.map((b) => <p key={b} className="break-words text-xs text-destructive">{b}</p>)}
        </div>
      )}
      <p className="text-xs text-muted-foreground">Jobs run a few at a time. Only add videos you have the right to clip.</p>
    </aside>
  );
}

export default function Queue() {
  const { session } = useBoot();
  const { data, error, load } = useQueue();
  const [note, setNote] = useState("");
  const jobs = data?.jobs ?? [];
  const counts = Object.entries(data?.counts ?? {});

  const retryAll = async () => {
    try {
      const d = await api<{ jobs: string[] }>("/api/queue/retry-failed", { method: "POST" });
      setNote(`Retrying ${d.jobs.length} job(s).`);
      void load();
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  return (
    <AppShell crumbs={[{ label: "Queue" }]}>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Queue</h1>
          <p className="mt-1 text-muted-foreground">Batch processing{session?.desktop ? " and publishing" : ""}.</p>
        </div>
        {error && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-6">
            <section className="surface overflow-hidden rounded-2xl">
              <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-3">
                {counts.length ? counts.map(([k, v]) => <Pill key={k} s={k}>{v} {k}</Pill>) : <span className="text-sm text-muted-foreground">No jobs yet</span>}
                <Button size="sm" variant="secondary" className="ml-auto" onClick={retryAll}><RotateCw /> Retry all failed</Button>
              </div>
              {!data ? (
                <div className="grid place-items-center p-10"><Loader2 className="size-6 animate-spin text-primary" /></div>
              ) : jobs.length ? (
                <ul>{jobs.map((j) => <JobRow key={j.id} j={j} onChange={load} onError={setNote} />)}</ul>
              ) : (
                <p className="p-8 text-center text-sm text-muted-foreground">No jobs yet. Add videos on the right.</p>
              )}
              {note && <p role="status" className="border-t border-white/10 px-4 py-2 text-sm text-muted-foreground">{note}</p>}
            </section>

            {session?.desktop && (
              <section>
                <h2 className="mb-3 text-lg font-semibold">Publishing schedule</h2>
                <div className="surface overflow-hidden rounded-2xl">
                  {data?.publish.length ? <ul>{data.publish.map((it) => <PublishRow key={it.id} it={it} onChange={load} />)}</ul> : <p className="p-6 text-center text-sm text-muted-foreground">Nothing scheduled. Publish a short from the Director.</p>}
                </div>
              </section>
            )}
          </div>
          <BatchForm onStarted={load} />
        </div>
      </div>
    </AppShell>
  );
}
