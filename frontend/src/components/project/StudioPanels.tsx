import { Captions, Check, ExternalLink, Image as ImageIcon, Palette, Quote } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useBoot } from "@/hooks/data";
import { api } from "@/lib/api";
import type { Clip } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";

// ---------- bottom timeline: the whole source video and every cut on it ----------

/** The video track of the whole source with each clip as a segment (colour = score); click a segment to open that clip. */
export function SourceTimeline({ duration, clips, selected, onSelect, playhead }: { duration: number; clips: Clip[]; selected: number; onSelect: (id: number) => void; playhead: number }) {
  const total = Math.max(1, duration || Math.max(...clips.map((c) => c.end), 1));
  const pct = (s: number) => `${(Math.min(Math.max(s, 0), total) / total) * 100}%`;
  const ticks = 8;
  return (
    <div className="surface rounded-xl p-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wide text-muted-foreground">Source timeline</span>
        <span className="tabular-nums text-muted-foreground">{clips.length} cuts · {fmtTime(total)}</span>
      </div>
      <div className="flex">
        <div className="w-14 shrink-0 space-y-1.5 pt-5 text-[11px] font-medium text-muted-foreground">
          <div className="flex h-9 items-center">Video</div>
          <div className="flex h-9 items-center">Cuts</div>
        </div>
        <div className="relative min-w-0 flex-1">
          <div className="relative mb-1 h-4">
            {Array.from({ length: ticks + 1 }, (_, i) => (
              <span key={i} className="absolute -translate-x-1/2 text-[10px] tabular-nums text-muted-foreground" style={{ left: `${(i / ticks) * 100}%` }}>{fmtTime((total * i) / ticks)}</span>
            ))}
          </div>
          {/* video track */}
          <div className="relative h-9 overflow-hidden rounded-md border border-white/10 bg-gradient-to-r from-indigo-950/70 via-slate-900 to-indigo-950/70">
            <div className="absolute inset-0 opacity-40 [background-image:repeating-linear-gradient(90deg,transparent_0,transparent_22px,rgba(255,255,255,.07)_22px,rgba(255,255,255,.07)_23px)]" />
            {clips.map((c) => (
              <span key={c.id} className="absolute inset-y-0 bg-primary/25" style={{ left: pct(c.start), width: `max(2px, calc(${pct(c.end)} - ${pct(c.start)}))` }} />
            ))}
          </div>
          {/* cut segments */}
          <div className="relative mt-1.5 h-9 rounded-md bg-white/[.03]">
            {clips.map((c, i) => {
              const hot = c.score >= 70;
              const on = c.id === selected;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onSelect(c.id)}
                  title={`${i + 1}. ${c.title || c.time} · ${Math.round(c.duration)}s · score ${Math.round(c.score)}`}
                  aria-label={`Clip ${i + 1}: ${c.title || c.time}`}
                  aria-pressed={on}
                  className={cn(
                    "absolute inset-y-0 min-w-[6px] overflow-hidden rounded-md px-1 text-left text-[11px] font-semibold leading-9 text-white transition-all",
                    hot ? "bg-emerald-500/70 hover:bg-emerald-500" : "bg-indigo-500/60 hover:bg-indigo-500",
                    on && "z-10 ring-2 ring-white",
                  )}
                  style={{ left: pct(c.start), width: `max(6px, calc(${pct(c.end)} - ${pct(c.start)}))` }}
                >
                  <span className="truncate">{i + 1}</span>
                </button>
              );
            })}
          </div>
          {/* the playhead of the selected clip, mapped onto the source */}
          <span className="pointer-events-none absolute bottom-0 top-4 w-px bg-white shadow-[0_0_6px_white]" style={{ left: pct(playhead) }} aria-hidden="true">
            <span className="absolute -left-[3px] -top-0.5 size-[7px] rotate-45 bg-white" />
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------- right panel tabs ----------

export function TranscriptTab({ clip }: { clip: Clip }) {
  const emphasis = new Set((clip.emphasis ?? []).map((w) => w.toLowerCase()));
  const words = (clip.transcript || "").split(/(\s+)/);
  return (
    <div className="space-y-4">
      {clip.hook && (
        <div className="rounded-lg border border-primary/30 bg-primary/10 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary"><Quote className="size-3.5" /> Hook</p>
          <p className="text-sm font-medium leading-snug">{clip.hook}</p>
        </div>
      )}
      {clip.transcript ? (
        <p className="text-[15px] leading-7 text-foreground/90">
          {words.map((w, i) => (emphasis.has(w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, "")) ? <mark key={i} className="rounded bg-amber-400/20 px-0.5 text-amber-200">{w}</mark> : <span key={i}>{w}</span>))}
        </p>
      ) : (
        <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No transcript for this clip.</p>
      )}
      <p className="text-xs text-muted-foreground">{fmtTime(clip.start)} – {fmtTime(clip.end)} of the video. Highlighted words are what the captions emphasise.</p>
    </div>
  );
}

