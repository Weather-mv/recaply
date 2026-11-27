import { ChevronDown, Film, FolderOpen, GripVertical, Loader2, PanelLeftClose, PanelLeftOpen, Search, Upload, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { DirectorWorkspace } from "@/components/director/Workspace";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { pollList } from "@/hooks/data";
import { ApiError, thumbUrl } from "@/lib/api";
import type { LibraryClip, LibraryItem } from "@/lib/types";
import { ago, cn, fmtTime } from "@/lib/utils";

/** The drag payload for a clip from the bin. */
const CLIP_TYPE = "application/x-clipforge-clip";
const VIDEO_EXT = /\.(mp4|mov|mkv|webm|m4v|avi|flv|ts|mpg|mpeg|wmv)$/i;

type Pick = { job: string; clip: number };

const isBusy = (i: LibraryItem) => i.state === "queued" || i.state === "running";

function useLibrary() {
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [error, setError] = useState("");
  const last = useRef<{ rows: LibraryItem[] | null; full: number }>({ rows: null, full: 0 });
  const fetchList = useCallback(async (forceFull: boolean) => {
    try {
      if (forceFull) last.current.full = 0;
      const r = await pollList<LibraryItem>("/api/editor/library", "items", last.current.rows, isBusy, last.current.full);
      last.current = { rows: r.rows, full: r.full ? Date.now() : last.current.full };
      setItems(r.rows);
      setError("");
      return r.rows;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load your clips.");
      return null;
    }
  }, []);
  const load = useCallback(() => fetchList(true), [fetchList]);
  useEffect(() => {
    let stop = false;
    let timer = 0;
    const tick = async () => {
      const got = await fetchList(false);
      if (stop) return;
      timer = window.setTimeout(tick, got?.some(isBusy) ? 2500 : 15000);
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [fetchList]);
  return { items, error, reload: load };
}

/** Upload with progress (fetch can't report it). Resolves to the new job id. */
function uploadVideo(file: File, onProgress: (f: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/editor/upload");
    xhr.setRequestHeader("X-CSRF-Token", document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data: { id?: string; error?: string } = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300 && data.id) resolve(data.id);
      else reject(new Error(data.error || (xhr.status === 413 ? "That file is too big." : `Upload failed (${xhr.status}).`)));
    };
    xhr.onerror = () => reject(new Error("Upload failed: the connection dropped."));
    const form = new FormData();
    form.set("file", file);
    xhr.send(form);
  });
}

function isFileDrag(e: DragEvent) {
  return Array.from(e.dataTransfer.types).includes("Files");
}
function isClipDrag(e: DragEvent) {
  return Array.from(e.dataTransfer.types).includes(CLIP_TYPE);
}

function ClipRow({ item, clip, selected, onOpen }: { item: LibraryItem; clip: LibraryClip; selected: boolean; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(CLIP_TYPE, JSON.stringify({ job: item.id, clip: clip.id } satisfies Pick));
          e.dataTransfer.setData("text/plain", clip.title);
          e.dataTransfer.effectAllowed = "copy";
        }}
        onClick={onOpen}
        title="Drag into the editor, or click to open"
        className={cn(
          "group flex w-full cursor-grab items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors active:cursor-grabbing",
          selected ? "bg-primary/15 text-foreground ring-1 ring-primary/40" : "hover:bg-white/[.05]",
        )}
      >
        <GripVertical className="size-4 shrink-0 text-muted-foreground/50 group-hover:text-muted-foreground" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{clip.title}</span>
          <span className="block text-xs text-muted-foreground tabular-nums">
            {fmtTime(clip.duration)}
            {clip.score != null && ` · score ${Math.round(clip.score)}`}
            {clip.exported && " · exported"}
          </span>
        </span>
      </button>
    </li>
  );
}

