import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { SCORE_AXES } from "@/lib/stages";
import type { Clip, Virality } from "@/lib/types";
import { cn } from "@/lib/utils";

const tone = (v: number) => (v >= 7.5 ? "bg-beat-payoff" : v >= 5 ? "bg-beat-build" : "bg-muted-foreground/50");

/** The four 0-10 axes (hook, engagement, value, shareability) as bars. `compact` = one thin row, for lists. */
export function ViralityBars({ v, compact, className }: { v: Virality | null | undefined; compact?: boolean; className?: string }) {
  if (!v) return null;
  if (compact) {
    return (
      <div className={cn("grid grid-cols-4 gap-1.5", className)} aria-label="Hook, engagement, value and shareability scores">
        {SCORE_AXES.map((a) => (
          <div key={a.key} title={`${a.label} ${v[a.key].toFixed(1)}/10. ${a.hint}`}>
            <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
              <div className={cn("h-full rounded-full", tone(v[a.key]))} style={{ width: `${v[a.key] * 10}%` }} />
            </div>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">{a.label}</span>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className={cn("space-y-2.5", className)}>
      {SCORE_AXES.map((a) => (
        <div key={a.key} title={a.hint}>
          <div className="mb-1 flex items-baseline justify-between text-sm">
            <span className="font-semibold">{a.label}</span>
            <span className="tabular text-muted-foreground">{v[a.key].toFixed(1)}<span className="text-xs"> / 10</span></span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-secondary" role="meter" aria-label={a.label} aria-valuemin={0} aria-valuemax={10} aria-valuenow={v[a.key]}>
            <div className={cn("h-full rounded-full transition-[width] duration-500", tone(v[a.key]))} style={{ width: `${v[a.key] * 10}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: nothing to do */
        }
      }}
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      {done ? <Check className="size-4 text-beat-payoff" /> : <Copy className="size-4" />}
    </button>
  );
}

/** The post copy the AI wrote for a clip (title, hook, description, hashtags), each with a copy button. */
export function ClipCopy({ clip }: { clip: Clip }) {
  const tags = (clip.hashtags ?? []).join(" ");
  const rows: { label: string; text: string }[] = [
    { label: "Title", text: clip.title },
    { label: "Hook", text: clip.hook },
    { label: "Description", text: clip.description },
    { label: "Hashtags", text: tags },
  ].filter((r) => r.text);
  if (!rows.length) return null;
  return (
    <dl className="divide-y rounded-lg border bg-card">
      {rows.map((r) => (
        <div key={r.label} className="flex items-start gap-2 px-3 py-2">
          <div className="min-w-0 flex-1">
            <dt className="text-xs font-bold uppercase tracking-wide text-muted-foreground">{r.label}</dt>
            <dd className={cn("break-words text-sm", r.label === "Hashtags" && "text-primary")}>{r.text}</dd>
          </div>
          <CopyButton text={r.text} label={r.label.toLowerCase()} />
        </div>
      ))}
    </dl>
  );
}
