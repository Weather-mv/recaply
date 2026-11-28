import { Pencil, RotateCcw } from "lucide-react";
import { type ReactNode, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { SwitchRow } from "@/components/ui/switch";
import { ENTERS, HI_MODES, MOTIONS, resolveStyle, type CaptionStyle, type StyleOverrides } from "@/lib/captions";
import type { Capabilities, ShortOptions } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  caps: Capabilities;
  options: ShortOptions;
  onChange: (o: ShortOptions) => void;
  /** caption words retyped in the transcript */
  editCount: number;
  onEditWords: () => void;
  onUndoEdits: () => void;
  /** where the captions sit by default for the current layout (share of the height) */
  autoY: number;
}

const PLACES = [
  { label: "Top", y: 0.2 },
  { label: "Middle", y: 0.5 },
  { label: "Bottom", y: 0.72 },
];

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="space-y-3 border-t pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-bold">{title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function Segmented<T extends string>({ label, value, items, onChange }: { label: string; value: T; items: { key: T; label: string; hint?: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="grid gap-1 rounded-lg bg-secondary p-1" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          role="radio"
          aria-checked={value === it.key}
          title={it.hint}
          onClick={() => onChange(it.key)}
          className={cn("rounded-md px-2 py-1.5 text-sm font-semibold transition-colors", value === it.key ? "bg-background text-foreground shadow" : "text-muted-foreground hover:text-foreground")}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

function Slider({ id, label, value, min, max, step, shown, onChange }: { id: string; label: string; value: number; min: number; max: number; step: number; shown: string; onChange: (v: number) => void }) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <Label htmlFor={id} className="mb-0">{label}</Label>
        <span className="tabular text-sm text-muted-foreground">{shown}</span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-2 w-full accent-[hsl(var(--primary))]" />
    </div>
  );
}

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-sm font-semibold">
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className="size-7 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="tabular text-xs uppercase text-muted-foreground">{value}</span>
    </label>
  );
}

/** a small sample of a preset: three words, the middle one spoken */
function PresetTile({ label, st, on, onClick }: { label: string; st: CaptionStyle; on: boolean; onClick: () => void }) {
  const words = st.upper ? ["THIS", "IS", "IT"] : ["This", "is", "it"];
  const stroke = st.stroke_w > 0 ? `${Math.max(1, st.stroke_w / 4)}px ${st.stroke}` : undefined;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className={cn("flex flex-col items-center gap-1.5 rounded-lg border p-2 transition-colors", on ? "border-primary ring-2 ring-primary/40" : "hover:border-primary/50")}
    >
      <span className="flex h-12 w-full items-center justify-center rounded-md bg-gradient-to-br from-slate-600 to-slate-900 px-1">
        <span className="inline-flex gap-1 rounded px-1 text-[15px] font-black leading-tight" style={{ background: st.bg_op > 0 ? hexA(st.bg, st.bg_op) : undefined }}>
          {words.map((w, i) => {
            const hot = i === 1;
            return (
              <span
                key={i}
                style={{
                  color: hot && st.hi_mode === "color" ? st.hi : st.color,
                  background: hot && st.hi_mode === "box" ? st.hi_bg : undefined,
                  WebkitTextStroke: stroke,
                  paintOrder: "stroke fill",
                  borderRadius: 3,
                  padding: hot && st.hi_mode === "box" ? "0 2px" : undefined,
                }}
              >
                {w}
              </span>
            );
          })}
        </span>
      </span>
      <span className="text-sm font-semibold">{label}</span>
    </button>
  );
}

