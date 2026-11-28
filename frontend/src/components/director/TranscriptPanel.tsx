import { MousePointerClick, Pencil, RotateCcw, Scissors, Wand2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Word } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";
import { paragraphs } from "@/lib/words";

export type EditMode = "seek" | "cut" | "text";

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
  /** caption text typed in the studio, by word id ("" hides the word from the captions) */
  edits: Map<number, string>;
  onEdit: (w: Word, text: string) => void;
  onResetEdits: () => void;
}

/** One word being retyped: Enter or leaving the box saves it, Esc cancels. */
function WordInput({ initial, onDone }: { initial: string; onDone: (text: string | null) => void }) {
  const [v, setV] = useState(initial);
  const done = useRef(false);
  const finish = (text: string | null) => {
    if (done.current) return;
    done.current = true;
    onDone(text);
  };
  return (
    <input
      autoFocus
      value={v}
      maxLength={40}
      aria-label="Caption text for this word"
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => finish(v)}
      onKeyDown={(e) => {
        if (e.key === "Enter") finish(v);
        else if (e.key === "Escape") finish(null);
        e.stopPropagation(); // Space and the arrows type here instead of driving the player
      }}
      style={{ width: `${Math.max(3, v.length + 2)}ch` }}
      className="mr-1 inline rounded border border-primary bg-background px-1 py-0.5 text-base outline-none ring-2 ring-primary/30"
    />
  );
}

/** The transcript. Click a word to jump the video to it; "Cut words" strikes it out of the clip; "Edit text" retypes its caption. */
export function TranscriptPanel({ words, fillers, deleted, active, playing, mode, onMode, onSeek, onToggle, onSelectFillers, onReset, removedSeconds, edits, onEdit, onResetEdits }: Props) {
  const paras = useMemo(() => paragraphs(words), [words]);
  const activeEl = useRef<HTMLButtonElement | null>(null);
  const [editing, setEditing] = useState(-1);

  useEffect(() => {
    if (playing) activeEl.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active, playing]);
  useEffect(() => {
    if (mode !== "text") setEditing(-1);
  }, [mode]);

  const click = (w: Word) => {
    if (mode === "cut") onToggle(w);
    else if (mode === "text") {
      if (!deleted.has(w.i)) setEditing(w.i);
    } else onSeek(w);
  };

  const status = mode === "text"
    ? edits.size ? `${edits.size} caption ${edits.size === 1 ? "word" : "words"} changed` : "Click a word to retype it"
    : deleted.size ? `${deleted.size} words · −${removedSeconds.toFixed(1)}s` : mode === "cut" ? "Click words to cut them" : "Click a word to jump";

  const modeBtn = (m: EditMode, label: string, icon: ReactNode, on: string) => (
    <button type="button" onClick={() => onMode(m)} aria-pressed={mode === m} className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-semibold", mode === m ? on : "text-muted-foreground")}>
      {icon} {label}
    </button>
  );

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border bg-card">
      <div className="space-y-3 border-b p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">Transcript</h3>
          <div className="inline-flex rounded-full bg-secondary p-0.5" role="group" aria-label="Click behaviour">
            {modeBtn("seek", "Seek", <MousePointerClick className="size-4" />, "bg-background shadow")}
            {modeBtn("cut", "Cut", <Scissors className="size-4" />, "bg-destructive text-destructive-foreground shadow")}
            {modeBtn("text", "Edit text", <Pencil className="size-4" />, "bg-primary text-primary-foreground shadow")}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {mode === "text" ? (
            <Button size="sm" variant="ghost" onClick={onResetEdits} disabled={!edits.size}>
              <RotateCcw /> Undo text edits
            </Button>
          ) : (
            <>
              <Button size="sm" variant="secondary" onClick={onSelectFillers} disabled={!fillers.size}>
                <Wand2 /> Remove fillers {fillers.size ? `(${fillers.size})` : ""}
              </Button>
              <Button size="sm" variant="ghost" onClick={onReset} disabled={!deleted.size}>
                <RotateCcw /> Reset
              </Button>
            </>
          )}
          <span className="ml-auto text-sm text-muted-foreground">{status}</span>
        </div>
        {mode === "text" && <p className="text-sm text-muted-foreground">Changes the caption only, not the audio. Clear a word to hide it from the captions.</p>}
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-base leading-8">
        {paras.map((p, pi) => (
          <p key={pi}>
            <span className="mr-2 select-none text-sm tabular text-muted-foreground">{fmtTime(p[0].s)}</span>
            {p.map((w) => {
              const cut = deleted.has(w.i);
              const on = w.i === active;
              const edited = edits.get(w.i);
              if (editing === w.i) {
                return (
                  <WordInput
                    key={w.i}
                    initial={edited ?? w.w}
                    onDone={(text) => {
                      setEditing(-1);
                      if (text !== null) onEdit(w, text);
                    }}
                  />
                );
              }
              const hidden = edited === "";
              return (
                <button
                  key={w.i}
                  type="button"
                  ref={on ? activeEl : undefined}
                  onClick={() => click(w)}
                  title={`${fmtTime(w.s)}${fillers.has(w.i) ? " · filler" : ""}${edited !== undefined ? ` · said "${w.w}"` : ""}`}
                  className={cn(
                    "mr-1 inline rounded px-1 py-0.5 text-left transition-colors",
                    cut ? "bg-destructive/10 text-destructive line-through decoration-2" : on ? "bg-primary text-primary-foreground" : "hover:bg-accent",
                    !cut && !on && fillers.has(w.i) && "underline decoration-dotted decoration-beat-build underline-offset-4",
                    !cut && !on && edited !== undefined && "bg-primary/10 text-primary underline decoration-primary decoration-2 underline-offset-4",
                    hidden && !on && "opacity-50",
                    mode === "cut" && !cut && "hover:bg-destructive/15",
                    mode === "text" && !cut && "cursor-text hover:bg-primary/15",
                  )}
                >
                  {hidden ? <span className="italic">({w.w})</span> : edited ?? w.w}
                </button>
              );
            })}
          </p>
        ))}
      </div>
    </div>
  );
}