function VideoCard({ item, selected, onOpen }: { item: LibraryItem; selected: Pick | null; onOpen: (p: Pick) => void }) {
  const clip = item.clips[0];
  const busy = item.state === "queued" || item.state === "running";
  const pic = thumbUrl(item.thumb, item.extractor);
  const on = !!clip && selected?.job === item.id;
  return (
    <li>
      <button
        type="button"
        draggable={!!clip}
        disabled={busy || !clip}
        onDragStart={(e) => {
          if (!clip) return;
          e.dataTransfer.setData(CLIP_TYPE, JSON.stringify({ job: item.id, clip: clip.id } satisfies Pick));
          e.dataTransfer.setData("text/plain", item.title);
          e.dataTransfer.effectAllowed = "copy";
        }}
        onClick={() => clip && onOpen({ job: item.id, clip: clip.id })}
        title={busy ? "Getting the video ready…" : "Drag into the editor, or click to open"}
        className={cn(
          "group flex w-full cursor-grab items-center gap-2.5 rounded-lg p-1.5 text-left text-sm transition-colors active:cursor-grabbing disabled:cursor-wait",
          on ? "bg-primary/15 ring-1 ring-primary/40" : "hover:bg-white/[.05]",
        )}
      >
        <span className="grid h-10 w-16 shrink-0 place-items-center overflow-hidden rounded-md bg-secondary">
          {pic ? <img src={pic} alt="" className="size-full object-cover" loading="lazy" /> : busy ? <Loader2 className="size-4 animate-spin text-primary" /> : <Film className="size-4 text-muted-foreground" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{item.title}</span>
          <span className="block text-xs text-muted-foreground tabular-nums">{busy ? "Getting ready…" : `${fmtTime(item.duration ?? clip?.duration ?? 0)} · ${ago(item.created)}`}</span>
        </span>
      </button>
    </li>
  );
}

function ProjectGroup({ item, selected, onOpen, defaultOpen }: { item: LibraryItem; selected: Pick | null; onOpen: (p: Pick) => void; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (selected?.job === item.id) setOpen(true);
  }, [selected, item.id]);
  const busy = item.state === "queued" || item.state === "running";
  return (
    <li className="rounded-lg">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-white/[.04]">
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90")} />
        <span className="min-w-0 flex-1 truncate font-semibold">{item.title}</span>
        {busy ? <Loader2 className="size-3.5 animate-spin text-primary" /> : <span className="text-xs text-muted-foreground tabular-nums">{item.clips.length}</span>}
      </button>
      {open && (
        <ul className="ml-3 space-y-0.5 border-l pl-2">
          {item.clips.map((c) => (
            <ClipRow key={c.id} item={item} clip={c} selected={selected?.job === item.id && selected.clip === c.id} onOpen={() => onOpen({ job: item.id, clip: c.id })} />
          ))}
          {!item.clips.length && <li className="px-2 py-1.5 text-xs text-muted-foreground">{busy ? "Still finding clips…" : "No clips"}</li>}
        </ul>
      )}
    </li>
  );
}

