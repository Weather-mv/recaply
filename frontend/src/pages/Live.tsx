import { AlertCircle, Bell, CheckCircle2, Clapperboard, Loader2, Radio, Scissors, Square, Trash2, Play, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { useLive } from "@/hooks/data";
import { api, ApiError } from "@/lib/api";
import type { LiveChannel, LiveClip, LiveEvent, LiveState } from "@/lib/types";
import { ago, cn, fmtTime } from "@/lib/utils";

const CLIP_LENGTHS = [30, 60, 90];

const STATE: Record<LiveState, { label: string; variant: "success" | "default" | "warning" | "destructive" | "secondary" | "outline" }> = {
  recording: { label: "Recording", variant: "success" },
  waiting: { label: "Waiting for the stream", variant: "default" },
  starting: { label: "Starting", variant: "default" },
  idle: { label: "Not watching", variant: "outline" },
  stopped: { label: "Stopped", variant: "secondary" },
  ended: { label: "Stream ended", variant: "secondary" },
  error: { label: "Error", variant: "destructive" },
};

function AddChannel({ profiles, onAdded }: { profiles: { key: string; label: string }[]; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [profile, setProfile] = useState("gaming");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api<{ live?: boolean }>("/api/studio/projects", { json: { url: url.trim(), profile, live_only: true } });
      setUrl("");
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2 rounded-[1.5rem] border bg-card p-3 shadow-lg">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste a live link… Twitch, Kick, YouTube Live" aria-label="Live stream link" spellCheck={false} className="h-12 flex-1 border-0 bg-transparent text-base shadow-none focus-visible:ring-0" />
        <Select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Content type" className="h-12 sm:w-56">
          {profiles.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </Select>
        <Button type="submit" size="lg" disabled={busy || !url.trim()}>
          {busy ? <Loader2 className="animate-spin" /> : <Radio />} Watch live
        </Button>
      </div>
      {error && <p role="alert" className="flex items-center gap-2 px-3 text-sm font-medium text-destructive"><AlertCircle className="size-4" /> {error}</p>}
    </form>
  );
}

function Moment({ c }: { c: LiveClip }) {
  const signals = Object.entries(c.signals ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border bg-background/50 px-3 py-2 text-sm">
      <span className="tabular font-semibold">{c.clock}</span>
      {c.manual ? <Badge variant="secondary"><Scissors className="size-3" /> Clipped by you</Badge> : <Badge variant="warning">Score {c.score.toFixed(2)}</Badge>}
      {signals.map(([k, v]) => <span key={k} className="text-muted-foreground">{k.replace(/_/g, " ")} {v}</span>)}
      <span className="ml-auto flex items-center gap-2">
        {c.error ? <span className="text-destructive">{c.error}</span> : c.job ? (
          <Link to={`/p/${c.job}`} className="inline-flex items-center gap-1.5 font-semibold text-primary hover:underline"><Clapperboard className="size-4" /> Open clip</Link>
        ) : <span className="text-muted-foreground">Cut</span>}
      </span>
    </li>
  );
}

function Channel({ ch, onChange }: { ch: LiveChannel; onChange: () => void }) {
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const st = STATE[ch.state] ?? STATE.idle;
  const recording = ch.state === "recording";
  const running = ch.alive && ch.state !== "ended" && ch.state !== "stopped" && ch.state !== "error";
  const pct = ch.buffer_limit_mb ? Math.min(1, ch.buffer_mb / ch.buffer_limit_mb) : 0;

  const act = async (what: string, path: string, json?: unknown, method?: string) => {
    setBusy(what);
    setMsg(null);
    try {
      await api(path, { method, json });
      if (what.startsWith("clip")) setMsg({ ok: true, text: "Clipped. It's being ranked and titled now." });
      onChange();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "That didn't work." });
    } finally {
      setBusy("");
    }
  };
  const base = `/api/live/channels/${encodeURIComponent(ch.name)}`;

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-lg font-bold">{ch.name}</h3>
          <a href={ch.url} target="_blank" rel="noreferrer" className="block truncate text-sm text-muted-foreground hover:underline">{ch.url}</a>
        </div>
        <Badge variant={ch.reconnecting ? "warning" : st.variant}>
          {recording && !ch.reconnecting && <span className="size-2 animate-pulse rounded-full bg-current" />}
          {ch.reconnecting ? "Reconnecting…" : st.label}
        </Badge>
        {running ? (
          <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => act("stop", `${base}/stop`)}>
            {busy === "stop" ? <Loader2 className="animate-spin" /> : <Square />} Stop
          </Button>
        ) : (
          <Button size="sm" disabled={!!busy} onClick={() => act("start", `${base}/start`)}>
            {busy === "start" ? <Loader2 className="animate-spin" /> : <Play />} Start
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={!!busy || running} aria-label={`Remove ${ch.name}`} onClick={() => act("remove", base, undefined, "DELETE")}>
          <Trash2 />
        </Button>
      </div>

      {ch.note && <p className={cn("flex items-start gap-2 text-sm", ch.reconnecting || ch.state === "error" ? "text-beat-build" : "text-muted-foreground")}>{ch.reconnecting ? <WifiOff className="mt-0.5 size-4 shrink-0" /> : null}{ch.note}</p>}

      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div><dt className="text-muted-foreground">Recorded</dt><dd className="text-lg font-bold tabular">{fmtTime(ch.recording_sec)}</dd></div>
        <div><dt className="text-muted-foreground">Moments caught</dt><dd className="text-lg font-bold tabular">{ch.spikes}</dd></div>
        <div><dt className="text-muted-foreground">Hype right now</dt><dd className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-secondary" role="meter" aria-label="Hype right now" aria-valuemin={0} aria-valuemax={1} aria-valuenow={ch.score}><div className={cn("h-full rounded-full transition-[width]", ch.score >= 0.5 ? "bg-beat-payoff" : "bg-primary")} style={{ width: `${Math.min(100, ch.score * 100)}%` }} /></dd></div>
        <div>
          <dt className="text-muted-foreground">Chat</dt>
          <dd className="flex items-center gap-1.5 font-semibold">
            {ch.chat ? (ch.chat.connected ? <><Wifi className="size-4 text-beat-payoff" /> {ch.chat.messages} msgs</> : <><WifiOff className="size-4 text-muted-foreground" /> offline</>) : <span className="text-muted-foreground">not used</span>}
          </dd>
        </div>
      </dl>

      <div>
        <div className="mb-1 flex items-center justify-between text-sm text-muted-foreground">
          <span>Rolling buffer on disk: last {ch.retention_min} min</span>
          <span className="tabular">{ch.buffer_mb.toFixed(0)} / {ch.buffer_limit_mb} MB</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-secondary" role="meter" aria-label="Buffer size" aria-valuemin={0} aria-valuemax={ch.buffer_limit_mb} aria-valuenow={ch.buffer_mb}>
          <div className={cn("h-full rounded-full", pct > 0.9 ? "bg-beat-build" : "bg-primary/70")} style={{ width: `${pct * 100}%` }} />
        </div>
        {ch.reconnects > 0 && <p className="mt-1 text-xs text-muted-foreground">Reconnected {ch.reconnects} time{ch.reconnects === 1 ? "" : "s"} so far.</p>}
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-secondary/40 p-3">
        <span className="mr-1 flex items-center gap-1.5 text-sm font-semibold"><Scissors className="size-4" /> Clip the last</span>
        {CLIP_LENGTHS.map((s) => (
          <Button key={s} size="sm" variant="secondary" disabled={!recording || !!busy} onClick={() => act(`clip${s}`, `${base}/clip`, { seconds: s })}>
            {busy === `clip${s}` ? <Loader2 className="animate-spin" /> : null} {s} s
          </Button>
        ))}
        {!recording && <span className="text-sm text-muted-foreground">Available while recording.</span>}
      </div>
      {msg && (
        <p role="status" className={cn("flex items-start gap-2 text-sm font-medium", msg.ok ? "text-beat-payoff" : "text-destructive")}>
          {msg.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <AlertCircle className="mt-0.5 size-4 shrink-0" />} {msg.text}
        </p>
      )}

      {ch.clips.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Captured moments</h4>
          <ul className="space-y-1.5">{[...ch.clips].reverse().map((c, i) => <Moment key={`${c.t}-${i}`} c={c} />)}</ul>
        </div>
      )}
    </Card>
  );
}

