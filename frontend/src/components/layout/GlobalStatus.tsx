import { Cpu, Loader2, ScanSearch, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Tip } from "@/components/ui/tooltip";
import { describeActivity } from "@/lib/status";
import type { Health, Project } from "@/lib/types";
import { cn } from "@/lib/utils";

export function GlobalStatus({ projects, health }: { projects: Project[]; health: Health | null }) {
  const act = describeActivity(projects);
  const ai = health?.ollama;
  const usingOllama = health?.llm === "ollama";
  const aiReady = !usingOllama || (!!ai?.running && !!ai?.model_ready);

  const pill = (
    <span
      className={cn(
        "inline-flex h-10 max-w-[19rem] items-center gap-2 rounded-full border px-3.5 text-sm font-semibold",
        act.kind === "idle" ? "text-muted-foreground" : "border-primary/40 bg-primary/10 text-foreground",
      )}
    >
      {act.kind === "detecting" ? <ScanSearch className="size-4 shrink-0 text-primary" /> : act.kind === "working" ? <Loader2 className="size-4 shrink-0 animate-spin text-primary" /> : <span className="size-2 shrink-0 rounded-full bg-beat-payoff" />}
      <span className="truncate" aria-live="polite">
        {act.text}
      </span>
    </span>
  );

  return (
    <div className="hidden items-center gap-2 sm:flex">
      {act.id ? (
        <Link to={`/p/${act.id}`} aria-label="Open the project that is running">
          {pill}
        </Link>
      ) : (
        pill
      )}
      {health && (
        <Tip
          text={
            aiReady
              ? usingOllama
                ? `Local AI ready (${ai?.model}). Nothing leaves this PC.`
                : `AI provider: ${health.llm}`
              : ai?.hint || "The AI isn't ready. Clips are scored by rules until it is."
          }
        >
          <span
            className={cn(
              "inline-flex h-10 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-sm font-semibold",
              aiReady ? "text-muted-foreground" : "border-beat-build/50 bg-beat-build/10 text-beat-build",
            )}
          >
            {aiReady ? <Cpu className="size-4" /> : <TriangleAlert className="size-4" />}
            {aiReady ? (usingOllama ? "Local AI" : health.llm) : "AI offline"}
          </span>
        </Tip>
      )}
    </div>
  );
}
