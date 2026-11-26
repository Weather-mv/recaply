import { CheckCircle2, Crop, Download, Expand, Eye, Loader2, PanelBottom, PanelTop, PictureInPicture2, Rocket, ScanFace, SquareDashed, Sparkles, TriangleAlert, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { SwitchRow } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { clipFileUrl } from "@/lib/api";
import { CAM_LAYOUTS, LAYOUT_META, LAYOUTS, resolveLayout, type Layout, type LayoutInfo } from "@/lib/reframe";
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
  /** what's on screen in this clip, measured from the cut (null while unknown) */
  layoutInfo: LayoutInfo | null;
  layoutState: "idle" | "loading" | "ready" | "error";
  layoutError?: string;
  onDrawCam: () => void;
  /** what the hook check found for this clip */
  stakes: string;
  hookScore?: number;
  hookReason?: string;
}

const ICONS: Record<Layout, typeof ScanFace> = {
  auto: Sparkles, split: PanelTop, split_bottom: PanelBottom, pip: PictureInPicture2,
  face: ScanFace, action: Zap, center: Crop, fit: Expand,
};

function LayoutStatus({ layout, info, state, error, manual }: { layout: Layout; info: LayoutInfo | null; state: string; error?: string; manual: boolean }) {
  if (state === "loading") {
    return <p className="mt-1.5 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Finding the facecam and the action…</p>;
  }
  if (state === "error") return <p className="mt-1.5 text-sm text-muted-foreground">{error || "Couldn't look at the picture"}. The preview is approximate.</p>;
  if (!info) return null;
  const drawn = manual ? "split" : resolveLayout(layout, info, null);
  const camLine = manual ? "Using the cam box you drew." : info.cam ? "Facecam found." : "No facecam found. If there is one, draw its box.";
  return (
    <p className="mt-1.5 text-sm text-muted-foreground">
      {layout === "auto" && <>Auto picks <span className="font-semibold text-foreground">{LAYOUT_META[drawn].label}</span> for this clip. </>}
      {(layout === "auto" || CAM_LAYOUTS.includes(layout)) && camLine}
    </p>
  );
}

export function ExportPanel({ jobId, caps, options, onChange, short, busy, error, deletedCount, onExport, canExport, preview, previewBusy, onPreview, previewError, publishClip, layoutInfo, layoutState, layoutError, onDrawCam, stakes, hookScore, hookReason }: Props) {
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
        {hookScore != null && (
          <p className="mb-3 rounded-lg border bg-secondary/40 p-2.5 text-sm" title={hookReason}>
            <span className="font-bold">Hook {hookScore}/10</span>
            {hookReason && <span className="text-muted-foreground"> · {hookReason}</span>}
          </p>
        )}
        <Label>Layout</Label>
        <div role="radiogroup" aria-label="Layout" className="grid grid-cols-2 gap-1.5">
          {LAYOUTS.map((key) => {
            const Icon = ICONS[key];
            const on = options.layout === key;
            return (
              <button
                key={key}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={LAYOUT_META[key].label}
                title={LAYOUT_META[key].hint}
                onClick={() => set("layout", key)}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-sm font-semibold transition-colors",
                  on ? "border-primary bg-primary text-primary-foreground shadow" : "bg-secondary/40 text-muted-foreground hover:border-primary/50 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" /> {LAYOUT_META[key].label}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-sm text-muted-foreground">{LAYOUT_META[options.layout as Layout]?.hint}</p>
        <LayoutStatus layout={options.layout as Layout} info={layoutInfo} state={layoutState} error={layoutError} manual={!!options.cam_box} />
      </div>

      {(CAM_LAYOUTS.includes(options.layout as Layout) || options.layout === "auto") && (
        <div className="space-y-3 rounded-lg border p-3">
          <div>
            <div className="flex items-center justify-between">
              <Label htmlFor="exp-camsize" className="mb-0">Facecam size</Label>
              <span className="tabular text-sm text-muted-foreground">{Math.round(options.split_ratio * 100)}%</span>
            </div>
            <input id="exp-camsize" type="range" min={0.25} max={0.6} step={0.01} value={options.split_ratio}
              onChange={(e) => set("split_ratio", Number(e.target.value))} className="mt-2 w-full accent-[hsl(var(--primary))]" />
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="secondary" size="sm" className="flex-1" onClick={onDrawCam}>
              <SquareDashed /> {options.cam_box ? "Redraw the cam box" : "Draw the cam box"}
            </Button>
            {options.cam_box && <Button type="button" variant="ghost" size="sm" onClick={() => set("cam_box", null)}>Auto-detect</Button>}
          </div>
        </div>
      )}
      <div className="divide-y">
        <SwitchRow title="Hook text" hint="Opening line for the first 3 seconds" checked={options.hook} onChange={(v) => set("hook", v)} />
        <SwitchRow title="Stakes line" hint="For the first second, so a viewer knows what's going on (the hook text follows)" checked={options.context} onChange={(v) => set("context", v)} />
        {options.context && (
          <div className="pb-3">
            <Input
              aria-label="Stakes line"
              maxLength={40}
              value={options.context_text || stakes}
              placeholder="e.g. Cops right behind him"
              onChange={(e) => set("context_text", e.target.value)}
            />
            <p className="mt-1.5 text-xs text-muted-foreground">
              {stakes ? "Written by the hook check because the clip starts mid-action. Edit it, or switch the line off." : "The hook check didn't find this clip starting mid-action. Type a line to add one."}
            </p>
          </div>
        )}
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
