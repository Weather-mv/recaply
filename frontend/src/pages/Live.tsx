import { AlertCircle, Bell, CheckCircle2, ChevronDown, Clapperboard, Link2, Loader2, Play, Radio, Scissors, Square, Trash2, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { StatusDot } from "@/components/ui/viz";
import { useLive } from "@/hooks/data";
import { api, ApiError } from "@/lib/api";
import type { LiveChannel, LiveClip, LiveEvent, LiveState } from "@/lib/types";
import { ago, cn, fmtTime } from "@/lib/utils";

const CLIP_LENGTHS = [30, 60, 90];

type Tone = "green" | "red" | "yellow" | "blue" | "gray";
const STATE: Record<LiveState, { label: string; tone: Tone; pulse?: boolean }> = {
  recording: { label: "Recording", tone: "red", pulse: true },
  waiting: { label: "Waiting for stream", tone: "yellow", pulse: true },
  starting: { label: "Starting", tone: "blue", pulse: true },
  idle: { label: "Not watching", tone: "gray" },
  stopped: { label: "Stopped", tone: "gray" },
  ended: { label: "Stream ended", tone: "gray" },
  error: { label: "Error", tone: "red" },
};

/** A public preview picture for the stream when the site offers one (Twitch live previews, YouTube live thumbnails). */
export function streamThumb(url: string): string | null {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    if (host === "twitch.tv") {
      const login = u.pathname.split("/").filter(Boolean)[0];
      return login ? `https://static-cdn.jtvnw.net/previews-ttv/live_user_${login.toLowerCase()}-640x360.jpg` : null;
    }
    if (host === "youtube.com" || host === "youtu.be") {
      const id = host === "youtu.be" ? u.pathname.slice(1) : u.searchParams.get("v");
      return id ? `https://i.ytimg.com/vi/${id}/mqdefault_live.jpg` : null;
    }
  } catch {
    /* not a URL */
  }
  return null;
}

export function platformOf(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    if (host.includes("twitch")) return "Twitch";
    if (host.includes("kick")) return "Kick";
    if (host.includes("youtu")) return "YouTube";
    return host;
  } catch {
    return "Stream";
  }
}

const PLATFORM_TINT: Record<string, string> = {
  Twitch: "from-violet-600/40 to-fuchsia-900/30",
  Kick: "from-green-500/35 to-emerald-900/30",
  YouTube: "from-red-600/35 to-rose-900/30",
};

function CommandBar({ profiles, onAdded }: { profiles: { key: string; label: string }[]; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [profile, setProfile] = useState("gaming");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nav = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ live?: boolean; id?: string }>("/api/studio/projects", { json: { url: url.trim(), profile, live_only: true } });
      setUrl("");
      if (r.id) {
        nav(`/p/${r.id}`); // the stream has ended: it was started as a normal video project
        return;
      }
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <div className="surface flex flex-col gap-2 rounded-2xl p-2 transition-shadow focus-within:glow sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Link2 className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-primary" />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a live link: Twitch, Kick or YouTube Live"
            aria-label="Live stream link"
            spellCheck={false}
            autoComplete="off"
            className="h-12 w-full rounded-xl bg-transparent pl-12 pr-3 text-base outline-none placeholder:text-muted-foreground/70"
          />
        </div>
        <Select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Content type" className="h-11 sm:w-48">
          {profiles.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </Select>
        <Button type="submit" size="lg" disabled={busy || !url.trim()} className="h-11">
          {busy ? <Loader2 className="animate-spin" /> : <Radio />} Watch live
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 text-xs text-muted-foreground">
        <span>Clipforge records the stream, catches big moments and cuts them for you.</span>
        <span className="flex items-center gap-1.5"><StatusDot tone="gray" /> Offline channels are watched until they go live</span>
      </div>
      {error && <p role="alert" className="flex items-center gap-2 px-2 text-sm font-medium text-destructive"><AlertCircle className="size-4" /> {error}</p>}
    </form>
  );
}