function Feed({ events }: { events: LiveEvent[] }) {
  if (!events.length) return null;
  return (
    <Card className="p-5">
      <h2 className="mb-3 flex items-center gap-2 text-lg font-bold"><Bell className="size-5 text-primary" /> Activity</h2>
      <ul className="space-y-2">
        {[...events].reverse().slice(0, 15).map((e) => (
          <li key={e.id} className="flex gap-3 text-sm">
            <span className="w-16 shrink-0 text-muted-foreground tabular">{ago(e.ts)}</span>
            <span className="min-w-0"><span className={cn("font-semibold", e.kind === "error" && "text-destructive")}>{e.title ?? e.kind}</span>{e.text ? <span className="text-muted-foreground"> · {e.text}</span> : null}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function Live() {
  const { data, error, loading, refresh } = useLive();
  const unavailable = error?.status === 404;
  return (
    <AppShell>
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Live</h1>
          <p className="mt-1 text-muted-foreground">Clipforge records a stream while it happens, catches the big moments and turns each one into a clip.</p>
        </div>

        {unavailable ? (
          <Card className="p-8 text-center">
            <p className="text-lg font-bold">Live mode isn't available here</p>
            <p className="mt-1 text-sm text-muted-foreground">Recording needs the PC that runs Clipforge. Open Clipforge on that computer to use it.</p>
          </Card>
        ) : (
          <>
            <AddChannel profiles={data?.profiles ?? [{ key: "gaming", label: "Gaming" }]} onAdded={refresh} />
            {error && !unavailable && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">{error.message}</p>}
            {loading && !data ? (
              <div className="grid place-items-center py-16"><Loader2 className="size-8 animate-spin text-primary" /></div>
            ) : data && data.channels.length ? (
              <div className="space-y-5">{data.channels.map((c) => <Channel key={c.name} ch={c} onChange={refresh} />)}</div>
            ) : (
              <Card className="grid place-items-center gap-3 p-12 text-center">
                <Radio className="size-10 text-muted-foreground" />
                <p className="text-lg font-semibold">No streams yet</p>
                <p className="max-w-sm text-sm text-muted-foreground">Paste a live link above. If the channel is offline, Clipforge waits and starts when it goes live.</p>
              </Card>
            )}
            <Feed events={data?.events ?? []} />
          </>
        )}
      </div>
    </AppShell>
  );
}