const CAPTION_NOTE: Record<string, string> = {
  none: "Plain picture",
  brand: "Your brand kit's style",
  bold_yellow: "Bold, yellow highlight, bounce",
  minimal_white: "Clean white text",
  neon: "Neon glow, staggered",
};

export function CaptionsTab({ jobId, clipId }: { jobId: string; clipId: number }) {
  const { caps } = useBoot();
  const list = caps?.captions ?? [];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Word-by-word karaoke captions are drawn into the export. Pick the style in the Director, with a live preview.</p>
      <ul className="grid gap-2">
        {list.map((c) => (
          <li key={c.key} className="flex items-center gap-3 rounded-lg border bg-white/[.03] px-3 py-2.5">
            <span className="grid size-9 place-items-center rounded-md bg-black text-[13px] font-black uppercase tracking-tight" style={c.key === "bold_yellow" ? { color: "#facc15" } : c.key === "neon" ? { color: "#22d3ee", textShadow: "0 0 8px #22d3ee" } : undefined}>Aa</span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium">{c.label}</span>
              {CAPTION_NOTE[c.key] && <span className="block text-xs text-muted-foreground">{CAPTION_NOTE[c.key]}</span>}
            </span>
          </li>
        ))}
        {!list.length && <li className="text-sm text-muted-foreground">Caption styles are loading…</li>}
      </ul>
      <Link to={`/p/${jobId}/clip/${clipId}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
        <Captions className="size-4" /> Style captions in Director
      </Link>
    </div>
  );
}

interface Brand {
  kit: { logo: boolean; logo_pos: string; accent: string; text_color: string; keyword_color: string; caption_style: string; font: string; intro: boolean; outro: boolean };
}

export function BrandTab() {
  const [brand, setBrand] = useState<Brand | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api<Brand>("/api/brand").then(setBrand).catch(() => setFailed(true));
  }, []);
  if (failed) return <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">The brand kit isn't available right now.</p>;
  if (!brand) return <div className="h-40 animate-pulse rounded-lg bg-white/[.04]" />;
  const k = brand.kit;
  const Flag = ({ on, label }: { on: boolean; label: string }) => (
    <li className={cn("flex items-center justify-between rounded-lg border px-3 py-2 text-sm", on ? "bg-emerald-500/10" : "bg-white/[.03] text-muted-foreground")}>
      {label}
      <span className={cn("inline-flex items-center gap-1 text-xs font-medium", on ? "text-emerald-400" : "text-muted-foreground")}>{on ? <><Check className="size-3.5" /> Set</> : "Not set"}</span>
    </li>
  );
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Applied to every export when <span className="font-medium text-foreground">Use brand kit</span> is on in the Director.</p>
      <div>
        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground"><Palette className="size-3.5" /> Colours</p>
        <div className="flex gap-2">
          {[["Accent", k.accent], ["Text", k.text_color], ["Keywords", k.keyword_color]].map(([n, c]) => (
            <div key={n} className="flex-1 rounded-lg border bg-white/[.03] p-2 text-center">
              <span className="mx-auto mb-1.5 block h-8 rounded-md border border-white/10" style={{ background: c }} />
              <span className="block text-xs font-medium">{n}</span>
              <span className="block font-mono text-[11px] text-muted-foreground">{c}</span>
            </div>
          ))}
        </div>
      </div>
      <ul className="space-y-1.5">
        <Flag on={k.logo} label="Logo / watermark" />
        <Flag on={k.intro} label="Intro clip" />
        <Flag on={k.outro} label="Outro clip" />
      </ul>
      <p className="flex items-center gap-2 text-xs text-muted-foreground"><ImageIcon className="size-3.5" /> Caption style: <span className="font-medium text-foreground">{k.caption_style}</span> · font: <span className="font-medium text-foreground">{k.font}</span></p>
      <Link to="/brand" className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"><ExternalLink className="size-4" /> Edit brand kit</Link>
    </div>
  );
}
