import { AlertCircle, ArrowRight, Clapperboard, Layers, Loader2, Sparkles, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { SystemWidget } from "@/components/layout/SystemWidget";
import { ProjectCard } from "@/components/project/ProjectViews";
import { useBoot, useProjects } from "@/hooks/data";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

const LENGTHS = [
  { key: "auto", label: "Length: auto" },
  { key: "short", label: "15–30 s" },
  { key: "medium", label: "30–60 s" },
  { key: "long", label: "60–90 s" },
];

function Composer() {
  const { session } = useBoot();
  const nav = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [profile, setProfile] = useState("auto");
  const [count, setCount] = useState("10");
  const [length, setLength] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const urlBox = useRef<HTMLInputElement>(null);
  const loc = useLocation();
  useEffect(() => urlBox.current?.focus(), [(loc.state as { focus?: number } | null)?.focus]);
  const canCreate = session?.plan.can_create !== false;

  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ id?: string; live?: boolean; redirect?: string }>("/api/studio/projects", { json: { url, profile, count: Number(count), length } });
      if (r.live && r.redirect) window.location.href = r.redirect;
      else if (r.id) nav(`/p/${r.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    setError("");
    const form = new FormData();
    form.set("file", file);
    form.set("profile", profile);
    form.set("count", count);
    form.set("length", length);
    try {
      const res = await fetch("/api/clips/upload", {
        method: "POST",
        body: form,
        credentials: "same-origin",
        headers: { "X-CSRF-Token": document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(data.error ?? "Upload failed.", res.status);
      nav(`/p/${data.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed.");
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={start}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files?.[0];
        if (f) void upload(f);
      }}
      className={cn("surface rounded-2xl p-2 transition-shadow focus-within:glow", drag && "glow")}
    >
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Layers className="pointer-events-none absolute left-5 top-1/2 size-5 -translate-y-1/2 text-primary" />
          <Input
            ref={urlBox}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a link… YouTube, Twitch, Kick"
            aria-label="Video link"
            spellCheck={false}
            className="h-14 border-0 bg-transparent pl-14 text-base shadow-none hover:bg-transparent focus-visible:ring-0 focus-visible:ring-offset-0"
          />
        </div>
        <div className="flex gap-2">
          <input ref={fileInput} type="file" hidden accept="video/*,audio/*" onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
          <Button type="button" variant="secondary" size="lg" onClick={() => fileInput.current?.click()} disabled={busy || !canCreate}>
            <Upload /> Upload
          </Button>
          <Button type="submit" size="lg" disabled={busy || !url.trim() || !canCreate}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Find clips
          </Button>
        </div>
      </div>
      <div className="mt-1 grid grid-cols-1 gap-2 border-t px-1 pt-3 pb-1 sm:grid-cols-3">
        <Select value={profile} onChange={(e) => setProfile(e.target.value)} aria-label="Content type">
          <option value="auto">Content: auto-detect</option>
          {session?.profiles
            .filter((p) => !p.hidden)
            .map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
        </Select>
        <Select value={count} onChange={(e) => setCount(e.target.value)} aria-label="How many clips">
          {[5, 10, 15, 20].map((n) => (
            <option key={n} value={n}>
              Up to {n} clips
            </option>
          ))}
        </Select>
        <Select value={length} onChange={(e) => setLength(e.target.value)} aria-label="Clip length">
          {LENGTHS.map((l) => (
            <option key={l.key} value={l.key}>
              {l.label}
            </option>
          ))}
        </Select>
      </div>
      {!canCreate && <p className="px-3 pt-3 text-sm text-destructive">{session?.plan.reason || "You've used your clips for now."}</p>}
      {error && (
        <p role="alert" className="flex items-center gap-2 px-3 pt-3 text-sm font-medium text-destructive">
          <AlertCircle className="size-4" /> {error}
        </p>
      )}
    </form>
  );
}

export default function Dashboard() {
  const { session, error: bootError } = useBoot();
  const { data: projects, loading } = useProjects();
  const first = session?.user.name?.split(" ")[0];
  const recent = (projects ?? []).slice(0, 6);
  const ready = (projects ?? []).filter((p) => p.state === "ready");
  const clips = ready.reduce((n, p) => n + p.clips, 0);

  return (
    <AppShell>
      <section className="mx-auto max-w-3xl pb-10 pt-4 text-center">
        <p className="mb-4 inline-flex items-center gap-2 rounded-full border bg-white/[.03] px-3 py-1 text-xs font-medium text-muted-foreground">
          <Sparkles className="size-3.5 text-primary" /> Podcasts, gaming, IRL, webinars. Anything with a story.
        </p>
        <h1 className="text-gradient mb-8 text-4xl font-bold tracking-tight sm:text-5xl">{first ? `Hey ${first}, what are we clipping today?` : "What are we clipping today?"}</h1>
        <div className="text-left">
          <Composer />
        </div>
        <p className="mt-4 text-xs text-muted-foreground">Only clip videos you own or have permission to use, and follow each platform's rules when you post.</p>
      </section>

      {bootError && !bootError.needsLogin && <p role="alert" className="mb-6 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">{bootError.message}</p>}

      <div className="grid grid-cols-1 items-start gap-8 xl:grid-cols-[minmax(0,1fr)_300px]">
        <section>
          <div className="mb-4 flex items-end justify-between">
            <h2 className="text-lg font-semibold">Recent projects</h2>
            <Link to="/projects" className="inline-flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">View all <ArrowRight className="size-3.5" /></Link>
          </div>
          {loading && !projects ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <div key={i} className="h-60 animate-pulse rounded-xl bg-white/[.04]" />)}</div>
          ) : recent.length ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{recent.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
          ) : (
            <div className="surface grid place-items-center gap-3 rounded-2xl p-12 text-center">
              <span className="grid size-12 place-items-center rounded-2xl bg-primary/15"><Clapperboard className="size-6 text-primary" /></span>
              <p className="text-lg font-semibold">No projects yet</p>
              <p className="max-w-sm text-sm text-muted-foreground">Paste a link above. Clipforge finds the best moments, labels the story beats and gets them ready to export.</p>
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <SystemWidget />
          <div className="surface grid grid-cols-2 divide-x divide-white/10 rounded-2xl text-center">
            <div className="p-4"><p className="text-2xl font-bold tabular-nums">{ready.length}</p><p className="text-xs text-muted-foreground">Projects done</p></div>
            <div className="p-4"><p className="text-2xl font-bold tabular-nums">{clips}</p><p className="text-xs text-muted-foreground">Clips found</p></div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