function MediaBin({
  items,
  error,
  selected,
  onOpen,
  onFiles,
  uploading,
}: {
  items: LibraryItem[] | null;
  error: string;
  selected: Pick | null;
  onOpen: (p: Pick) => void;
  onFiles: (f: File[]) => void;
  uploading: { name: string; frac: number } | null;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [drag, setDrag] = useState(false);
  const needle = q.trim().toLowerCase();
  const match = (s: string) => !needle || s.toLowerCase().includes(needle);
  const videos = (items ?? []).filter((i) => i.editor && match(i.title));
  const projects = (items ?? [])
    .filter((i) => !i.editor)
    .map((i) => (needle && !match(i.title) ? { ...i, clips: i.clips.filter((c) => match(c.title)) } : i))
    .filter((i) => !needle || match(i.title) || i.clips.length);

  return (
    <aside aria-label="Media" className="surface flex min-h-0 flex-col gap-3 rounded-2xl p-3 xl:h-[calc(100vh-9rem)]">
      <div
        onDragOver={(e) => {
          if (!isFileDrag(e)) return;
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          if (!isFileDrag(e)) return;
          e.preventDefault();
          e.stopPropagation();
          setDrag(false);
          onFiles(Array.from(e.dataTransfer.files));
        }}
        className={cn("rounded-xl border border-dashed p-3 text-center transition-colors", drag ? "border-primary bg-primary/10" : "border-white/15")}
      >
        <input ref={fileInput} type="file" hidden multiple accept="video/*" onChange={(e) => e.target.files && onFiles(Array.from(e.target.files))} />
        {uploading ? (
          <div className="space-y-2 text-left">
            <p className="truncate text-sm font-semibold">Uploading {uploading.name}</p>
            <Progress value={uploading.frac * 100} />
            <p className="text-xs text-muted-foreground tabular-nums">{Math.round(uploading.frac * 100)}%</p>
          </div>
        ) : (
          <>
            <Upload className="mx-auto size-5 text-primary" />
            <p className="mt-1 text-sm font-semibold">Drop videos here</p>
            <p className="text-xs text-muted-foreground">or</p>
            <Button type="button" size="sm" variant="secondary" className="mt-1" onClick={() => fileInput.current?.click()}>
              <FolderOpen /> Browse
            </Button>
          </>
        )}
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search clips" aria-label="Search clips" className="h-9 pl-9" />
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {items === null && !error && <Loader2 className="mx-auto mt-6 size-5 animate-spin text-primary" />}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <section>
          <h2 className="mb-1 px-2 text-xs font-bold uppercase tracking-wide text-muted-foreground">Your videos</h2>
          {videos.length ? (
            <ul className="space-y-0.5">{videos.map((i) => <VideoCard key={i.id} item={i} selected={selected} onOpen={onOpen} />)}</ul>
          ) : (
            <p className="px-2 text-xs text-muted-foreground">{needle ? "No match." : "Videos you drop here show up in this list."}</p>
          )}
        </section>
        <section>
          <h2 className="mb-1 px-2 text-xs font-bold uppercase tracking-wide text-muted-foreground">Clips from projects</h2>
          {projects.length ? (
            <ul className="space-y-0.5">
              {projects.map((i, n) => <ProjectGroup key={i.id} item={i} selected={selected} onOpen={onOpen} defaultOpen={n === 0 || !!needle} />)}
            </ul>
          ) : (
            <p className="px-2 text-xs text-muted-foreground">{needle ? "No match." : "Clips that Clipforge finds in your projects show up here."}</p>
          )}
        </section>
      </div>
    </aside>
  );
}

export default function Editor() {
  const [params, setParams] = useSearchParams();
  const selected: Pick | null = params.get("job") ? { job: params.get("job")!, clip: Number(params.get("clip") || 1) } : null;
  const open = useCallback((p: Pick | null) => setParams(p ? { job: p.job, clip: String(p.clip) } : {}), [setParams]);
  const { items, error, reload } = useLibrary();
  const [binOpen, setBinOpen] = useState(true);
  const [over, setOver] = useState<"" | "file" | "clip">("");
  const [uploading, setUploading] = useState<{ name: string; frac: number } | null>(null);
  const [uploadError, setUploadError] = useState("");
  const depth = useRef(0);

  const onFiles = useCallback(
    async (files: File[]) => {
      const vids = files.filter((f) => f.type.startsWith("video/") || VIDEO_EXT.test(f.name));
      if (!vids.length) {
        setUploadError("Drop a video file (MP4, MOV, MKV, WebM…).");
        return;
      }
      setUploadError("");
      let first: string | null = null;
      for (const f of vids) {
        setUploading({ name: f.name, frac: 0 });
        try {
          const id = await uploadVideo(f, (frac) => setUploading({ name: f.name, frac }));
          first ??= id;
          await reload(); // so the stage knows the new video before it opens
        } catch (e) {
          setUploadError(e instanceof Error ? e.message : "Upload failed.");
        }
      }
      setUploading(null);
      if (first) open({ job: first, clip: 1 }); // the first dropped video opens right away
    },
    [open, reload],
  );

  // the whole stage is a drop target: a clip from the bin opens it, a file from the PC uploads and opens it
  const stageDrop = {
    onDragEnter: (e: DragEvent) => {
      if (!isFileDrag(e) && !isClipDrag(e)) return;
      depth.current += 1;
      setOver(isClipDrag(e) ? "clip" : "file");
    },
    onDragOver: (e: DragEvent) => {
      if (!isFileDrag(e) && !isClipDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: () => {
      depth.current = Math.max(0, depth.current - 1);
      if (!depth.current) setOver("");
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      depth.current = 0;
      setOver("");
      const raw = e.dataTransfer.getData(CLIP_TYPE);
      if (raw) {
        try {
          const p = JSON.parse(raw) as Pick;
          if (p.job) open({ job: p.job, clip: Number(p.clip) || 1 });
        } catch {
          /* not ours */
        }
        return;
      }
      if (e.dataTransfer.files?.length) void onFiles(Array.from(e.dataTransfer.files));
    },
  };

  // a clip that was deleted elsewhere: close it
  const known = useMemo(() => !selected || !items || items.some((i) => i.id === selected.job), [selected, items]);

  return (
    <AppShell wide crumbs={[{ label: "Editor" }]}>
      <div className={cn("grid gap-4", binOpen && "xl:grid-cols-[300px_minmax(0,1fr)]")}>
        {binOpen && <MediaBin items={items} error={error} selected={selected} onOpen={open} onFiles={onFiles} uploading={uploading} />}

        <section aria-label="Editing stage" className="relative min-w-0" {...stageDrop}>
          {uploadError && (
            <p role="alert" className="mb-3 flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {uploadError}
              <button type="button" aria-label="Dismiss" className="ml-auto" onClick={() => setUploadError("")}><X className="size-4" /></button>
            </p>
          )}

          {selected && known ? (
            <DirectorWorkspace
              key={`${selected.job}:${selected.clip}`}
              id={selected.job}
              clipId={selected.clip}
              back={null}
              narrow={binOpen}
              actions={
                <>
                  <Button variant="ghost" size="sm" onClick={() => setBinOpen((b) => !b)} aria-label={binOpen ? "Hide media" : "Show media"}>
                    {binOpen ? <PanelLeftClose /> : <PanelLeftOpen />} {binOpen ? "Hide media" : "Media"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => open(null)} aria-label="Close this clip"><X /> Close</Button>
                </>
              }
            />
          ) : (
            <div className="grid min-h-[60vh] place-items-center rounded-2xl border border-dashed border-white/15 p-8 text-center">
              <div className="max-w-md space-y-3">
                <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/15 text-primary"><Wand2 className="size-7" /></span>
                <h1 className="text-2xl font-extrabold tracking-tight">Editor</h1>
                <p className="text-muted-foreground">
                  Drag a clip from the media bin, or drop any video from your PC here. You get every Director tool: captions, layouts and facecam,
                  trims, word cuts, preview and export.
                </p>
                {!binOpen && <Button variant="secondary" onClick={() => setBinOpen(true)}><PanelLeftOpen /> Show media</Button>}
              </div>
            </div>
          )}

          {over && (
            <div className="pointer-events-none absolute inset-0 z-20 grid place-items-center rounded-2xl border-2 border-dashed border-primary bg-background/80 backdrop-blur-sm">
              <p className="flex items-center gap-2 text-lg font-bold">
                {over === "clip" ? <Film className="size-6 text-primary" /> : <Upload className="size-6 text-primary" />}
                {over === "clip" ? "Drop to edit this clip" : "Drop to upload and edit"}
              </p>
            </div>
          )}
        </section>
      </div>
    </AppShell>
  );
}
