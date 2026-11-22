import { AlertCircle, AudioLines, CheckCircle2, Clapperboard, ExternalLink, FileVideo, Loader2, Pencil, Trophy } from "lucide-react";
import { Link } from "react-router-dom";
import { PipelineStepper } from "@/components/project/PipelineStepper";
import { Tip } from "@/components/ui/tooltip";
import { isActive } from "@/hooks/data";
import { thumbUrl } from "@/lib/api";
import { STAGE_LABEL } from "@/lib/stages";
import type { Project } from "@/lib/types";
import { ago, cn, fmtTime } from "@/lib/utils";

/** Done (green), Processing (yellow with a spinner), Failed, Cancelled. */
export function StatusBadge({ p, className }: { p: Pick<Project, "state">; className?: string }) {
  const base = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset";
  if (p.state === "ready") return <span className={cn(base, "bg-emerald-500/10 text-emerald-400 ring-emerald-400/20", className)}><CheckCircle2 className="size-3.5" /> Done</span>;
  if (p.state === "failed") return <span className={cn(base, "bg-red-500/10 text-red-400 ring-red-400/20", className)}><AlertCircle className="size-3.5" /> Failed</span>;
  if (p.state === "cancelled") return <span className={cn(base, "bg-zinc-500/10 text-zinc-400 ring-zinc-400/20", className)}>Cancelled</span>;
  return (
    <span className={cn(base, "bg-amber-400/10 text-amber-300 ring-amber-300/20", className)}>
      <Loader2 className="size-3.5 animate-spin" /> {p.state === "queued" ? "Queued" : "Processing"}
    </span>
  );
}

export function Thumb({ p, className }: { p: Project; className?: string }) {
  const src = thumbUrl(p.thumb, p.extractor);
  return (
    <div className={cn("relative overflow-hidden bg-gradient-to-br from-primary/25 via-zinc-900 to-zinc-950", className)}>
      {src ? (
        <img src={src} alt="" loading="lazy" className="size-full object-cover" />
      ) : (
        <div className="grid size-full place-items-center text-white/25">{p.extractor ? <Clapperboard className="size-8" /> : <FileVideo className="size-8" />}</div>
      )}
      {p.duration ? <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white">{fmtTime(p.duration)}</span> : null}
    </div>
  );
}

/** Hover-revealed quick actions: open the Studio. Edit opens the same view (the editor lives there). */
function QuickActions({ p }: { p: Project }) {
  if (p.state !== "ready") return null;
  const cls = "grid size-8 place-items-center rounded-lg bg-black/70 text-white backdrop-blur transition-colors hover:bg-primary";
  return (
    <div className="absolute right-2 top-2 flex gap-1.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
      <Tip text="Open in Studio"><Link to={`/p/${p.id}`} aria-label={`Open ${p.title || "project"} in Studio`} className={cls}><ExternalLink className="size-4" /></Link></Tip>
      <Tip text="Edit clips"><Link to={`/p/${p.id}`} aria-label={`Edit clips of ${p.title || "project"}`} className={cls}><Pencil className="size-4" /></Link></Tip>
    </div>
  );
}

export function ProjectCard({ p }: { p: Project }) {
  const active = isActive(p);
  return (
    <div className="group relative">
      <Link to={`/p/${p.id}`} className="surface surface-hover block overflow-hidden rounded-xl focus-visible:ring-2 focus-visible:ring-ring">
        <div className="relative">
          <Thumb p={p} className="aspect-video" />
          <span className="absolute left-2 top-2"><StatusBadge p={p} className="bg-black/60 backdrop-blur" /></span>
        </div>
        <div className="space-y-2.5 p-3.5">
          <h3 className="line-clamp-2 min-h-[2.5rem] text-sm font-semibold leading-snug">{p.title || "Untitled"}</h3>
          {active ? (
            <div className="space-y-1.5">
              <PipelineStepper stages={p.stages} compact />
              <p className="truncate text-xs text-muted-foreground">{p.stage || "Starting"}</p>
            </div>
          ) : p.state === "failed" ? (
            <div className="space-y-0.5">
              <p className="text-xs font-semibold text-red-400">{p.failed_stage ? `Failed while ${STAGE_LABEL[p.failed_stage].toLowerCase()}` : "Failed"}</p>
              <p className="line-clamp-2 text-xs text-red-400/80">{p.error || "Something went wrong."}</p>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1"><Clapperboard className="size-3.5" /> {p.clips} clips</span>
              <span className="inline-flex items-center gap-1"><AudioLines className="size-3.5" /> {p.beats} beats</span>
              {p.best != null && <span className="inline-flex items-center gap-1 font-semibold text-emerald-400"><Trophy className="size-3.5" /> {Math.round(p.best)}</span>}
            </div>
          )}
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="max-w-[65%] truncate">{p.profile?.label ?? (active ? "Detecting…" : "")}</span>
            <span>{ago(p.created)}</span>
          </div>
        </div>
      </Link>
      <QuickActions p={p} />
    </div>
  );
}

/** One row of the data table. */
export function ProjectRow({ p }: { p: Project }) {
  const active = isActive(p);
  return (
    <tr className="group relative border-b border-white/[.06] last:border-0 hover:bg-white/[.03]">
      <td className="w-[44%] min-w-[15rem] py-2.5 pl-4 pr-3">
        <Link to={`/p/${p.id}`} className="flex items-center gap-3 focus-visible:outline-none">
          <Thumb p={p} className="aspect-video w-28 shrink-0 rounded-md" />
          <span className="min-w-0 flex-1">
            <span className="line-clamp-1 font-medium">{p.title || "Untitled"}</span>
            <span className="line-clamp-1 text-xs text-muted-foreground">{p.profile?.label ?? (active ? "Detecting content type…" : "")}</span>
          </span>
        </Link>
      </td>
      <td className="px-3"><StatusBadge p={p} /></td>
      <td className="hidden px-3 md:table-cell">
        {active ? (
          <div className="w-40"><PipelineStepper stages={p.stages} compact /></div>
        ) : p.state === "failed" ? (
          <span className="line-clamp-1 max-w-[16rem] text-xs text-red-400/90">{p.error || "Something went wrong."}</span>
        ) : (
          <span className="text-sm tabular-nums text-muted-foreground">{p.clips} clips · {p.beats} beats</span>
        )}
      </td>
      <td className="hidden px-3 text-sm font-semibold tabular-nums text-emerald-400 lg:table-cell">{p.best != null && p.state === "ready" ? Math.round(p.best) : ""}</td>
      <td className="hidden whitespace-nowrap px-3 text-sm text-muted-foreground sm:table-cell">{ago(p.created)}</td>
      <td className="w-24 pr-4 text-right">
        {p.state === "ready" && (
          <span className="inline-flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
            <Tip text="Open in Studio"><Link to={`/p/${p.id}`} aria-label={`Open ${p.title || "project"}`} className="grid size-8 place-items-center rounded-lg hover:bg-white/10"><ExternalLink className="size-4" /></Link></Tip>
            <Tip text="Edit clips"><Link to={`/p/${p.id}`} aria-label={`Edit ${p.title || "project"}`} className="grid size-8 place-items-center rounded-lg hover:bg-white/10"><Pencil className="size-4" /></Link></Tip>
          </span>
        )}
      </td>
    </tr>
  );
}
