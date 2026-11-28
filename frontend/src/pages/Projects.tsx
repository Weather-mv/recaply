import { Clapperboard, LayoutGrid, Plus, Rows3, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { SystemWidget } from "@/components/layout/SystemWidget";
import { ProjectCard, ProjectRow } from "@/components/project/ProjectViews";
import { isActive, useProjects } from "@/hooks/data";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "processing" | "done" | "failed";
const FILTERS: { key: Filter; label: string; test: (p: Project) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  { key: "processing", label: "Processing", test: isActive },
  { key: "done", label: "Done", test: (p) => p.state === "ready" },
  { key: "failed", label: "Failed", test: (p) => p.state === "failed" || p.state === "cancelled" },
];

function readView(): "table" | "grid" {
  try {
    return localStorage.getItem("cf_projects_view") === "grid" ? "grid" : "table";
  } catch {
    return "table";
  }
}

export default function Projects() {
  const { data: projects, loading } = useProjects();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<"table" | "grid">(readView);
  const choose = (v: "table" | "grid") => {
    setView(v);
    try {
      localStorage.setItem("cf_projects_view", v);
    } catch {
      /* private mode */
    }
  };

  const all = projects ?? [];
  const rows = useMemo(() => {
    const t = FILTERS.find((f) => f.key === filter)!.test;
    const needle = q.trim().toLowerCase();
    return all.filter((p) => t(p) && (!needle || (p.title || "").toLowerCase().includes(needle) || (p.profile?.label ?? "").toLowerCase().includes(needle)));
  }, [all, q, filter]);

  return (
    <AppShell>
      <div className="grid grid-cols-1 items-start gap-8 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">Projects</h1>
              <p className="mt-1 text-muted-foreground">{all.length ? `${all.length} video${all.length === 1 ? "" : "s"} processed` : "Everything you've clipped lives here."}</p>
            </div>
            <Link to="/" className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90"><Plus className="size-4" /> New project</Link>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects" aria-label="Search projects" className="h-9 w-full rounded-lg border border-input bg-white/[.03] pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground/70 focus-visible:border-primary/60" />
            </div>
            <div role="tablist" aria-label="Filter" className="flex rounded-lg border bg-white/[.03] p-0.5">
              {FILTERS.map((f) => (
                <button key={f.key} role="tab" aria-selected={filter === f.key} type="button" onClick={() => setFilter(f.key)} className={cn("rounded-md px-3 py-1 text-sm font-medium transition-colors", filter === f.key ? "bg-white/[.09] text-foreground" : "text-muted-foreground hover:text-foreground")}>
                  {f.label}
                </button>
              ))}
            </div>
            <div className="ml-auto flex rounded-lg border bg-white/[.03] p-0.5" role="group" aria-label="View">
              <button type="button" aria-label="Table view" aria-pressed={view === "table"} onClick={() => choose("table")} className={cn("grid size-8 place-items-center rounded-md", view === "table" ? "bg-white/[.09]" : "text-muted-foreground hover:text-foreground")}><Rows3 className="size-4" /></button>
              <button type="button" aria-label="Grid view" aria-pressed={view === "grid"} onClick={() => choose("grid")} className={cn("grid size-8 place-items-center rounded-md", view === "grid" ? "bg-white/[.09]" : "text-muted-foreground hover:text-foreground")}><LayoutGrid className="size-4" /></button>
            </div>
          </div>

          {loading && !projects ? (
            <div className="space-y-2">{[0, 1, 2, 3].map((i) => <div key={i} className="h-16 animate-pulse rounded-xl bg-white/[.04]" />)}</div>
          ) : !rows.length ? (
            <div className="surface grid place-items-center gap-3 rounded-2xl p-12 text-center">
              <span className="grid size-12 place-items-center rounded-2xl bg-primary/15"><Clapperboard className="size-6 text-primary" /></span>
              <p className="text-lg font-semibold">{all.length ? "Nothing matches" : "No projects yet"}</p>
              <p className="max-w-sm text-sm text-muted-foreground">{all.length ? "Try another search or filter." : "Paste a link in the Studio and your first clips show up here."}</p>
            </div>
          ) : view === "grid" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{rows.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
          ) : (
            <div className="surface overflow-hidden rounded-2xl">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-white/10 text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="py-2.5 pl-4 pr-3 font-medium">Video</th>
                    <th className="px-3 font-medium">Status</th>
                    <th className="hidden px-3 font-medium md:table-cell">Progress</th>
                    <th className="hidden px-3 font-medium lg:table-cell">Best</th>
                    <th className="hidden px-3 font-medium sm:table-cell">Created</th>
                    <th className="w-24" />
                  </tr>
                </thead>
                <tbody>{rows.map((p) => <ProjectRow key={p.id} p={p} />)}</tbody>
              </table>
            </div>
          )}
        </div>
        <aside className="space-y-4"><SystemWidget /></aside>
      </div>
    </AppShell>
  );
}
