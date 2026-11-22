import { AudioLines, Captions, Clapperboard, Cpu, FileText, Info, Layers, Loader2, Palette, Rows3, Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { BeatsPanel, ScoreDot } from "@/components/project/BeatsPanel";
import { ClipsPanel } from "@/components/project/ClipsPanel";
import { FailurePanel, PipelineStepper } from "@/components/project/PipelineStepper";
import { BrandTab, CaptionsTab, SourceTimeline, TranscriptTab } from "@/components/project/StudioPanels";
import { ClipCopy, ViralityBars } from "@/components/project/ViralityBars";
import { VideoStage } from "@/components/project/VideoStage";
import { Block, Timeline, Track } from "@/components/Timeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useJob } from "@/hooks/data";
import { pickMedia, usePlayer, usePlayerKeys } from "@/hooks/usePlayer";
import { ApiError } from "@/lib/api";
import { BEAT_META, beatAt } from "@/lib/beats";
import type { Beat, Job } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";

function Working({ job }: { job: Job }) {
  const detecting = /kind of video|detect|reading the link/i.test(job.stage || "");
  return (
    <Card className="mx-auto mt-10 max-w-xl space-y-4 p-8 text-center">
      <Loader2 className="mx-auto size-10 animate-spin text-primary" />
      <h2 className="text-xl font-bold">{detecting ? "Detecting content type…" : job.state === "queued" ? "Waiting for a free slot…" : "Finding the best moments…"}</h2>
      <PipelineStepper stages={job.stages} />
      <p className="text-sm text-muted-foreground">{job.stage || "Starting"}</p>
      <p className="text-sm text-muted-foreground">Only the audio is analysed first, so long videos take minutes, not hours.</p>
    </Card>
  );
}

function Failed({ job, onRetry }: { job: Job; onRetry: () => void }) {
  return (
    <Card className="mx-auto mt-10 max-w-2xl space-y-5 p-8">
      <PipelineStepper stages={job.stages} hideBar />
      <FailurePanel jobId={job.id} error={job.error} stages={job.stages} onRetry={onRetry} />
    </Card>
  );
}

export default function Project() {
  const { id } = useParams();
  const { job, error, gone, refresh } = useJob(id);
  const [sp, setSp] = useSearchParams();
  const [zoom, setZoom] = useState(1);

  const clips = job?.result?.clips ?? [];
  const clipId = Number(sp.get("clip")) || clips[0]?.id || 0;
  const clip = clips.find((c) => c.id === clipId) ?? clips[0] ?? null;
  const media = useMemo(() => (job && clip ? pickMedia(job, clip) : null), [job, clip]);
  const player = usePlayer(media, clip);
  usePlayerKeys(player, !!clip);

  const absTime = clip ? clip.start + player.time : 0;
  const active: Beat | null = clip ? beatAt(clip.beats, absTime) : null;
  const seekBeat = (b: Beat) => clip && player.seek(b.start - clip.start);

  if (gone || (error && error.status === 404)) {
    return (
      <AppShell crumbs={[{ label: "Projects", to: "/projects" }, { label: "Not found" }]}>
        <Card className="mx-auto mt-10 max-w-md p-8 text-center">
          <p className="text-lg font-bold">Project not found</p>
          <Button asChild className="mt-4"><Link to="/projects">Back to projects</Link></Button>
        </Card>
      </AppShell>
    );
  }
  if (!job) {
    return <AppShell crumbs={[{ label: "Projects", to: "/projects" }, { label: "Loading…" }]}><div className="mx-auto mt-24 grid place-items-center"><Loader2 className="size-8 animate-spin text-primary" /></div></AppShell>;
  }

  const res = job.result;
  const notes = (job.summary?.notes ?? res?.summary?.notes ?? []).filter((n) => /AI|LLM|local/i.test(n));

  return (
    <AppShell wide crumbs={[{ label: "Projects", to: "/projects" }, { label: job.title || "Project" }]}>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 truncate text-xl font-bold tracking-tight">{job.title || "Project"}</h1>
        {res?.profile && (
          <Badge variant="outline" title={res.profile.reason}>
            <Layers className="size-3.5" /> {res.profile.label}
          </Badge>
        )}
        {res && (
          <Badge variant={res.scorer.startsWith("llm") ? "default" : "warning"} title={res.scorer.startsWith("llm") ? "Scored by the AI" : "The AI wasn't used for this run"}>
            <Cpu className="size-3.5" /> {res.scorer.startsWith("llm") ? `AI: ${res.scorer.split(":")[1]}` : "Rule-based"}
          </Badge>
        )}
      </div>

      {(job.state === "queued" || (job.state === "running" && !res)) && <Working job={job} />}
      {job.state === "failed" && <Failed job={job} onRetry={refresh} />}

      {res && clip && (
        <>
          {notes.length > 0 && (
            <p className="mb-4 flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-sm">
              <Info className="mt-0.5 size-4 shrink-0 text-amber-300" /> {notes.join(" ")}
            </p>
          )}
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]">
            {/* left: the player */}
            <div className="min-w-0 space-y-4">
              <div className="flex items-start gap-3">
                <ScoreDot score={clip.score} className="mt-0.5 h-10 min-w-10 text-base" />
                <div className="min-w-0 flex-1">
                  <h2 className="text-lg font-semibold leading-snug">{clip.title || `Moment at ${clip.time}`}</h2>
                  <p className="text-sm text-muted-foreground">{clip.reason}</p>
                </div>
                <Button asChild>
                  <Link to={`/p/${job.id}/clip/${clip.id}`}><Wand2 /> Open Director</Link>
                </Button>
              </div>

              <div className="mx-auto max-w-4xl rounded-xl border border-white/10 p-2 shadow-[0_0_60px_-24px_hsl(var(--primary))]">
                <VideoStage job={job} clip={clip} media={media} player={player} beat={active} onChanged={refresh} />
              </div>

              {clip.virality && (
                <div className="surface grid gap-4 rounded-xl p-4 sm:grid-cols-[auto_1fr] sm:items-center">
                  <div className="text-center">
                    <p className="text-4xl font-bold tabular-nums">{Math.round(clip.virality.overall_virality)}</p>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Virality</p>
                  </div>
                  <ViralityBars v={clip.virality} />
                </div>
              )}
              <ClipCopy clip={clip} />
            </div>

            {/* right: the editor panel */}
            <aside className="min-w-0">
              <Tabs defaultValue="clips" className="surface flex flex-col rounded-xl p-3 xl:max-h-[calc(100vh-7.5rem)]">
                <TabsList className="w-full justify-start overflow-x-auto">
                  <TabsTrigger value="clips"><Clapperboard className="size-3.5" /> Clips · {clips.length}</TabsTrigger>
                  <TabsTrigger value="transcript"><FileText className="size-3.5" /> Transcript</TabsTrigger>
                  <TabsTrigger value="captions"><Captions className="size-3.5" /> Captions</TabsTrigger>
                  <TabsTrigger value="brand"><Palette className="size-3.5" /> Brand Kit</TabsTrigger>
                  <TabsTrigger value="beats"><AudioLines className="size-3.5" /> Beats</TabsTrigger>
                </TabsList>
                <div className="mt-1 min-h-0 flex-1 overflow-y-auto pr-1">
                  <TabsContent value="clips"><ClipsPanel clips={clips} selected={clip.id} onSelect={(cid) => setSp({ clip: String(cid) }, { replace: true })} /></TabsContent>
                  <TabsContent value="transcript"><TranscriptTab clip={clip} /></TabsContent>
                  <TabsContent value="captions"><CaptionsTab jobId={job.id} clipId={clip.id} /></TabsContent>
                  <TabsContent value="brand"><BrandTab jobId={job.id} /></TabsContent>
                  <TabsContent value="beats"><BeatsPanel clip={clip} active={active} onSeek={seekBeat} /></TabsContent>
                </div>
              </Tabs>
            </aside>
          </div>

          {/* bottom: the timelines */}
          <div className="mt-5 space-y-3">
            <SourceTimeline duration={res.source.duration} clips={clips} selected={clip.id} onSelect={(cid) => setSp({ clip: String(cid) }, { replace: true })} playhead={absTime} />
            <div className="surface space-y-2 rounded-xl p-3">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"><Rows3 className="size-4" /> Clip timeline</h3>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  Zoom
                  <input type="range" min={1} max={8} step={0.5} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Timeline zoom" className="w-28 accent-[hsl(var(--primary))]" />
                </label>
              </div>
              <Timeline duration={clip.duration} time={player.time} playing={player.playing} onSeek={player.seek} zoom={zoom}>
                <Track label="Beats" height={56}>
                  {clip.beats.map((b, i) => (
                    <Block
                      key={i}
                      start={b.start - clip.start}
                      end={b.end - clip.start}
                      onClick={() => seekBeat(b)}
                      title={`${BEAT_META[b.type].label}: ${b.reason}`}
                      className={cn("text-white", BEAT_META[b.type].bar, active === b && "ring-2 ring-white")}
                    >
                      <span className="block truncate px-2 pt-1.5 text-sm font-bold">{BEAT_META[b.type].label}</span>
                    </Block>
                  ))}
                  {!clip.beats.length && <span className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">No beats for this clip</span>}
                </Track>
                <Track label="Impact" height={44}>
                  {clip.beats.map((b, i) => (
                    <Block key={i} start={b.start - clip.start} end={b.end - clip.start} inset={0} className="rounded-none bg-transparent">
                      <div className={cn("absolute inset-x-0 bottom-0 rounded-t-md opacity-80", BEAT_META[b.type].bar)} style={{ height: `${Math.max(8, b.score)}%` }} />
                    </Block>
                  ))}
                </Track>
              </Timeline>
              <p className="text-xs text-muted-foreground">
                {fmtTime(clip.start)} – {fmtTime(clip.end)} of the video · {Math.round(clip.duration)} s · click a track or a beat to jump
              </p>
            </div>
          </div>
        </>
      )}

      {res && !clip && <Card className="mx-auto mt-10 max-w-md p-8 text-center"><p className="font-bold">No clips found</p><p className="mt-1 text-sm text-muted-foreground">Try a longer video or another content type.</p></Card>}

      {/* errors from fetching are inline, not fatal */}
      {error && error.status !== 404 && <p role="alert" className="mt-4 text-sm text-destructive">{error instanceof ApiError ? error.message : "Couldn't reach Clipforge."}</p>}
    </AppShell>
  );
}