function hexA(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/** The Captions tab: words, look, colours, animation and position of the burned-in captions. */
export function CaptionsPanel({ caps, options, onChange, editCount, onEditWords, onUndoEdits, autoY }: Props) {
  const presets = caps.captions.filter((c) => c.style);
  const presetKey = options.captions === "none" || options.captions === "brand" ? options.captions : options.captions;
  const base = presets.find((p) => p.key === options.captions)?.style ?? presets[0]?.style;
  const o: StyleOverrides = options.caption_style ?? {};
  const st = resolveStyle(base, o);
  const on = options.captions !== "none";
  const last = useRef("bold");
  if (presetKey !== "none") last.current = presetKey;
  const lastPreset = last.current;

  const setStyle = (patch: StyleOverrides) => {
    const next: Record<string, unknown> = { ...o, ...patch };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
    onChange({ ...options, caption_style: next as StyleOverrides });
  };
  const pickPreset = (key: string) => {
    // a new preset brings its own look; the font, position and size you set stay
    const keep: StyleOverrides = {};
    if (o.font) keep.font = o.font;
    if (o.y != null) keep.y = o.y;
    if (o.x != null) keep.x = o.x;
    if (o.w != null) keep.w = o.w;
    onChange({ ...options, captions: key, caption_style: keep });
  };
  const changed = Object.keys(o).length > 0;

  return (
    <div className="space-y-4">
      <SwitchRow title="Show captions" hint="Burned into the video, timed to every word" checked={on} onChange={(v) => onChange({ ...options, captions: v ? lastPreset : "none" })} />
      {on && (
        <>
          <Section title="Style">
            <div role="radiogroup" aria-label="Caption style" className="grid grid-cols-2 gap-2">
              {presets.map((p) => (
                <PresetTile key={p.key} label={p.label} st={resolveStyle(p.style, {})} on={options.captions === p.key} onClick={() => pickPreset(p.key)} />
              ))}
            </div>
            {caps.captions.some((c) => c.key === "brand") && (
              <Button type="button" size="sm" variant={options.captions === "brand" ? "default" : "secondary"} className="w-full" onClick={() => pickPreset("brand")}>
                Use my brand kit style
              </Button>
            )}
          </Section>

          <Section
            title="Text"
            action={editCount > 0 ? <Button type="button" size="sm" variant="ghost" onClick={onUndoEdits}><RotateCcw /> Undo {editCount}</Button> : undefined}
          >
            <Button type="button" variant="secondary" className="w-full" onClick={onEditWords}>
              <Pencil /> {editCount ? `Edit words (${editCount} changed)` : "Edit the words"}
            </Button>
            <p className="text-sm text-muted-foreground">Opens the transcript in Edit text mode: click any word to retype it, or clear it to hide it.</p>
            {!!caps.caption_fonts?.length && (
              <div>
                <Label htmlFor="cap-font">Font</Label>
                <Select id="cap-font" value={o.font ?? ""} onChange={(e) => setStyle({ font: e.target.value || undefined })}>
                  <option value="">Brand kit font</option>
                  {caps.caption_fonts.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </Select>
              </div>
            )}
            <Slider id="cap-size" label="Size" value={st.size} min={28} max={160} step={1} shown={`${st.size}`} onChange={(v) => setStyle({ size: v })} />
            <Slider id="cap-per" label="Words on screen" value={st.per} min={1} max={8} step={1} shown={`up to ${st.per}`} onChange={(v) => setStyle({ per: v })} />
            <SwitchRow title="ALL CAPS" checked={st.upper} onChange={(v) => setStyle({ upper: v })} />
          </Section>

          <Section title="Colours">
            <div className="grid gap-2">
              <ColorField label="Text" value={st.color} onChange={(v) => setStyle({ color: v })} />
            </div>
            <div>
              <Label>Spoken word</Label>
              <Segmented label="Spoken word highlight" value={st.hi_mode} items={HI_MODES} onChange={(v) => setStyle({ hi_mode: v })} />
            </div>
            {st.hi_mode === "color" && <ColorField label="Spoken word colour" value={st.hi} onChange={(v) => setStyle({ hi: v })} />}
            {st.hi_mode === "box" && <ColorField label="Box colour" value={st.hi_bg} onChange={(v) => setStyle({ hi_bg: v })} />}
            <ColorField label="Outline" value={st.stroke} onChange={(v) => setStyle({ stroke: v })} />
            <Slider id="cap-stroke" label="Outline width" value={st.stroke_w} min={0} max={16} step={0.5} shown={st.stroke_w ? `${st.stroke_w}` : "off"} onChange={(v) => setStyle({ stroke_w: v })} />
            <ColorField label="Background" value={st.bg} onChange={(v) => setStyle({ bg: v })} />
            <Slider id="cap-bgop" label="Background opacity" value={st.bg_op} min={0} max={1} step={0.05} shown={st.bg_op ? `${Math.round(st.bg_op * 100)}%` : "off"} onChange={(v) => setStyle({ bg_op: v })} />
          </Section>

          <Section title="Animation">
            <div>
              <Label>Each line comes in with</Label>
              <Segmented label="Line entrance" value={st.enter ?? "pop"} items={ENTERS} onChange={(v) => setStyle({ enter: v })} />
            </div>
            <div>
              <Label>Spoken word</Label>
              <Segmented label="Word animation" value={st.motion ?? "none"} items={MOTIONS} onChange={(v) => setStyle({ motion: v })} />
              <p className="mt-1.5 text-sm text-muted-foreground">{MOTIONS.find((m) => m.key === (st.motion ?? "none"))?.hint}</p>
            </div>
          </Section>

          <Section
            title="Position"
            action={o.y != null || o.x != null || o.w != null ? <Button type="button" size="sm" variant="ghost" onClick={() => setStyle({ y: undefined, x: undefined, w: undefined })}>Auto</Button> : undefined}
          >
            <p className="text-sm text-muted-foreground">Drag the captions on the preview, or use the controls below. They snap to the middle.</p>
            <div>
              <Label>Quick place</Label>
              <div className="grid grid-cols-3 gap-1 rounded-lg bg-secondary p-1">
                {PLACES.map((p) => {
                  const active = o.y != null && Math.abs(o.y - p.y) < 0.005 && (o.x ?? 0.5) === 0.5;
                  return (
                    <button
                      key={p.label}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setStyle({ y: p.y, x: undefined })}
                      className={cn("rounded-md px-2 py-1.5 text-sm font-semibold transition-colors", active ? "bg-background text-foreground shadow" : "text-muted-foreground hover:text-foreground")}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <Slider id="cap-y" label="Up and down" value={o.y ?? autoY} min={0.08} max={0.92} step={0.01} shown={o.y == null ? "auto" : `${Math.round((1 - o.y) * 100)}% from the bottom`} onChange={(v) => setStyle({ y: v })} />
            <Slider id="cap-x" label="Left and right" value={o.x ?? 0.5} min={0.15} max={0.85} step={0.01} shown={o.x == null || o.x === 0.5 ? "centred" : o.x < 0.5 ? `${Math.round((0.5 - o.x) * 100)}% left` : `${Math.round((o.x - 0.5) * 100)}% right`} onChange={(v) => setStyle({ x: Math.abs(v - 0.5) < 0.015 ? undefined : v })} />
            <Slider id="cap-w" label="Line width" value={o.w ?? 0.84} min={0.3} max={0.92} step={0.01} shown={`${Math.round((o.w ?? 0.84) * 100)}% of the screen`} onChange={(v) => setStyle({ w: v })} />
            <p className="text-sm text-muted-foreground">A narrower line wraps the words onto more lines. Auto keeps them on the facecam seam or above the app buttons; turn on Safe zone under the preview to check.</p>
          </Section>

          <Section title="Extras">
            <SwitchRow title="Emoji" hint="A matching emoji pops above a line now and then" checked={options.emoji} onChange={(v) => onChange({ ...options, emoji: v })} />
            <div>
              <Label htmlFor="cap-lang">Translate captions</Label>
              <Select id="cap-lang" value={options.translate} onChange={(e) => onChange({ ...options, translate: e.target.value })}>
                <option value="">Original language</option>
                {caps.languages.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </Select>
              {options.translate && editCount > 0 && <p className="mt-1.5 text-sm text-muted-foreground">Translated captions replace your edited words.</p>}
            </div>
          </Section>

          {changed && (
            <Button type="button" variant="ghost" className="w-full" onClick={() => onChange({ ...options, caption_style: {} })}>
              <RotateCcw /> Reset to the {presets.find((p) => p.key === options.captions)?.label ?? "preset"} look
            </Button>
          )}
        </>
      )}
    </div>
  );
}
