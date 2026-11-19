import { CheckCircle2, Download, Loader2, Rocket, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { SwitchRow } from "@/components/ui/switch";
import { clipFileUrl } from "@/lib/api";
import { LAYOUT_NOTES, type Layout } from "@/lib/reframe";
import type { Capabilities, CutInfo, ShortOptions } from "@/lib/types";

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
}

export function ExportPanel({ jobId, caps, options, onChange, short, busy, error, deletedCount, onExport, canExport }: Props) {
  const set = <K extends keyof ShortOptions>(k: K, v: ShortOptions[K]) => onChange({ ...options, [k]: v });
  const rendering = !!busy || short?.state === "queued" || short?.state === "rendering";

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
        <Label htmlFor="exp-layout">Reframe layout (live preview)</Label>
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
      </div>

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
          </div>
        )}
      </div>
    </div>
  );
}
