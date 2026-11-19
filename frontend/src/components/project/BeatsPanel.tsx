import { Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useHealth } from "@/hooks/data";
import { BEAT_META, BEAT_ORDER, groupBeats } from "@/lib/beats";
import type { Beat, BeatType, Clip } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";

/** Beat counts as a single coloured bar: how the clip's story is shaped at a glance. */
export function BeatBar({ clip, className }: { clip: Clip; className?: string }) {
  const total = Math.max(clip.duration, 0.1);
  return (
    <div className={cn("flex h-2.5 w-full overflow-hidden rounded-full bg-secondary", className)} aria-hidden>
      {clip.beats.map((b, i) => (
        <div key={i} className={BEAT_META[b.type].bar} style={{ width: `${((b.end - b.start) / total) * 100}%` }} />
      ))}
    </div>
  );
}

export function ScoreDot({ score, className }: { score: number; className?: string }) {
  const tone = score >= 75 ? "bg-beat-payoff/15 text-beat-payoff" : score >= 50 ? "bg-beat-build/15 text-beat-build" : "bg-secondary text-muted-foreground";
  return <span className={cn("inline-grid h-8 min-w-8 place-items-center rounded-full px-2 text-sm font-bold tabular", tone, className)}>{Math.round(score)}</span>;
}

interface Props {
  clip: Clip;
  active: Beat | null;
  /** seek the player to a beat (clip-relative seconds are worked out by the caller) */
  onSeek: (b: Beat) => void;
}

export function BeatsPanel({ clip, active, onSeek }: Props) {
  const [filter, setFilter] = useState<BeatType | "all">("all");
  const health = useHealth().data;
  const grouped = useMemo(() => groupBeats(clip.beats), [clip.beats]);

  if (!clip.beats.length) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center">
        <Sparkles className="mx-auto mb-2 size-8 text-muted-foreground" />
        <p className="font-semibold">No story beats for this clip</p>
        <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
          {health && health.llm === "ollama" && !health.ollama.running
            ? health.ollama.hint
            : "Beats come from the AI reading the transcript. They're missing when the clip has no speech or the AI wasn't available for this run."}
        </p>
      </div>
    );
  }

  const shown = clip.beats.filter((b) => filter === "all" || b.type === filter);
  return (
    <div className="space-y-4">
      <div>
        <BeatBar clip={clip} />
        <div className="mt-3 flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setFilter("all")}
            className={cn("rounded-full border px-3 py-1 text-sm font-semibold", filter === "all" ? "border-primary bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground")}
          >
            All {clip.beats.length}
          </button>
          {BEAT_ORDER.filter((t) => grouped[t].length).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setFilter(filter === t ? "all" : t)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-semibold transition-colors",
                filter === t ? "border-transparent text-white " + BEAT_META[t].bar : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span className={cn("size-2 rounded-full", filter === t ? "bg-white" : BEAT_META[t].bar)} />
              {BEAT_META[t].label} {grouped[t].length}
            </button>
          ))}
        </div>
      </div>

      <ol className="space-y-2">
        {shown.map((b, i) => {
          const meta = BEAT_META[b.type];
          const isActive = active === b || (active && active.start === b.start && active.type === b.type);
          return (
            <li key={`${b.start}-${i}`}>
              <button
                type="button"
                onClick={() => onSeek(b)}
                className={cn(
                  "flex w-full gap-3 rounded-lg border p-3 text-left transition-colors hover:border-primary/50",
                  isActive ? "border-primary bg-primary/5" : "bg-card",
                )}
              >
                <span className={cn("mt-0.5 w-1.5 shrink-0 self-stretch rounded-full", meta.bar)} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className={cn("text-sm font-bold", meta.text)}>{meta.label}</span>
                    <span className="text-sm tabular text-muted-foreground">
                      {fmtTime(b.start)} – {fmtTime(b.end)}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-sm text-foreground/90">{b.reason || meta.blurb}</span>
                </span>
                <ScoreDot score={b.score} />
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
