import { MousePointerClick, RotateCcw, Scissors, Wand2 } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { Button } from "@/components/ui/button";
import type { Word } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";
import { paragraphs } from "@/lib/words";

export type EditMode = "seek" | "cut";

interface Props {
  words: Word[];
  fillers: Set<number>;
  deleted: Set<number>;
  active: number;
  playing: boolean;
  mode: EditMode;
  onMode: (m: EditMode) => void;
  onSeek: (w: Word) => void;
  onToggle: (w: Word) => void;
  onSelectFillers: () => void;
  onReset: () => void;
  removedSeconds: number;
}

/** The transcript. Click a word to jump the video to it; in "Cut words" mode a click strikes the word out of the clip. */
export function TranscriptPanel({ words, fillers, deleted, active, playing, mode, onMode, onSeek, onToggle, onSelectFillers, onReset, removedSeconds }: Props) {
  const paras = useMemo(() => paragraphs(words), [words]);
  const activeEl = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (playing) activeEl.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active, playing]);

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border bg-card">
      <div className="space-y-3 border-b p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Transcript</h3>
          <div className="inline-flex rounded-full bg-secondary p-0.5" role="group" aria-label="Click behaviour">
            <button type="button" onClick={() => onMode("seek")} aria-pressed={mode === "seek"} className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold", mode === "seek" ? "bg-background shadow" : "text-muted-foreground")}>
              <MousePointerClick className="size-4" /> Seek
            </button>
            <button type="button" onClick={() => onMode("cut")} aria-pressed={mode === "cut"} className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold", mode === "cut" ? "bg-destructive text-destructive-foreground shadow" : "text-muted-foreground")}>
              <Scissors className="size-4" /> Cut words
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={onSelectFillers} disabled={!fillers.size}>
            <Wand2 /> Remove fillers {fillers.size ? `(${fillers.size})` : ""}
          </Button>
          <Button size="sm" variant="ghost" onClick={onReset} disabled={!deleted.size}>
            <RotateCcw /> Reset
          </Button>
          <span className="ml-auto text-sm text-muted-foreground">{deleted.size ? `${deleted.size} words · −${removedSeconds.toFixed(1)}s` : mode === "cut" ? "Click words to cut them" : "Click a word to jump"}</span>
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-base leading-8">
        {paras.map((p, pi) => (
          <p key={pi}>
            <span className="mr-2 select-none text-sm tabular text-muted-foreground">{fmtTime(p[0].s)}</span>
            {p.map((w) => {
              const cut = deleted.has(w.i);
              const on = w.i === active;
              return (
                <button
                  key={w.i}
                  type="button"
                  ref={on ? activeEl : undefined}
                  onClick={() => (mode === "cut" ? onToggle(w) : onSeek(w))}
                  title={`${fmtTime(w.s)}${fillers.has(w.i) ? " · filler" : ""}`}
                  className={cn(
                    "mr-1 inline rounded px-1 py-0.5 text-left transition-colors",
                    cut ? "bg-destructive/10 text-destructive line-through decoration-2" : on ? "bg-primary text-primary-foreground" : "hover:bg-accent",
                    !cut && !on && fillers.has(w.i) && "underline decoration-dotted decoration-beat-build underline-offset-4",
                    mode === "cut" && !cut && "hover:bg-destructive/15",
                  )}
                >
                  {w.w}
                </button>
              );
            })}
          </p>
        ))}
      </div>
    </div>
  );
}
