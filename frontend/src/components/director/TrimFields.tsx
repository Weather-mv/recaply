import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Clip } from "@/lib/types";
import { cn, fmtPrecise } from "@/lib/utils";

interface Props {
  clip: Clip;
  total: number; // length of the whole video, seconds
  value: { start: number; end: number } | null;
  onChange: (v: { start: number; end: number } | null) => void;
  disabled?: boolean;
}

const STEP = 0.5;
const MIN = 2;

/** Nudge where the clip starts and ends in the source video. Used by the 5-second preview and by the export. */
export function TrimFields({ clip, total, value, onChange, disabled }: Props) {
  const start = value?.start ?? clip.start;
  const end = value?.end ?? clip.end;
  const max = total > 0 ? total : clip.end + 600;
  const bad = end - start < MIN;
  const set = (s: number, e: number) => onChange(Math.abs(s - clip.start) < 0.04 && Math.abs(e - clip.end) < 0.04 ? null : { start: s, end: e });
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo));

  const field = (label: string, v: number, onNum: (n: number) => void, id: string) => (
    <div className="min-w-0 flex-1">
      <label htmlFor={id} className="mb-1 block text-xs font-bold uppercase tracking-wide text-muted-foreground">{label}</label>
      <div className="flex items-center gap-1">
        <button type="button" aria-label={`${label} earlier`} disabled={disabled} onClick={() => onNum(v - STEP)} className="grid size-9 shrink-0 place-items-center rounded-full border text-lg font-bold hover:bg-accent disabled:opacity-50">−</button>
        <input
          id={id}
          type="number"
          step={STEP}
          min={0}
          max={max}
          value={Number(v.toFixed(2))}
          disabled={disabled}
          onChange={(e) => onNum(Number(e.target.value))}
          className="h-9 w-full min-w-0 rounded-full border border-input bg-transparent px-2 text-center text-sm tabular disabled:opacity-50"
        />
        <button type="button" aria-label={`${label} later`} disabled={disabled} onClick={() => onNum(v + STEP)} className="grid size-9 shrink-0 place-items-center rounded-full border text-lg font-bold hover:bg-accent disabled:opacity-50">+</button>
      </div>
    </div>
  );

  return (
    <div className={cn("w-full space-y-2 rounded-lg border p-3", disabled && "opacity-70")}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Cut points</h3>
        <span className={cn("tabular text-sm", bad ? "text-destructive" : "text-muted-foreground")}>{fmtPrecise(end - start)} long</span>
        <Button type="button" size="sm" variant="ghost" onClick={() => onChange(null)} disabled={disabled || !value} aria-label="Reset the cut points">
          <RotateCcw /> Reset
        </Button>
      </div>
      <div className="flex gap-3">
        {field("Starts at (s)", start, (n) => set(clamp(n, 0, end - MIN), end), "trim-start")}
        {field("Ends at (s)", end, (n) => set(start, clamp(n, start + MIN, max)), "trim-end")}
      </div>
      <p className="text-xs text-muted-foreground">
        {disabled ? "Cut points cannot change while words are cut: the edited clip keeps its own range." : "Seconds in the source video. Applies to the preview and the export."}
      </p>
    </div>
  );
}
