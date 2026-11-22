import { CheckCircle2, Download, Eye, Loader2, Rocket, ScanFace, Crop, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { SwitchRow } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { clipFileUrl } from "@/lib/api";
import { LAYOUT_NOTES, type Layout } from "@/lib/reframe";
import type { Capabilities, Clip, CutInfo, ShortOptions } from "@/lib/types";
import { PublishPanel } from "./PublishPanel";

interface Props {
  jobId: string;
  caps: Capabilities;
  options: ShortOptions;
  onChange: (o: ShortOptions) => void;
  short: CutInfo | undefined;
  busy: string;
  error: string;
  deletedCount: number;
  onExport: () => void;
  canExport: boolean;
  /** the free 5-second low-res preview of this clip with the current crop and captions */
  preview?: CutInfo;
  previewBusy?: boolean;
  onPreview?: () => void;
  previewError?: string;
  /** set on the desktop app: lets the finished short be published or scheduled */
  publishClip?: Clip;
}

const REFRAME = [
  { layout: "auto", label: "Auto-reframe", hint: "Follows face or action", Icon: ScanFace },
  { layout: "center", label: "Center crop", hint: "Fixed crop", Icon: Crop },
] as const;

export function ExportPanel({ jobId, caps, options, onChange, short, busy, error, deletedCount, onExport, canExport, preview, previewBusy, onPreview, previewError, publishClip }: Props) {
  const set = <K extends keyof ShortOptions>(k: K, v: ShortOptions[K]) => onChange({ ...options, [k]: v });
  const rendering = !!busy || short?.state === "queued" || short?.state === "rendering";
  const previewing = !!previewBusy || preview?.state === "queued" || preview?.state === "rendering";

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto rounded-lg border bg-card p-4">
      <h3 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Export</h3>

      <div>
        <Label htmlFor="exp-preset">Preset</Label>
        <Select id="exp-preset" value={options.export} onChange={(e) => set("export", e.target.value)}>
          {caps.exports.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </Select>
      </div>
      <div>
        <Label>Framing</Label>
        <div role="radiogroup" aria-label="Framing" className="grid grid-cols-2 gap-1 rounded-full border bg-secondary/50 p-1">
          {REFRAME.map(({ layout, label, hint, Icon }) => (
            <button
              key={layout}
              type="button"
              role="radio"
              aria-checked={options.layout === layout}
              onClick={() => set("layout", layout)}
              className={cn(
                "flex h-11 flex-col items-center justify-center rounded-full text-sm font-semibold leading-tight transition-colors",
                options.layout === layout ? "bg-primary text-primary-foreground shadow" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span className="inline-flex items-center gap-1.5"><Icon className="size-4" /> {label}</span>
              <span className="text-xs font-normal opacity-80">{hint}</span>
            </button>
          ))}
        </div>
      </div>
      <div>
        <Label htmlFor="exp-layout">All layouts (live preview)</Label>
        <Select id="exp-layout" value={options.layout} onChange={(e) => set("layout", e.target.value)}>
          {caps.layouts.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </Select>
        <p className="mt-1.5 text-sm text-muted-foreground">{LAYOUT_NOTES[options.layout as Layout] ?? ""}</p>
      </div>
      <div>
        <Label htmlFor="exp-captions">Captions</Label>
        <Select id="exp-captions" value={options.captions} onChange={(e) => set("captions", e.target.value)}>
          {caps.captions.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </Select>
      </div>
      <div>
        <Label htmlFor="exp-lang">Translate captions</Label>
        <Select id="exp-lang" value={options.translate} onChange={(e) => set("translate", e.target.value)}>
          <option value="">Original language</option>
          {caps.languages.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </Select>
      </div>

      <div className="divide-y">
        <SwitchRow title="Hook text" hint="Opening line for the first 3 seconds" checked={options.hook} onChange={(v) => set("hook", v)} />
        <SwitchRow title="Emoji in captions" checked={options.emoji} onChange={(v) => set("emoji", v)} />
        <SwitchRow title="Tighten" hint="Remove fillers and long pauses" checked={options.tighten} onChange={(v) => set("tighten", v)} />
        <SwitchRow title="B-roll" hint="Stock cut-aways matched to keywords" checked={options.broll} onChange={(v) => set("broll", v)} />
        <SwitchRow title="Cinematic teaser" hint="A 3 second cold open with the best frame, then a glitch into the clip" checked={options.teaser} onChange={(v) => set("teaser", v)} />
      </div>
      <div>
        <Label htmlFor="exp-music">Background music</Label>
        <Select id="exp-music" value={options.bgm} onChange={(e) => set("bgm", e.target.value)} disabled={!caps.music.length}>
          <option value="">No music</option>
          {caps.music.length > 0 && <option value="auto">Auto (one per clip)</option>}
          {caps.music.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
        </Select>
        <p className="mt-1.5 text-sm text-muted-foreground">
          {caps.music.length ? "It dips automatically while someone is speaking." : "Add music files to the assets/bgm folder, then reload this page."}
        </p>
      </div>

      {onPreview && (
        <div className="space-y-2 rounded-lg border p-3">
          <Button type="button" variant="secondary" className="w-full" onClick={onPreview} disabled={previewing || !canExport}>
            {previewing ? <Loader2 className="animate-spin" /> : <Eye />}
            {previewing ? preview?.stage || "Rendering preview…" : "Preview 5 seconds"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">Free, low resolution: check the crop and captions before you export.</p>
          {(previewError || preview?.state === "failed") && (
            <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" /> {previewError || preview?.error || "The preview failed."}
            </p>
          )}
          {preview?.state === "done" && preview.file && !previewing && (
            <video key={preview.file} src={`${clipFileUrl(jobId, preview.file)}?v=${preview.t ?? ""}`} controls autoPlay muted loop playsInline className="mx-auto max-h-72 rounded-md bg-black" />
          )}
        </div>
      )}

      <div className="mt-auto space-y-3">
        {deletedCount > 0 && <p className="text-sm text-muted-foreground">{deletedCount} cut words are applied first, then the short is rendered.</p>}
        <Button className="w-full" size="lg" onClick={onExport} disabled={rendering || !canExport}>
          {rendering ? <Loader2 className="animate-spin" /> : <Rocket />}
          {busy || (short?.state === "rendering" ? short.stage || "Rendering…" : short?.state === "queued" ? "Queued…" : "Export short")}
        </Button>
        <p className="text-center text-sm text-muted-foreground">Counts as one clip on your plan, once.</p>

        {(error || short?.state === "failed") && (
          <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" /> {error || short?.error || "The export failed."}
          </p>
        )}
        {short?.state === "done" && short.file && (
          <div className="space-y-2 rounded-lg border border-beat-payoff/40 bg-beat-payoff/10 p-3">
            <p className="flex items-center gap-2 text-sm font-semibold text-beat-payoff"><CheckCircle2 className="size-4" /> Your short is ready</p>
            <video src={clipFileUrl(jobId, short.file)} controls playsInline className="mx-auto max-h-72 rounded-md bg-black" />
            <Button asChild variant="secondary" size="sm" className="w-full">
              <a href={`${clipFileUrl(jobId, short.file)}?dl=1`}><Download /> Download MP4</a>
            </Button>
            {short.ass && (
              <Button asChild variant="ghost" size="sm" className="w-full">
                <a href={`${clipFileUrl(jobId, short.ass)}?dl=1`}><Download /> Captions (.ass)</a>
              </Button>
            )}
            {(short.teaser || short.music) && (
              <p className="text-center text-xs text-muted-foreground">{[short.teaser && "teaser added", short.music && `music: ${short.music}`].filter(Boolean).join(" · ")}</p>
            )}
          </div>
        )}
        {publishClip && short?.state === "done" && short.file && <PublishPanel jobId={jobId} file={short.file} clip={publishClip} />}
      </div>
    </div>
  );
}