function Preview({ ch, st, reconnecting }: { ch: LiveChannel; st: (typeof STATE)[LiveState]; reconnecting: boolean }) {
  const [failed, setFailed] = useState(false);
  const thumb = ch.state === "recording" || ch.state === "waiting" ? streamThumb(ch.url) : null;
  const platform = platformOf(ch.url);
  const tone = reconnecting ? "yellow" : st.tone;
  return (
    <div className={cn("relative aspect-video overflow-hidden bg-gradient-to-br", PLATFORM_TINT[platform] ?? "from-primary/30 to-zinc-900")}>
      {thumb && !failed ? (
        <img src={thumb} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className="size-full object-cover" />
      ) : (
        <div className="grid size-full place-items-center"><Radio className="size-9 text-white/30" /></div>
      )}
      <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/30" />
      <span className="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-xs font-semibold uppercase tracking-wide backdrop-blur">
        <StatusDot tone={tone} pulse={st.pulse || reconnecting} /> {reconnecting ? "Reconnecting" : st.label}
      </span>
      <span className="absolute right-3 top-3 rounded-md bg-black/60 px-2 py-1 text-xs font-medium backdrop-blur">{platform}</span>
      <span className="absolute bottom-3 left-3 text-xs font-medium tabular-nums text-white/90">{fmtTime(ch.recording_sec)} recorded</span>
    </div>
  );
}

