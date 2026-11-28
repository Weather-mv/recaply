import { CalendarDays, CheckCircle2, FileText, Link2, PenSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { AccountsPanel } from "@/components/social/AccountsPanel";
import { CalendarView, PostDetails, type CalMode } from "@/components/social/CalendarView";
import { Composer, type ComposerSeed } from "@/components/social/Composer";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { NET_LOOK, STATUS_LABEL, type NetworkInfo, type NetworkKey, type SocialAccount, type SocialPost } from "@/lib/social";
import { cn } from "@/lib/utils";

function useSocial() {
  const [networks, setNetworks] = useState<NetworkInfo[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [posts, setPosts] = useState<SocialPost[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(async () => {
    try {
      const [n, a, p] = await Promise.all([
        api<{ networks: NetworkInfo[] }>("/api/social/networks"),
        api<{ accounts: SocialAccount[] }>("/api/social/accounts"),
        api<{ posts: SocialPost[] }>("/api/social/posts"),
      ]);
      setNetworks(n.networks);
      setAccounts(a.accounts);
      setPosts(p.posts);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach Clipforge.");
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => {
    void load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [load]);
  return { networks, accounts, posts, error, loaded, load };
}

/** The Social hub: a content calendar, connected accounts, and the post composer. */
export default function Social() {
  const loc = useLocation();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const { networks, accounts, posts, error, loaded, load } = useSocial();
  const [cursor, setCursor] = useState(new Date());
  const [mode, setMode] = useState<CalMode>(() => (window.innerWidth < 768 ? "week" : "month"));
  const [filter, setFilter] = useState<NetworkKey | "all">("all");
  const [seed, setSeed] = useState<ComposerSeed | null>(null);
  const [open, setOpen] = useState<SocialPost | null>(null);
  const [toast, setToast] = useState("");
  const tab = loc.pathname.endsWith("/accounts") ? "accounts" : "calendar";

  // /social/compose?job=…&clip=…&file=… opens the composer (from the Director's export panel)
  useEffect(() => {
    if (loc.pathname.endsWith("/compose")) {
      const clip = params.get("clip");
      setSeed({ job: params.get("job") ?? undefined, clip: clip ? Number(clip) : undefined, file: params.get("file") ?? undefined });
      nav("/social", { replace: true });
    }
  }, [loc.pathname, params, nav]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(t);
  }, [toast]);

  const shown = useMemo(() => (filter === "all" ? posts : posts.filter((p) => p.targets.some((t) => t.platform === filter))), [posts, filter]);
  const drafts = shown.filter((p) => p.status === "draft");
  const upcoming = shown.filter((p) => p.status === "queued" && p.run_at > Date.now() / 1000).length;
  const failed = shown.filter((p) => p.status === "failed" || p.status === "partial").length;

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      setToast(ok);
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "That didn't work.");
    } finally {
      void load();
    }
  };

  const move = (p: SocialPost, runAt: number) =>
    act(() => api(`/api/social/posts/${p.group}`, { method: "PATCH", json: { when: runAt } }), `Moved to ${new Date(runAt * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}.`);

  return (
    <AppShell crumbs={[{ label: "Social" }]} wide>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">Social</h1>
          <p className="mt-1 text-[15px] text-muted-foreground">Plan, write and schedule posts to your connected accounts.</p>
        </div>
        <Button size="lg" onClick={() => setSeed({})}><PenSquare /> New post</Button>
      </div>

      <nav className="mt-5 flex gap-1 border-b" aria-label="Social sections">
        {[{ to: "/social", label: "Calendar", icon: CalendarDays, end: true }, { to: "/social/accounts", label: `Accounts${accounts.length ? ` (${accounts.length})` : ""}`, icon: Link2 }].map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}
            className={({ isActive }) => cn("-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-[15px]", isActive ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <t.icon className="size-4" /> {t.label}
          </NavLink>
        ))}
      </nav>

      {error && <p className="mt-4 rounded-lg bg-red-500/10 px-4 py-3 text-red-600 dark:text-red-400">{error}</p>}

      {tab === "accounts" ? (
        <div className="mt-5"><AccountsPanel networks={networks} accounts={accounts} reload={load} /></div>
      ) : (
        <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_280px]">
          <div className="min-w-0 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex gap-1 rounded-lg bg-secondary p-1 text-sm">
                {(["month", "week"] as const).map((m) => (
                  <button key={m} onClick={() => setMode(m)} className={cn("rounded-md px-3 py-1.5 font-medium capitalize", mode === m ? "bg-card shadow-sm" : "text-muted-foreground")}>{m}</button>
                ))}
              </div>
              <div className="flex flex-wrap gap-1">
                {(["all", "youtube", "linkedin", "x", "instagram"] as const).map((k) => {
                  const I = k === "all" ? null : NET_LOOK[k].icon;
                  return (
                    <button key={k} onClick={() => setFilter(k)} aria-pressed={filter === k}
                      className={cn("flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm ring-1 ring-inset", filter === k ? "bg-primary/10 ring-primary" : "ring-border text-muted-foreground hover:text-foreground")}>
                      {I && <I className="size-3.5" style={{ color: NET_LOOK[k as NetworkKey].color }} />} {k === "all" ? "All" : NET_LOOK[k].label}
                    </button>
                  );
                })}
              </div>
              <p className="ml-auto text-sm text-muted-foreground">{upcoming} scheduled{failed ? ` · ${failed} need attention` : ""}</p>
            </div>
            <CalendarView posts={shown} cursor={cursor} setCursor={setCursor} mode={mode} onOpen={setOpen} onNew={(day) => setSeed({ day })} onMove={move} />
            {loaded && accounts.length === 0 && (
              <p className="rounded-lg bg-primary/10 px-4 py-3 text-[15px]">
                Start by connecting an account on the <NavLink to="/social/accounts" className="font-medium text-primary hover:underline">Accounts</NavLink> tab.
              </p>
            )}
          </div>
          <aside className="space-y-3">
            <h2 className="flex items-center gap-2 font-semibold"><FileText className="size-4" /> Drafts ({drafts.length})</h2>
            {drafts.length === 0 && <p className="text-sm text-muted-foreground">Drafts you save appear here. Open one to schedule it.</p>}
            {drafts.map((p) => (
              <button key={p.group} onClick={() => setOpen(p)} className="surface w-full rounded-xl p-3 text-left hover:ring-1 hover:ring-primary">
                <p className="line-clamp-2 text-[15px] font-medium">{p.title}</p>
                <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground">
                  {[...new Set(p.targets.map((t) => t.platform))].map((n) => { const I = NET_LOOK[n].icon; return <I key={n} className="size-3.5" style={{ color: NET_LOOK[n].color }} />; })}
                  {STATUS_LABEL[p.status]}
                </p>
              </button>
            ))}
          </aside>
        </div>
      )}

      {open && (
        <PostDetails post={open} onClose={() => setOpen(null)}
          onEdit={() => { setSeed({ post: open }); setOpen(null); }}
          onRetry={() => { void act(() => api(`/api/social/posts/${open.group}/retry`, { method: "POST" }), "Trying again."); setOpen(null); }}
          onDelete={() => { if (window.confirm("Delete this post? Posts already sent stay on the networks.")) { void act(() => api(`/api/social/posts/${open.group}`, { method: "DELETE" }), "Deleted."); setOpen(null); } }} />
      )}
      {seed && (
        <Composer seed={seed} networks={networks} accounts={accounts} onClose={() => setSeed(null)}
          onSaved={(text) => { setSeed(null); setToast(text); void load(); }} />
      )}
      {toast && (
        <div role="status" className="fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-2 rounded-full bg-foreground px-4 py-2.5 text-[15px] text-background shadow-lg">
          <CheckCircle2 className="size-4" /> {toast}
        </div>
      )}
    </AppShell>
  );
}
