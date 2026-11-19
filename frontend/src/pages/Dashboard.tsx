import { AlertCircle, ArrowRight, AudioLines, CheckCircle2, Clapperboard, FileVideo, Layers, Loader2, Sparkles, Trophy, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { isActive, useBoot, useProjects } from "@/hooks/data";
import { api, ApiError, thumbUrl } from "@/lib/api";
import type { Project } from "@/lib/types";
import { ago, cn, fmtTime } from "@/lib/utils";

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
      className={cn("rounded-[1.75rem] border bg-card p-3 shadow-lg transition-colors", drag && "border-primary ring-2 ring-primary/40")}
    >
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Layers className="pointer-events-none absolute left-5 top-1/2 size-5 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a link… YouTube, Twitch, Kick"
            aria-label="Video link"
            spellCheck={false}
            className="h-14 border-0 bg-transparent pl-14 text-base shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
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
      <div className="mt-2 grid grid-cols-1 gap-2 border-t px-1 pt-3 sm:grid-cols-3">
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

function StateBadge({ p }: { p: Project }) {
  if (p.state === "ready") return <Badge variant="success"><CheckCircle2 className="size-3.5" /> Ready</Badge>;
  if (p.state === "failed") return <Badge variant="destructive"><AlertCircle className="size-3.5" /> Failed</Badge>;
  if (p.state === "cancelled") return <Badge variant="secondary">Cancelled</Badge>;
  return <Badge><Loader2 className="size-3.5 animate-spin" /> {p.state === "queued" ? "Queued" : "Working"}</Badge>;
}

function ProjectCard({ p }: { p: Project }) {
  const thumb = thumbUrl(p.thumb, p.extractor);
  const active = isActive(p);
  return (
    <Link to={`/p/${p.id}`} className="group block focus-visible:outline-none">
      <Card className="overflow-hidden transition-all group-hover:-translate-y-0.5 group-hover:border-primary/50 group-hover:shadow-lg group-focus-visible:ring-2 group-focus-visible:ring-ring">
        <div className="relative aspect-video bg-gradient-to-br from-primary/25 via-secondary to-card">
          {thumb ? (
            <img src={thumb} alt="" loading="lazy" className="size-full object-cover" />
          ) : (
            <div className="grid size-full place-items-center text-muted-foreground">{p.extractor ? <Clapperboard className="size-10" /> : <FileVideo className="size-10" />}</div>
          )}
          {p.duration ? <span className="absolute bottom-2 right-2 rounded-md bg-black/70 px-2 py-0.5 text-sm font-semibold tabular text-white">{fmtTime(p.duration)}</span> : null}
          <span className="absolute left-2 top-2"><StateBadge p={p} /></span>
        </div>
        <div className="space-y-3 p-4">
          <h3 className="line-clamp-2 min-h-[2.75rem] text-base font-semibold leading-snug">{p.title || "Untitled"}</h3>
          {active ? (
            <div className="space-y-2">
              <Progress value={p.progress} indeterminate={p.progress < 0.02} />
              <p className="truncate text-sm text-muted-foreground">{p.stage || "Starting"}</p>
            </div>
          ) : p.state === "failed" ? (
            <p className="line-clamp-2 text-sm text-destructive">{p.error || "Something went wrong."}</p>
          ) : (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1.5"><Clapperboard className="size-4" /> {p.clips} clips</span>
              <span className="inline-flex items-center gap-1.5"><AudioLines className="size-4" /> {p.beats} beats</span>
              {p.best != null && <span className="inline-flex items-center gap-1.5 text-beat-payoff"><Trophy className="size-4" /> {Math.round(p.best)}</span>}
            </div>
          )}
          <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
            {p.profile?.label ? <Badge variant="outline" className="max-w-[70%] truncate">{p.profile.label}</Badge> : <span>{active ? "Detecting content type…" : ""}</span>}
            <span>{ago(p.created)}</span>
          </div>
        </div>
      </Card>
    </Link>
  );
}

export default function Dashboard() {
  const { session, error: bootError } = useBoot();
  const { data: projects, loading } = useProjects();
  const first = session?.user.name?.split(" ")[0];

  return (
    <AppShell>
      <section className="mx-auto max-w-3xl pb-10 pt-6 text-center">
        <p className="mb-3 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-semibold text-muted-foreground">
          <Sparkles className="size-4 text-primary" /> Podcasts, gaming, IRL, webinars. Anything with a story.
        </p>
        <h1 className="mb-8 text-4xl font-extrabold tracking-tight sm:text-5xl">{first ? `Hey ${first}, what are we clipping today?` : "What are we clipping today?"}</h1>
        <div className="text-left">
          <Composer />
        </div>
        <p className="mt-4 text-sm text-muted-foreground">Only clip videos you own or have permission to use, and follow each platform's rules when you post.</p>
      </section>

      {bootError && !bootError.needsLogin && <p role="alert" className="mb-6 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">{bootError.message}</p>}

      <section>
        <div className="mb-4 flex items-end justify-between">
          <h2 className="text-xl font-bold">Recent projects</h2>
          {projects && projects.length > 0 && <span className="text-sm text-muted-foreground">{projects.length} shown</span>}
        </div>
        {loading && !projects ? (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {[0, 1, 2, 3].map((i) => <div key={i} className="h-72 animate-pulse rounded-lg bg-secondary" />)}
          </div>
        ) : projects && projects.length ? (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{projects.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
        ) : (
          <Card className="grid place-items-center gap-3 p-12 text-center">
            <Clapperboard className="size-10 text-muted-foreground" />
            <p className="text-lg font-semibold">No projects yet</p>
            <p className="max-w-sm text-sm text-muted-foreground">Paste a link above. Clipforge finds the best moments, labels the story beats and gets them ready to export.</p>
            <ArrowRight className="size-5 -rotate-90 text-primary" />
          </Card>
        )}
      </section>
    </AppShell>
  );
}