function HypeBar({ score, active }: { score: number; active: boolean }) {
  const pct = Math.min(100, Math.max(2, score * 100));
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="font-medium text-muted-foreground">Hype right now</span>
        <span className="font-semibold tabular-nums">{Math.round(score * 100)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/[.07]" role="meter" aria-label="Hype right now" aria-valuemin={0} aria-valuemax={1} aria-valuenow={score}>
        <div className={cn("h-full rounded-full transition-[width] duration-700", active ? "bg-hype" : "bg-zinc-600")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Moment({ c }: { c: LiveClip }) {
  const signals = Object.entries(c.signals ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 2);
  return (
    <li className="flex items-center gap-2 rounded-lg bg-white/[.03] px-2.5 py-1.5 text-xs">
      <span className="font-semibold tabular-nums">{c.clock}</span>
      {c.manual ? (
        <span className="inline-flex items-center gap-1 text-muted-foreground"><Scissors className="size-3" /> by you</span>
      ) : (
        <span className="rounded bg-amber-400/15 px-1.5 py-0.5 font-semibold text-amber-300">{c.score.toFixed(2)}</span>
      )}
      <span className="min-w-0 flex-1 truncate text-muted-foreground">{signals.map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}</span>
      {c.error ? <span className="text-destructive">{c.error}</span> : c.job ? (
        <Link to={`/p/${c.job}`} className="inline-flex items-center gap-1 font-semibold text-primary hover:underline"><Clapperboard className="size-3.5" /> Open</Link>
      ) : <span className="text-muted-foreground">Cut</span>}
    </li>
  );
}

function Channel({ ch, onChange }: { ch: LiveChannel; onChange: () => void }) {
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [all, setAll] = useState(false);
  const st = STATE[ch.state] ?? STATE.idle;
  const recording = ch.state === "recording";
  const running = ch.alive && ch.state !== "ended" && ch.state !== "stopped" && ch.state !== "error";
  const pct = ch.buffer_limit_mb ? Math.min(1, ch.buffer_mb / ch.buffer_limit_mb) : 0;
  const moments = [...ch.clips].reverse();

  const act = async (what: string, path: string, json?: unknown, method?: string) => {
    setBusy(what);
    setMsg(null);
    try {
      await api(path, { method, json });
      if (what.startsWith("clip")) setMsg({ ok: true, text: "Cutting it now. It shows up under captured moments, then gets ranked and titled." });
      onChange();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "That didn't work." });
    } finally {
      setBusy("");
    }
  };
  const base = `/api/live/channels/${encodeURIComponent(ch.name)}`;

  return (
    <article className={cn("surface min-w-0 overflow-hidden rounded-2xl transition-shadow", recording && !ch.reconnecting && "shadow-[0_0_40px_-18px_theme(colors.red.500)]")}>
      <Preview ch={ch} st={st} reconnecting={ch.reconnecting} />
      <div className="space-y-4 p-4">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-semibold">{ch.name}</h3>
            <a href={ch.url} target="_blank" rel="noreferrer" className="block truncate text-xs text-muted-foreground hover:text-foreground hover:underline">{ch.url}</a>
          </div>
          {running ? (
            <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => act("stop", `${base}/stop`, undefined, "POST")}>
              {busy === "stop" ? <Loader2 className="animate-spin" /> : <Square />} Stop
            </Button>
          ) : (
            <Button size="sm" disabled={!!busy} onClick={() => act("start", `${base}/start`, undefined, "POST")}>
              {busy === "start" ? <Loader2 className="animate-spin" /> : <Play />} Start
            </Button>
          )}
          <Button size="sm" variant="ghost" className="px-2 text-muted-foreground hover:text-destructive" disabled={!!busy || running} aria-label={`Remove ${ch.name}`} onClick={() => act("remove", base, undefined, "DELETE")}>
            <Trash2 />
          </Button>
        </div>

        {ch.note && (
          <p className={cn("flex items-start gap-2 text-xs", ch.reconnecting || ch.state === "error" ? "text-amber-300" : "text-muted-foreground")}>
            {ch.reconnecting && <WifiOff className="mt-0.5 size-3.5 shrink-0" />}
            {ch.note}
          </p>
        )}

        <HypeBar score={ch.score} active={recording} />

        <dl className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg bg-white/[.03] px-2 py-2"><dt className="text-[11px] text-muted-foreground">Moments</dt><dd className="text-lg font-semibold tabular-nums">{ch.spikes}</dd></div>
          <div className="rounded-lg bg-white/[.03] px-2 py-2"><dt className="text-[11px] text-muted-foreground">Buffer</dt><dd className="text-lg font-semibold tabular-nums">{ch.buffer_mb.toFixed(0)}<span className="text-xs font-normal text-muted-foreground"> MB</span></dd></div>
          <div className="rounded-lg bg-white/[.03] px-2 py-2">
            <dt className="text-[11px] text-muted-foreground">Chat</dt>
            <dd className="flex h-7 items-center justify-center gap-1 text-sm font-semibold">
              {ch.chat ? (ch.chat.connected ? <><Wifi className="size-4 text-emerald-400" /> {ch.chat.messages}</> : <WifiOff className="size-4 text-muted-foreground" />) : <span className="text-muted-foreground">off</span>}
            </dd>
          </div>
        </dl>

        <div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/[.07]" role="meter" aria-label="Buffer size" aria-valuemin={0} aria-valuemax={ch.buffer_limit_mb} aria-valuenow={ch.buffer_mb}>
            <div className={cn("h-full rounded-full", pct > 0.9 ? "bg-amber-400" : "bg-primary/70")} style={{ width: `${pct * 100}%` }} />
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Last {ch.retention_min} min kept on disk, {ch.buffer_mb.toFixed(0)} of {ch.buffer_limit_mb} MB
            {ch.reconnects > 0 ? ` · reconnected ${ch.reconnects}×` : ""}
          </p>
        </div>

        {/* quick-action toolbar */}
        <div role="group" aria-label={`Clip the last seconds of ${ch.name}`} className="flex items-center rounded-xl border bg-white/[.03] p-1">
          <span className="flex items-center gap-1.5 px-2.5 text-xs font-semibold text-muted-foreground"><Scissors className="size-3.5" /> Clip last</span>
          {CLIP_LENGTHS.map((s) => (
            <button
              key={s}
              type="button"
              disabled={!recording || !!busy}
              onClick={() => act(`clip${s}`, `${base}/clip`, { seconds: s })}
              className="flex h-8 flex-1 items-center justify-center rounded-lg text-sm font-semibold transition-colors hover:bg-primary hover:text-primary-foreground disabled:pointer-events-none disabled:opacity-40"
            >
              {busy === `clip${s}` ? <Loader2 className="size-4 animate-spin" /> : `${s}s`}
            </button>
          ))}
        </div>
        {!recording && <p className="-mt-2 text-[11px] text-muted-foreground">Quick clips work while the stream is being recorded.</p>}
        {msg && (
          <p role="status" className={cn("flex items-start gap-2 text-xs font-medium", msg.ok ? "text-emerald-400" : "text-destructive")}>
            {msg.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <AlertCircle className="mt-0.5 size-3.5 shrink-0" />} {msg.text}
          </p>
        )}

        {moments.length > 0 && (
          <div>
            <button type="button" onClick={() => setAll((a) => !a)} aria-expanded={all} className="mb-1.5 flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground">
              Captured moments · {moments.length}
              <ChevronDown className={cn("size-4 transition-transform", all && "rotate-180")} />
            </button>
            <ul className="space-y-1">{(all ? moments : moments.slice(0, 2)).map((c, i) => <Moment key={`${c.t}-${i}`} c={c} />)}</ul>
          </div>
        )}
      </div>
    </article>
  );
}

