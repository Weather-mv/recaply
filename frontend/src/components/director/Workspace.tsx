import { ArrowLeft, AudioLines, Film, Loader2, ShieldAlert, Type } from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CamBoxEditor } from "@/components/director/CamBoxEditor";
import { CaptionsPanel } from "@/components/director/CaptionsPanel";
import { ExportPanel } from "@/components/director/ExportPanel";
import { TrimFields } from "@/components/director/TrimFields";
import { ReframeCanvas } from "@/components/director/ReframeCanvas";
import { TranscriptPanel, type EditMode } from "@/components/director/TranscriptPanel";
import { useWaveform, Waveform } from "@/components/director/Waveform";
import { ScoreDot } from "@/components/project/BeatsPanel";
import { Block, Timeline, Track, useTimeline } from "@/components/Timeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tip } from "@/components/ui/tooltip";
import { useBoot, useJob, waitForJob } from "@/hooks/data";
import { pickMedia, usePlayer, usePlayerKeys } from "@/hooks/usePlayer";
import { api, ApiError } from "@/lib/api";
import { BEAT_META } from "@/lib/beats";
import { bandY, loadCaptionFont, resolveStyle } from "@/lib/captions";
import { CAM_LAYOUTS, resolveLayout, type Layout, type LayoutInfo } from "@/lib/reframe";
import type { Clip, Job, ShortOptions, Word, WordsReply } from "@/lib/types";
import { cn, fmtPrecise } from "@/lib/utils";
import { activeWordIndex, cutRanges } from "@/lib/words";

type WordsState = { status: "loading" | "ready" | "error"; words: Word[]; fillers: number[]; message?: string };

/** A word on the text track. The label only shows when the chip is wide enough to read; zoom in for the rest. */
function WordChip({ w, cut, active, onClick }: { w: Word; cut: boolean; active: boolean; onClick: () => void }) {
  const { pps } = useTimeline();
  const end = Math.max(w.e, w.s + 0.15);
  return (
    <Block
      start={w.s}
      end={end}
      inset={8}
      onClick={onClick}
      title={w.w}
      className={cn(
        "rounded-sm px-1.5 text-sm font-semibold leading-8",
        cut ? "bg-destructive/20 text-destructive line-through" : active ? "bg-primary text-primary-foreground" : "bg-accent text-accent-foreground hover:bg-primary/30",
      )}
    >
      {(end - w.s) * pps >= 34 && <span className="block truncate">{w.w}</span>}
    </Block>
  );
}

const DEFAULTS: ShortOptions = { export: "shorts", layout: "auto", split_ratio: 0.42, cam_box: null, captions: "bold", hook: true, context: true, context_text: "", emoji: false, tighten: false, broll: false, translate: "", teaser: true, bgm: "", bgm_level: 0.32 };

/** What frames the workspace: the Director page's shell and crumbs, or the Editor's bin. */
export type WorkspaceFrame = (content: ReactNode, ctx: { job: Job | null; clip: Clip | null }) => ReactNode;

interface WorkspaceProps {
  id: string | undefined;
  clipId: number;
  frame?: WorkspaceFrame;
  /** where the back button goes (default: the clip's project) */
  back?: { to: string; label: string } | null;
  /** extra controls at the right of the title row */
  actions?: ReactNode;
}

