import { AlertCircle, Check, ChevronDown, Loader2, MinusCircle, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api, ApiError } from "@/lib/api";
import { normalizeStages, overallProgress, seconds, STAGE_LABEL, visibleStages } from "@/lib/stages";
import type { JobLog, PipelineStage } from "@/lib/types";
import { cn } from "@/lib/utils";

function Dot({ s }: { s: PipelineStage }) {
  const base = "grid size-7 shrink-0 place-items-center rounded-full border-2 text-xs font-bold";
  if (s.state === "done") return <span className={cn(base, "border-beat-payoff bg-beat-payoff text-white")}><Check className="size-4" /></span>;
  if (s.state === "running") return <span className={cn(base, "border-primary text-primary")}><Loader2 className="size-4 animate-spin" /></span>;
  if (s.state === "failed") return <span className={cn(base, "border-destructive bg-destructive text-white")}><AlertCircle className="size-4" /></span>;
  if (s.state === "skipped") return <span className={cn(base, "border-border text-muted-foreground")}><MinusCircle className="size-4" /></span>;
  return <span className={cn(base, "border-border text-muted-foreground")} />;
}

/** Downloading → Analyzing → Transcribing → Scoring (→ Rendering): where the job is, with a bar inside the running step. */
export function PipelineStepper({ stages, compact, hideBar, className }: { stages: PipelineStage[] | undefined; compact?: boolean; hideBar?: boolean; className?: string }) {
  const steps = visibleStages(normalizeStages(stages));
  if (compact) {
    return (
      <ol className={cn("flex items-center gap-1", className)} aria-label="Progress">
        {steps.map((s) => (
          <li
            key={s.key}
            title={`${STAGE_LABEL[s.key]}: ${s.state}`}
            className={cn(
              "h-1.5 flex-1 rounded-full",
              s.state === "done" || s.state === "skipped" ? "bg-beat-payoff" : s.state === "running" ? "bg-primary" : s.state === "failed" ? "bg-destructive" : "bg-secondary",
              s.state === "running" && "animate-pulse",
            )}
          />
        ))}
      </ol>
    );
  }
  return (
    <div className={cn("space-y-4", className)}>
      <ol className="flex items-start" aria-label="Progress">
        {steps.map((s, i) => (
          <li key={s.key} className="relative flex flex-1 flex-col items-center gap-2 text-center" aria-current={s.state === "running" ? "step" : undefined}>
            {i > 0 && (
              <span
                aria-hidden
                className={cn("absolute right-1/2 top-3.5 h-0.5 w-full -translate-y-1/2", steps[i - 1].state === "done" || steps[i - 1].state === "skipped" ? "bg-beat-payoff" : "bg-border")}
              />
            )}
            <span className="relative z-10 rounded-full bg-card"><Dot s={s} /></span>
            <span className={cn("text-sm font-semibold", s.state === "pending" ? "text-muted-foreground" : s.state === "failed" ? "text-destructive" : "text-foreground")}>{STAGE_LABEL[s.key]}</span>
            {s.state === "running" && <span className="text-xs tabular text-muted-foreground">{Math.round(s.progress * 100)}%</span>}
            {s.state === "done" && seconds(s) != null && <span className="text-xs tabular text-muted-foreground">{Math.round(seconds(s)!)}s</span>}
            {s.state === "skipped" && <span className="text-xs text-muted-foreground">cached</span>}
          </li>
        ))}
      </ol>
      {!hideBar && <Progress value={overallProgress(steps)} indeterminate={overallProgress(steps) < 0.02} />}
    </div>
  );
}

/** Failed at <step>: why, the job log, and a retry (the finished steps are cached, so a retry resumes where it stopped). */
export function FailurePanel({ jobId, error, stages, onRetry, className }: { jobId: string; error: string | null; stages: PipelineStage[] | undefined; onRetry: () => void; className?: string }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState<JobLog | null>(null);
  const [logError, setLogError] = useState("");
  const failed = normalizeStages(stages).find((s) => s.state === "failed");

  useEffect(() => {
    if (!open || log) return;
    api<JobLog>(`/api/clipjob/${jobId}/log`)
      .then(setLog)
      .catch((e) => setLogError(e instanceof ApiError ? e.message : "Couldn't load the log."));
  }, [open, log, jobId]);

  const retry = async () => {
    setBusy(true);
    try {
      await api(`/api/clipjob/${jobId}/retry`, { method: "POST" });
      onRetry();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="alert" className={cn("space-y-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-left", className)}>
      <p className="flex items-start gap-2 font-bold text-destructive">
        <AlertCircle className="mt-0.5 size-5 shrink-0" />
        {failed ? `Failed while ${STAGE_LABEL[failed.key].toLowerCase()}` : "This project didn't finish"}
      </p>
      <p className="text-sm">{failed?.error || error || "Something went wrong."}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={retry} disabled={busy}>
          {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />} Retry{failed ? ` from ${STAGE_LABEL[failed.key].toLowerCase()}` : ""}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <ChevronDown className={cn("transition-transform", open && "rotate-180")} /> {open ? "Hide log" : "View log"}
        </Button>
      </div>
      {open && (
        <div className="max-h-64 overflow-y-auto rounded-md border bg-card p-3 text-sm">
          {logError ? (
            <p className="text-destructive">{logError}</p>
          ) : !log ? (
            <Loader2 className="size-4 animate-spin" />
          ) : log.log.length === 0 ? (
            <p className="text-muted-foreground">Nothing was logged for this run.</p>
          ) : (
            <ul className="space-y-1.5">
              {log.log.map((e, i) => (
                <li key={i} className="flex gap-2">
                  <span className="w-20 shrink-0 tabular text-muted-foreground">{new Date(e.t * 1000).toLocaleTimeString()}</span>
                  <span className={cn("w-24 shrink-0 font-semibold", e.level === "error" ? "text-destructive" : e.level === "warning" ? "text-beat-build" : "text-muted-foreground")}>
                    {e.stage ? STAGE_LABEL[e.stage] : "Job"}
                  </span>
                  <span className="min-w-0 break-words">{e.msg}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
