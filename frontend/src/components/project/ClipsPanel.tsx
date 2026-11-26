import { cn, fmtTime } from "@/lib/utils";
import type { Clip } from "@/lib/types";
import { BeatBar, ScoreDot } from "./BeatsPanel";
import { ViralityBars } from "./ViralityBars";

export function ClipsPanel({ clips, selected, onSelect }: { clips: Clip[]; selected: number; onSelect: (id: number) => void }) {
  if (!clips.length) return <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No clips were found in this video.</p>;
  return (
    <ol className="space-y-2">
      {clips.map((c, i) => (
        <li key={c.id}>
          <button
            type="button"
            onClick={() => onSelect(c.id)}
            className={cn("block w-full rounded-lg border p-3 text-left transition-colors hover:border-primary/50", c.id === selected ? "border-primary bg-primary/5" : "bg-card")}
          >
            <span className="flex items-start gap-3">
              <span className="mt-1 w-5 shrink-0 text-center text-sm font-bold text-muted-foreground tabular">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 text-base font-semibold leading-snug">{c.title || `Moment at ${c.time}`}</span>
                <span className="mt-0.5 block text-sm text-muted-foreground">
                  {c.time} · {Math.round(c.duration)}s{c.hook ? ` · “${c.hook}”` : ""}
                </span>
              </span>
              <ScoreDot score={c.score} />
            </span>
            {c.virality && <ViralityBars v={c.virality} compact className="mt-2.5" />}
            {c.beats.length > 0 && <BeatBar clip={c} className="mt-2.5" />}
            {c.hook_score != null && (
              <span className="mt-2 flex items-start gap-2 text-sm" title={c.hook_reason}>
                <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs font-bold tabular",
                  c.hook_score >= 8 ? "bg-beat-payoff/20 text-beat-payoff" : c.hook_score >= 6 ? "bg-primary/15 text-primary" : "bg-amber-400/15 text-amber-300")}>
                  Hook {c.hook_score}/10
                </span>
                {c.context && <span className="min-w-0 truncate text-muted-foreground">Stakes: {c.context}</span>}
              </span>
            )}
            {c.reason && <span className="mt-2 line-clamp-2 block text-sm text-muted-foreground">{c.reason}</span>}
            <span className="sr-only">{fmtTime(c.start)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