/** Every Director tool for one clip: transcript, 9:16 preview, layout, captions, export and the timeline. */
export function DirectorWorkspace({ id, clipId, frame = (c) => c, back, actions }: WorkspaceProps) {
  const { job, error, gone, refresh } = useJob(id);
  const { caps, session } = useBoot();

  const clip = job?.result?.clips.find((c) => c.id === clipId) ?? null;
  const media = useMemo(() => (job && clip ? pickMedia(job, clip) : null), [job, clip]);
  const player = usePlayer(media, clip);
  usePlayerKeys(player, !!clip);

  // ---- transcript
  const [ws, setWs] = useState<WordsState>({ status: "loading", words: [], fillers: [] });
  const loadedFor = useRef("");
  const ready = !!job && job.state !== "queued" && job.state !== "running";
  useEffect(() => {
    const key = `${id}:${clipId}`;
    if (!id || !clip || !ready || loadedFor.current === key) return;
    loadedFor.current = key;
    setWs({ status: "loading", words: [], fillers: [] });
    api<WordsReply>(`/api/clipjob/${id}/clip/${clipId}/words`)
      .then((r) => setWs({ status: "ready", words: r.words, fillers: r.fillers }))
      .catch((e) => {
        loadedFor.current = ""; // allow a retry when the job changes
        setWs({ status: "error", words: [], fillers: [], message: e instanceof ApiError ? e.message : "Couldn't load the transcript." });
      });
  }, [id, clipId, clip, ready]);

  // ---- edits
  const [deleted, setDeleted] = useState<Set<number>>(new Set());
  const [mode, setMode] = useState<EditMode>("seek");
  const [options, setOptions] = useState<ShortOptions>(DEFAULTS);
  const [zoom, setZoom] = useState(3);
  const [safeZone, setSafeZone] = useState(false);
  const [trim, setTrim] = useState<{ start: number; end: number } | null>(null); // new cut points (seconds in the source video)
  const [busy, setBusy] = useState("");
  const [exportError, setExportError] = useState("");
  const [cutting, setCutting] = useState(false);

  const words = ws.words;
  const fillers = useMemo(() => new Set(ws.fillers), [ws.fillers]);
  const cuts = useMemo(() => cutRanges(words, deleted), [words, deleted]);
  const removed = cuts.reduce((a, [s, e]) => a + (e - s), 0);
  const activeIdx = activeWordIndex(words, player.time);
  const activeId = activeIdx >= 0 ? words[activeIdx].i : -1;

  // playback jumps over words that are cut
  useEffect(() => {
    if (!player.playing) return;
    const hit = cuts.find(([a, b]) => player.time >= a - 0.02 && player.time < b);
    if (hit) player.seek(hit[1]);
  }, [player.time, player.playing, cuts]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleWord = useCallback((w: Word) => {
    setDeleted((d) => {
      const n = new Set(d);
      if (!n.delete(w.i)) n.add(w.i);
      return n;
    });
  }, []);
  const onWord = (w: Word) => (mode === "cut" ? toggleWord(w) : player.seek(w.s)); // timeline chips: "Edit text" seeks too

  // ---- caption text typed in the studio (by word id; "" hides the word). Audio is untouched.
  const [edits, setEdits] = useState<Map<number, string>>(new Map());
  useEffect(() => setEdits(new Map()), [ws.words]); // a new transcript (another clip) starts clean
  const editWord = useCallback((w: Word, text: string) => {
    const t = text.split(/\s+/).filter(Boolean).join(" ").slice(0, 40);
    setEdits((m) => {
      const n = new Map(m);
      if (t === w.w) n.delete(w.i);
      else n.set(w.i, t);
      return n;
    });
  }, []);
  /** the words as the captions show them */
  const captionWords = useMemo(
    () => (edits.size ? words.flatMap((w) => (edits.has(w.i) ? (edits.get(w.i) ? [{ ...w, w: edits.get(w.i)! }] : []) : [w])) : words),
    [words, edits],
  );
  /** what the render needs: each edited word by where it starts in the source video */
  const withEdits = (o: ShortOptions): ShortOptions =>
    clip && edits.size ? { ...o, caption_edits: words.filter((w) => edits.has(w.i)).map((w) => ({ t: Math.round((clip.start + w.s) * 1000) / 1000, w: edits.get(w.i)! })) } : o;

  // ---- what's on screen (facecam, face path, action), measured once from the cut
  const [layoutInfo, setLayoutInfo] = useState<LayoutInfo | null>(null);
  const [layoutState, setLayoutState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [layoutError, setLayoutError] = useState("");
  const [drawingCam, setDrawingCam] = useState(false);
  const layoutFor = useRef("");
  const cutInfo = job?.cuts[String(clipId)];
  const cutFile = cutInfo?.state === "done" ? cutInfo.file : undefined;
  useEffect(() => {
    if (!id || !cutFile) return;
    const key = `${id}:${clipId}:${cutFile}`;
    if (layoutFor.current === key) return;
    layoutFor.current = key;
    setLayoutState("loading");
    api<LayoutInfo>(`/api/clipjob/${id}/clip/${clipId}/layout`)
      .then((r) => {
        setLayoutInfo(r);
        setLayoutState("ready");
      })
      .catch((e) => {
        layoutFor.current = "";
        setLayoutState("error");
        setLayoutError(e instanceof ApiError ? e.message : "Couldn't look at the picture");
      });
  }, [id, clipId, cutFile]);

  // ---- caption look: preset + the Captions panel's changes, drawn with the export's font
  const [panel, setPanel] = useState<"export" | "captions">("export");
  const capStyle = useMemo(() => {
    const preset = caps?.captions.find((c) => c.key === options.captions)?.style ?? caps?.captions.find((c) => c.key === "bold")?.style;
    return resolveStyle(preset, options.caption_style);
  }, [caps, options.captions, options.caption_style]);
  const [capFont, setCapFont] = useState("'Arial Black', Arial, sans-serif");
  const fontKey = options.caption_style?.font || "default";
  useEffect(() => {
    let live = true;
    loadCaptionFont(fontKey).then((f) => live && setCapFont(f));
    return () => {
      live = false;
    };
  }, [fontKey]);
  const autoY = bandY(resolveLayout(options.layout as Layout, layoutInfo, options.cam_box), options.split_ratio);

  const waveform = useWaveform(media, clip?.duration ?? 0);
  const getVideo = player.el;

  const startCut = async () => {
    if (!job || !clip) return;
    setCutting(true);
    try {
      await api(`/api/clipjob/${job.id}/cut`, { json: { ids: [clip.id], quality: "720" } });
      refresh();
    } catch (e) {
      setExportError(e instanceof ApiError ? e.message : "Couldn't start the cut.");
    } finally {
      setCutting(false);
    }
  };
  const trimmed = trim && clip && (Math.abs(trim.start - clip.start) > 0.04 || Math.abs(trim.end - clip.end) > 0.04) && trim.end - trim.start >= 2 ? trim : null;
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const startPreview = async () => {
    if (!job || !clip) return;
    setPreviewing(true);
    setPreviewError("");
    try {
      await api(`/api/clipjob/${job.id}/preview`, { json: { id: clip.id, options: withEdits(options), ...(trimmed ? { start: trimmed.start, end: trimmed.end } : {}) } });
      refresh();
    } catch (e) {
      setPreviewError(e instanceof ApiError ? e.message : "Couldn't start the preview.");
    } finally {
      setPreviewing(false);
    }
  };
  const cutState = job?.cuts[String(clipId)]?.state;
  const cuttingNow = cutting || cutState === "queued" || cutState === "cutting";

  const exportShort = async () => {
    if (!job || !clip) return;
    setExportError("");
    try {
      if (deleted.size) {
        setBusy("Applying your cuts…");
        await api(`/api/clipjob/${job.id}/clip/${clip.id}/edit`, { json: { deleted: [...deleted].sort((a, b) => a - b), broll: options.broll } });
        await waitForJob(job.id, (j) => {
          const e = j.cuts[`e${clip.id}`];
          if (e?.state === "failed") throw new ApiError(e.error || "Applying the cuts failed.", 500);
          return j.state === "ready" && e?.state === "done";
        });
      }
      setBusy("Starting the export…");
      await api(`/api/clipjob/${job.id}/shorts`, { json: { ids: [clip.id], options: withEdits(options), ...(trimmed && !deleted.size ? { trim: trimmed } : {}) } });
      refresh();
    } catch (e) {
      setExportError(e instanceof ApiError ? e.message : "The export didn't start.");
    } finally {
      setBusy("");
    }
  };

  // ---- not ready states
  const importing = !!job && (job.state === "queued" || job.state === "running") && job.task === "import";
  if (gone || (error && error.status === 404)) {
    return frame(<Card className="mx-auto mt-10 max-w-md p-8 text-center"><p className="text-lg font-bold">Not found</p><p className="mt-1 text-sm text-muted-foreground">This video or clip was deleted.</p></Card>, { job: null, clip: null });
  }
  if (job?.state === "failed" && !clip) {
    return frame(<Card className="mx-auto mt-10 max-w-md space-y-2 p-8 text-center"><ShieldAlert className="mx-auto size-7 text-destructive" /><p className="text-lg font-bold">Couldn't open this video</p><p className="text-sm text-muted-foreground">{job.error}</p></Card>, { job, clip: null });
  }
  if (!job || !caps || importing) return frame(<div className="mx-auto mt-24 grid place-items-center gap-3 text-center"><Loader2 className="size-8 animate-spin text-primary" />{importing && <p className="font-semibold">Getting the video ready…</p>}</div>, { job, clip: null });
  if (!clip) {
    return frame(
      <Card className="mx-auto mt-10 max-w-md space-y-3 p-8 text-center">
        <p className="text-lg font-bold">{job.state === "running" || job.state === "queued" ? "Still analysing…" : "That clip doesn't exist"}</p>
        <Button asChild><Link to={`/p/${job.id}`}>Back to the project</Link></Button>
      </Card>,
      { job, clip: null },
    );
  }

  const short = job.cuts[`s${clip.id}`];

  const backLink = back === undefined ? { to: `/p/${job.id}?clip=${clip.id}`, label: "Project" } : back;
  return frame(
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {backLink && <Button asChild variant="ghost" size="sm"><Link to={backLink.to}><ArrowLeft /> {backLink.label}</Link></Button>}
        <Badge><Film className="size-3.5" /> Director</Badge>
        <h1 className="min-w-0 flex-1 truncate text-lg font-extrabold tracking-tight">{clip.title || `Moment at ${clip.time}`}</h1>
        {clip.score != null && <ScoreDot score={clip.score} />}
        {actions}
      </div>

      <div className="grid gap-4 lg:h-[calc(100vh-24rem)] lg:min-h-[480px] lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)_minmax(300px,360px)]">
        {/* transcript */}
        <div className="h-[420px] min-h-0 lg:h-auto">
          {ws.status === "ready" ? (
            <TranscriptPanel
              words={words}
              fillers={fillers}
              deleted={deleted}
              active={activeId}
              playing={player.playing}
              mode={mode}
              onMode={setMode}
              onSeek={(w) => player.seek(w.s)}
              onToggle={toggleWord}
              onSelectFillers={() => setDeleted((d) => new Set([...d, ...fillers]))}
              onReset={() => setDeleted(new Set())}
              removedSeconds={removed}
              edits={edits}
              onEdit={editWord}
              onResetEdits={() => setEdits(new Map())}
            />
          ) : (
            <div className="grid h-full place-items-center rounded-lg border bg-card p-6 text-center">
              {ws.status === "loading" ? (
                <div className="space-y-2"><Loader2 className="mx-auto size-7 animate-spin text-primary" /><p className="font-semibold">{ready ? "Reading the transcript…" : "Waiting for the analysis…"}</p><p className="text-sm text-muted-foreground">The first time can take a minute while this clip is transcribed.</p></div>
              ) : (
                <div className="space-y-2"><ShieldAlert className="mx-auto size-7 text-muted-foreground" /><p className="font-semibold">No transcript</p><p className="text-sm text-muted-foreground">{ws.message}</p></div>
              )}
            </div>
          )}
        </div>

        {/* 9:16 preview */}
        <div className="flex min-h-[460px] min-w-0 flex-col items-center gap-3">
          <div className="min-h-0 w-full flex-1">
            <ReframeCanvas
              getVideo={getVideo}
              hasPicture={!!media?.video}
              layout={options.layout as Layout}
              info={layoutInfo}
              ratio={options.split_ratio}
              camBox={options.cam_box}
              words={captionWords}
              time={player.time}
              showCaptions={options.captions !== "none"}
              capStyle={capStyle}
              capFont={capFont}
              hook={clip.hook}
              showHook={options.hook}
              stakes={options.context ? options.context_text || clip.context || "" : ""}
              safeZone={safeZone}
              onCut={startCut}
              cutting={cuttingNow}
              onMoveCaptions={(p) => setOptions((o) => ({ ...o, caption_style: { ...o.caption_style, x: p.x, y: p.y } }))}
            />
          </div>
          <div className="flex w-full items-center justify-center gap-3">
            <Button variant="secondary" onClick={player.toggle} disabled={!media || !player.ready}>{player.playing ? "Pause" : "Play"}</Button>
            <span className="tabular text-sm font-semibold">{fmtPrecise(player.time)} / {fmtPrecise(clip.duration - removed)}</span>
            <Tip text="Shows the area TikTok, Shorts and Reels cover with their own buttons and captions">
              <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground"><input type="checkbox" checked={safeZone} onChange={(e) => setSafeZone(e.target.checked)} className="size-4 accent-[hsl(var(--primary))]" /> Safe zone</label>
            </Tip>
          </div>
          <TrimFields clip={clip} total={job.result?.source.duration ?? 0} value={trim} onChange={setTrim} disabled={deleted.size > 0} />
          {/* the real element: hidden, the canvas shows it */}
          {media && <video ref={player.ref} src={media.url} playsInline preload="auto" className="hidden" />}
        </div>

        {/* export */}
        <div className="flex h-[560px] min-h-0 flex-col gap-2 lg:h-auto">
          <div role="tablist" aria-label="Director panels" className="grid grid-cols-2 gap-1 rounded-lg bg-secondary p-1">
            {([["export", "Layout & export", Film], ["captions", "Captions", Type]] as const).map(([key, label, Icon]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={panel === key}
                onClick={() => setPanel(key)}
                className={cn("inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-semibold transition-colors", panel === key ? "bg-background text-foreground shadow" : "text-muted-foreground hover:text-foreground")}
              >
                <Icon className="size-4" /> {label}
              </button>
            ))}
          </div>
          {panel === "captions" && (
            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-card p-4">
              <CaptionsPanel
                caps={caps}
                options={options}
                onChange={setOptions}
                editCount={edits.size}
                onEditWords={() => setMode("text")}
                onUndoEdits={() => setEdits(new Map())}
                autoY={autoY}
              />
            </div>
          )}
          <div className={cn("min-h-0 flex-1", panel !== "export" && "hidden")}>
          <ExportPanel
            jobId={job.id}
            caps={caps}
            options={options}
            onChange={setOptions}
            short={short}
            busy={busy}
            error={exportError}
            deletedCount={deleted.size}
            onExport={exportShort}
            preview={job.cuts[`p${clip.id}`]}
            previewBusy={previewing}
            onPreview={startPreview}
            previewError={previewError}
            publishClip={session?.desktop ? clip : undefined}
            canExport={job.state !== "queued" && job.state !== "running" && ws.status !== "loading"}
            stakes={clip.context || ""}
            hookScore={clip.hook_score}
            hookReason={clip.hook_reason}
            layoutInfo={layoutInfo}
            layoutState={layoutState}
            layoutError={layoutError}
            onDrawCam={() => {
              getVideo()?.pause();
              setDrawingCam(true);
            }}
          />
          {drawingCam && (
            <CamBoxEditor
              getVideo={getVideo}
              value={options.cam_box}
              detected={layoutInfo?.cam ?? null}
              onSave={(box) => setOptions((o) => ({ ...o, cam_box: box, layout: o.layout === "auto" || CAM_LAYOUTS.includes(o.layout as Layout) ? o.layout : "split" }))}
              onClose={() => setDrawingCam(false)}
            />
          )}
          </div>
        </div>
      </div>

      {/* timeline */}
      <div className="mt-4 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Timeline</h2>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Zoom
            <input type="range" min={1} max={10} step={0.5} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Timeline zoom" className="w-28 accent-[hsl(var(--primary))]" />
          </label>
        </div>
        <Timeline duration={clip.duration} time={player.time} playing={player.playing} onSeek={player.seek} zoom={zoom}>
          <Track label="Video" icon={<Film className="size-4" />} height={52}>
            <Block start={0} end={clip.duration} className="border border-primary/40 bg-primary/15" />
            {clip.beats.map((b, i) => (
              <Block key={i} start={b.start - clip.start} end={b.end - clip.start} onClick={() => player.seek(b.start - clip.start)} title={`${BEAT_META[b.type].label}: ${b.reason}`} className={cn("rounded-sm", BEAT_META[b.type].bar)} style={{ bottom: "auto", height: 8, top: 8 }} />
            ))}
            {cuts.map(([a, b], i) => (
              <Block key={`c${i}`} start={a} end={b} inset={0} className="rounded-none border-x border-destructive bg-destructive/50 [background-image:repeating-linear-gradient(45deg,transparent_0_5px,rgba(0,0,0,.25)_5px_10px)]" title="Cut" />
            ))}
          </Track>
          <Track label="Audio" icon={<AudioLines className="size-4" />} height={52}>
            <Waveform state={waveform} />
            {cuts.map(([a, b], i) => <Block key={i} start={a} end={b} inset={0} className="rounded-none bg-destructive/40" />)}
          </Track>
          <Track label="Text" icon={<Type className="size-4" />} height={48}>
            {words.map((w) => (
              <WordChip key={w.i} w={edits.has(w.i) ? { ...w, w: edits.get(w.i) || "·" } : w} cut={deleted.has(w.i)} active={w.i === activeId} onClick={() => onWord(w)} />
            ))}
            {!words.length && <span className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">{ws.status === "loading" ? "Reading the transcript…" : "No transcript for this clip"}</span>}
          </Track>
        </Timeline>
        <p className="text-sm text-muted-foreground">Click or drag the ruler to scrub · click a word to jump to it · Space plays, arrows step (Shift for 5 s){waveform.status === "skipped" ? ` · ${waveform.note}` : ""}</p>
      </div>
    </>,
    { job, clip },
  );
}
