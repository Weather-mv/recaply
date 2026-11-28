import { BarChart3, ChevronRight, Clapperboard, Cpu, FolderKanban, ListTodo, Megaphone, Menu, Palette, Plus, Radio, Settings as Cog, Sparkles, Wand2, TriangleAlert, X, Zap } from "lucide-react";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { StatusDot } from "@/components/ui/viz";
import { TooltipProvider, Tip } from "@/components/ui/tooltip";
import { isActive, useBoot, useHealth, useProjects, useSystem } from "@/hooks/data";
import { cn } from "@/lib/utils";

export interface Crumb {
  label: string;
  to?: string;
}

const NAV = [
  { to: "/", label: "Studio", icon: Sparkles, end: true },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/editor", label: "Editor", icon: Wand2 },
  { to: "/live", label: "Live Streams", icon: Radio },
  { to: "/social", label: "Social", icon: Megaphone },
  { to: "/queue", label: "Queue", icon: ListTodo },
  { to: "/brand", label: "Brand Kit", icon: Palette },
  { to: "/usage", label: "Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Cog },
];

const ROOT_CRUMB: Record<string, string> = { "/": "Studio", "/projects": "Projects", "/editor": "Editor", "/live": "Live Streams", "/settings": "Settings", "/queue": "Queue", "/social": "Social", "/usage": "Usage", "/brand": "Brand Kit" };

/** AI state for the sidebar and top bar: what answers, and whether the local model is up. */
function useAi() {
  const health = useHealth().data;
  if (!health) return { label: "Checking AI…", tone: "gray" as const, detail: "", provider: "" };
  const local = health.llm === "ollama";
  const ready = !local || (!!health.ollama?.running && !!health.ollama?.model_ready);
  const fall = health.llm_order && health.llm_order.length > 1 ? ` Falls back to ${health.llm_order.slice(1).join(", ")}.` : "";
  if (ready) {
    return {
      label: local ? "Ollama online" : `${health.llm} online`,
      tone: "green" as const,
      provider: local ? "Local AI" : health.llm,
      detail: local ? `Local AI ready (${health.ollama?.model}). Nothing leaves this PC.` : `AI provider: ${health.llm}.${fall}`,
    };
  }
  return {
    label: local ? "Ollama offline" : "AI offline",
    tone: "yellow" as const,
    provider: "Rules only",
    detail: `${health.ollama?.hint || "The AI isn't ready."} Clips are scored by rules until it is. Add a Groq or Gemini key in Settings.`,
  };
}

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2.5 px-2 text-[17px] font-bold tracking-tight">
      <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-primary to-violet-500 text-white shadow-[0_0_24px_-4px_hsl(var(--primary))]">
        <Clapperboard className="size-4" />
      </span>
      Clipforge
    </Link>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { session } = useBoot();
  const projects = useProjects().data ?? [];
  const running = projects.filter(isActive).length;
  const ai = useAi();
  const name = session?.user.name || "You";

  return (
    <div className="flex h-full flex-col gap-6 p-4">
      <Brand />
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map((n) => {
          const Icon = n.icon;
          const badge = n.label === "Queue" && running > 0 ? running : 0;
          const inner = (active: boolean) => (
            <>
              <Icon className={cn("size-[18px]", active ? "text-primary" : "text-muted-foreground group-hover:text-foreground")} />
              <span className="flex-1">{n.label}</span>
              {badge > 0 && <span className="rounded-full bg-primary/20 px-1.5 text-xs font-semibold text-primary tabular-nums">{badge}</span>}
            </>
          );
          const base = "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors";
          return (
            <NavLink
              key={n.label}
              to={n.to}
              end={"end" in n ? n.end : undefined}
              onClick={onNavigate}
              className={({ isActive: a }) => cn(base, a ? "bg-white/[.07] text-foreground" : "text-muted-foreground hover:bg-white/[.04] hover:text-foreground")}
            >
              {({ isActive: a }) => (
                <>
                  {a && <span className="absolute -left-4 top-2 h-5 w-[3px] rounded-r-full bg-primary" />}
                  {inner(a)}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3">
        <Tip text={ai.detail || ai.label}>
          <Link to="/settings" onClick={onNavigate} className="surface flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm">
            <StatusDot tone={ai.tone} pulse={ai.tone === "green"} />
            <span className="min-w-0 flex-1">
              <span className="block text-xs text-muted-foreground">AI status</span>
              <span className="block truncate font-medium">{ai.label}</span>
            </span>
            {ai.tone === "yellow" ? <TriangleAlert className="size-4 text-amber-400" /> : <Cpu className="size-4 text-muted-foreground" />}
          </Link>
        </Tip>
        <a href="/account" className="flex items-center gap-3 rounded-xl px-2 py-1.5 hover:bg-white/[.04]">
          <span className="grid size-8 place-items-center rounded-full bg-gradient-to-br from-primary/80 to-violet-500/80 text-sm font-bold">{name.slice(0, 1).toUpperCase()}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{name}</span>
            <span className="block truncate text-xs text-muted-foreground">{session?.plan.name || (session?.desktop ? "Local" : "Account")}</span>
          </span>
        </a>
      </div>
    </div>
  );
}

function Usage() {
  const { session } = useBoot();
  const sys = useSystem().data;
  const ai = useAi();
  const plan = session?.plan;
  const left = plan?.left;
  const limit = plan?.limit;
  return (
    <div className="hidden items-center gap-2 xl:flex">
      <Tip text={ai.detail || ai.label}>
        <span className={cn("inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium", ai.tone === "yellow" ? "border-amber-400/30 bg-amber-400/10 text-amber-300" : "text-muted-foreground")}>
          <Zap className="size-3.5" /> {ai.provider || "AI"}
        </span>
      </Tip>
      {sys?.gpu && (
        <Tip text={`${sys.gpu.name}: ${sys.gpu.used_gb} of ${sys.gpu.total_gb} GB memory in use`}>
          <span className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-muted-foreground tabular-nums">
            <StatusDot tone={sys.gpu.percent > 85 ? "yellow" : "green"} /> GPU {Math.round(sys.gpu.percent)}%
          </span>
        </Tip>
      )}
      {limit != null && left != null && (
        <Tip text="Clip exports left on your plan">
          <span className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-muted-foreground tabular-nums">
            {left} / {limit} left
          </span>
        </Tip>
      )}
    </div>
  );
}

function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1.5 text-sm">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <Fragment key={i}>
              {i > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/60" />}
              <li className={cn("min-w-0 truncate", last ? "font-semibold text-foreground" : "text-muted-foreground")} aria-current={last ? "page" : undefined}>
                {c.to && !last ? <Link to={c.to} className="hover:text-foreground">{c.label}</Link> : c.label}
              </li>
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * The persistent dashboard shell: fixed sidebar (logo, navigation, AI status, profile), a top bar with
 * breadcrumbs, usage chips and "New project", and a scrolling, width-limited content area.
 * `wide` lets the Studio editor use the full width.
 */
export function AppShell({ children, wide, crumbs }: { children: ReactNode; wide?: boolean; crumbs?: Crumb[] }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [loc.pathname]);

  const trail: Crumb[] = crumbs ?? [{ label: ROOT_CRUMB[loc.pathname] ?? "Studio" }];

  return (
    <TooltipProvider>
      <div className="min-h-screen bg-background">
        <div className="pointer-events-none fixed inset-x-0 top-0 -z-0 h-72 bg-grid-fade" aria-hidden="true" />

        <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 border-r bg-background/80 backdrop-blur-xl lg:block">
          <Sidebar />
        </aside>

        {open && (
          <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
            <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
            <aside className="absolute inset-y-0 left-0 w-64 border-r bg-background">
              <button type="button" aria-label="Close menu" onClick={() => setOpen(false)} className="absolute right-3 top-4 grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-white/10">
                <X className="size-4" />
              </button>
              <Sidebar onNavigate={() => setOpen(false)} />
            </aside>
          </div>
        )}

        <div className="relative lg:pl-60">
          <header className="glass sticky top-0 z-30 flex h-14 items-center gap-3 border-b px-4 sm:px-6">
            <button type="button" aria-label="Open menu" onClick={() => setOpen(true)} className="grid size-9 place-items-center rounded-md text-muted-foreground hover:bg-white/10 lg:hidden">
              <Menu className="size-5" />
            </button>
            <Breadcrumbs crumbs={trail} />
            <div className="ml-auto flex items-center gap-2">
              <Usage />
              <button
                type="button"
                onClick={() => nav("/", { state: { focus: Date.now() } })}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3.5 text-sm font-semibold text-primary-foreground shadow-[0_0_24px_-6px_hsl(var(--primary))] transition-colors hover:bg-primary/90"
              >
                <Plus className="size-4" /> <span className="hidden sm:inline">New project</span>
              </button>
            </div>
          </header>
          <main className={cn("relative mx-auto px-4 py-6 sm:px-6 lg:py-8", wide ? "max-w-[1800px]" : "max-w-6xl")}>{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