function Feed({ events }: { events: LiveEvent[] }) {
  return (
    <section className="surface rounded-2xl p-4 xl:sticky xl:top-20">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Bell className="size-4 text-primary" /> Activity</h2>
      {events.length ? (
        <ul className="space-y-3">
          {[...events].reverse().slice(0, 14).map((e) => (
            <li key={e.id} className="flex gap-2.5 text-sm">
              <StatusDot tone={e.kind === "error" ? "red" : "blue"} className="mt-1.5" />
              <div className="min-w-0">
                <p className={cn("truncate font-medium", e.kind === "error" && "text-destructive")}>{e.title ?? e.kind}</p>
                <p className="truncate text-xs text-muted-foreground">{e.channel} · {ago(e.ts)}{e.text ? ` · ${e.text}` : ""}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">Moments and reconnects show up here as they happen.</p>
      )}
    </section>
  );
}

export default function Live() {
  const { data, error, loading, refresh } = useLive();
  const unavailable = error?.status === 404;
  const channels = data?.channels ?? [];
  const recording = channels.filter((c) => c.state === "recording").length;
  const moments = channels.reduce((n, c) => n + c.spikes, 0);

  return (
    <AppShell crumbs={[{ label: "Live Streams", to: "/live" }, ...(channels.length === 1 ? [{ label: platformOf(channels[0].url) }] : [])]}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Live streams</h1>
            <p className="mt-1 text-muted-foreground">Your command center for live clipping.</p>
          </div>
          {channels.length > 0 && (
            <div className="flex gap-2 text-sm">
              <span className="surface inline-flex items-center gap-2 rounded-lg px-3 py-1.5"><StatusDot tone={recording ? "red" : "gray"} pulse={recording > 0} /> {recording} recording</span>
              <span className="surface inline-flex items-center gap-2 rounded-lg px-3 py-1.5"><Scissors className="size-3.5 text-primary" /> {moments} moments</span>
            </div>
          )}
        </div>

        {unavailable ? (
          <div className="surface rounded-2xl p-10 text-center">
            <p className="text-lg font-semibold">Live mode isn't available here</p>
            <p className="mt-1 text-sm text-muted-foreground">Recording needs the PC that runs Clipforge. Open Clipforge on that computer to use it.</p>
          </div>
        ) : (
          <>
            <CommandBar profiles={data?.profiles ?? [{ key: "gaming", label: "Gaming" }]} onAdded={refresh} />
            {error && !unavailable && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">{error.message}</p>}
            {loading && !data ? (
              <div className="grid place-items-center py-16"><Loader2 className="size-8 animate-spin text-primary" /></div>
            ) : channels.length ? (
              <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
                <div className="grid grid-cols-1 gap-5 md:grid-cols-2">{channels.map((c) => <Channel key={c.name} ch={c} onChange={refresh} />)}</div>
                <Feed events={data?.events ?? []} />
              </div>
            ) : (
              <div className="surface grid place-items-center gap-3 rounded-2xl p-14 text-center">
                <span className="grid size-14 place-items-center rounded-2xl bg-primary/15"><Radio className="size-7 text-primary" /></span>
                <p className="text-lg font-semibold">No streams yet</p>
                <p className="max-w-sm text-sm text-muted-foreground">Paste a live link above. If the channel is offline, Clipforge waits and starts when it goes live.</p>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
