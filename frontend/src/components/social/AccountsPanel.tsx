import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { NET_LOOK, type NetworkInfo, type SocialAccount } from "@/lib/social";
import { cn } from "@/lib/utils";

export function Avatar({ acc, size = 36 }: { acc: Pick<SocialAccount, "avatar" | "name" | "network">; size?: number }) {
  const [broken, setBroken] = useState(false);
  const look = NET_LOOK[acc.network];
  const Icon = look.icon;
  return (
    <span className="relative inline-block shrink-0" style={{ width: size, height: size }}>
      {acc.avatar && !broken ? (
        <img src={acc.avatar} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} className="size-full rounded-full object-cover" />
      ) : (
        <span className="grid size-full place-items-center rounded-full bg-secondary text-sm font-semibold">{(acc.name || "?").slice(0, 1).toUpperCase()}</span>
      )}
      <span className="absolute -bottom-0.5 -right-0.5 grid size-[18px] place-items-center rounded-full text-white ring-2 ring-card" style={{ background: look.color }}>
        <Icon className="size-[11px]" />
      </span>
    </span>
  );
}

interface Flow {
  network: string;
  state: string;
  url: string;
}

/** Connect / reconnect / disconnect accounts, one card per network. */
export function AccountsPanel({ networks, accounts, reload }: { networks: NetworkInfo[]; accounts: SocialAccount[]; reload: () => void }) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<number>();

  useEffect(() => () => window.clearInterval(timer.current), []);

  const connect = async (net: NetworkInfo) => {
    setMsg(null);
    try {
      const r = await api<{ state: string; url: string; opened: boolean }>(`/api/social/connect/${net.key}`, { method: "POST" });
      if (!r.opened) window.open(r.url, "_blank", "noopener");
      setFlow({ network: net.key, state: r.state, url: r.url });
      window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        try {
          const st = await api<{ status: string; error?: string; account?: SocialAccount }>(`/api/social/connect/status/${r.state}`);
          if (st.status === "done" || st.status === "failed") {
            window.clearInterval(timer.current);
            setFlow(null);
            setMsg(st.status === "done" ? { ok: true, text: `${st.account?.name ?? "Account"} is connected.` } : { ok: false, text: st.error || "Sign-in failed." });
            reload();
          }
        } catch {
          window.clearInterval(timer.current);
          setFlow(null);
          setMsg({ ok: false, text: "The sign-in expired. Try again." });
        }
      }, 1500);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Couldn't start the sign-in." });
    }
  };

  const disconnect = async (a: SocialAccount) => {
    if (!window.confirm(`Disconnect ${a.name}? Scheduled posts to it will fail until you connect it again.`)) return;
    try {
      await api(`/api/social/accounts/${a.id}`, { method: "DELETE" });
    } finally {
      reload();
    }
  };

  return (
    <div className="space-y-5">
      {msg && (
        <div role="status" className={cn("flex items-start gap-2 rounded-xl px-4 py-3 text-[15px] ring-1 ring-inset", msg.ok ? "bg-emerald-500/10 ring-emerald-500/25" : "bg-red-500/10 ring-red-500/25")}>
          {msg.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-500" />}
          {msg.text}
        </div>
      )}
      {flow && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl bg-primary/10 px-4 py-3 text-[15px] ring-1 ring-inset ring-primary/25">
          <Loader2 className="size-4 animate-spin text-primary" />
          <span className="flex-1">Finish signing in to {NET_LOOK[flow.network as keyof typeof NET_LOOK]?.label} in your browser…</span>
          <a href={flow.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
            Open the sign-in page <ExternalLink className="size-3.5" />
          </a>
          <Button size="sm" variant="ghost" onClick={() => { window.clearInterval(timer.current); setFlow(null); }}>Cancel</Button>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {networks.map((n) => {
          const look = NET_LOOK[n.key];
          const Icon = look.icon;
          const mine = accounts.filter((a) => a.network === n.key);
          return (
            <section key={n.key} className="surface flex flex-col rounded-xl p-5">
              <header className="flex items-center gap-3">
                <span className="grid size-10 place-items-center rounded-xl text-white" style={{ background: look.color }}><Icon className="size-5" /></span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-base font-semibold">{n.label}</h3>
                  <p className="text-sm text-muted-foreground">{n.note}</p>
                </div>
              </header>
              <ul className="mt-4 space-y-2">
                {mine.map((a) => (
                  <li key={a.id} className="flex items-center gap-3 rounded-lg bg-secondary/60 px-3 py-2">
                    <Avatar acc={a} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[15px] font-medium">{a.name}</p>
                      <p className={cn("truncate text-sm", a.status === "reconnect" ? "text-red-500" : "text-muted-foreground")}>
                        {a.status === "reconnect" ? a.error || "Needs to sign in again" : a.handle || "Connected"}
                      </p>
                    </div>
                    {a.status === "reconnect" && (
                      <Button size="sm" variant="secondary" onClick={() => connect(n)}><RefreshCw /> Reconnect</Button>
                    )}
                    <Button size="sm" variant="ghost" aria-label={`Disconnect ${a.name}`} className="px-2 text-muted-foreground hover:text-destructive" onClick={() => disconnect(a)}>
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
              <div className="mt-auto pt-4">
                {n.configured ? (
                  <Button variant={mine.length ? "secondary" : "default"} disabled={!!flow} onClick={() => connect(n)}>
                    <Plus /> {mine.length ? "Connect another" : `Connect ${look.label}`}
                  </Button>
                ) : (
                  <div className="rounded-lg border border-dashed px-3 py-2.5 text-sm text-muted-foreground">
                    <p className="font-medium text-foreground">Setup needed</p>
                    <p className="mt-1">Create a developer app, then add <code className="rounded bg-secondary px-1">{n.env}</code> to the .env file and restart Clipforge.</p>
                    <p className="mt-1 break-all">Redirect URL: <code className="rounded bg-secondary px-1">{n.redirect_uri}</code></p>
                    <a href={n.setup_url} target="_blank" rel="noopener noreferrer" className="mt-1.5 inline-flex items-center gap-1 font-medium text-primary hover:underline">
                      Open the developer portal <ExternalLink className="size-3.5" />
                    </a>
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
