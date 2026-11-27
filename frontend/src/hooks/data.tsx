import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { isActive } from "@/lib/status";
import type { Capabilities, Health, Job, LiveReply, Project, Session, SystemStats } from "@/lib/types";

/** Poll `load` every `ms` (and when the tab comes back); `ms` may change with the data (faster while jobs run). */
function usePolling<T>(load: (signal: AbortSignal) => Promise<T>, ms: (data: T | null) => number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  const msRef = useRef(ms);
  loadRef.current = load;
  msRef.current = ms;
  const timer = useRef<number>();
  const ctl = useRef<AbortController>();

  const run = useCallback(async () => {
    window.clearTimeout(timer.current);
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    let next: T | null = null;
    try {
      next = await loadRef.current(c.signal);
      setData(next);
      setError(null);
    } catch (e) {
      if (c.signal.aborted) return;
      setError(e instanceof ApiError ? e : new ApiError(String(e), 0));
    } finally {
      if (!c.signal.aborted) {
        setLoading(false);
        timer.current = window.setTimeout(run, msRef.current(next));
      }
    }
  }, []);

  useEffect(() => {
    run();
    const visible = () => document.visibilityState === "visible" && run();
    document.addEventListener("visibilitychange", visible);
    return () => {
      document.removeEventListener("visibilitychange", visible);
      window.clearTimeout(timer.current);
      ctl.current?.abort();
    };
  }, [run]);

  return { data, error, loading, refresh: run };
}

// ---------- session / capabilities (loaded once) ----------

interface Boot {
  session: Session | null;
  caps: Capabilities | null;
  error: ApiError | null;
}
const BootCtx = createContext<Boot>({ session: null, caps: null, error: null });

export function BootProvider({ children }: { children: ReactNode }) {
  const [boot, setBoot] = useState<Boot>({ session: null, caps: null, error: null });
  useEffect(() => {
    Promise.all([api<Session>("/api/studio/session"), api<Capabilities>("/api/studio/capabilities")])
      .then(([session, caps]) => setBoot({ session, caps, error: null }))
      .catch((e) => setBoot({ session: null, caps: null, error: e instanceof ApiError ? e : new ApiError(String(e), 0) }));
  }, []);
  return <BootCtx.Provider value={boot}>{children}</BootCtx.Provider>;
}
export const useBoot = () => useContext(BootCtx);

// ---------- dashboard data ----------

export { isActive } from "@/lib/status";

const FULL_EVERY = 15000;

/** Refresh `prev` cheaply: while some rows are busy, fetch only those (`?ids=`) and merge them in;
 *  the whole list is fetched when nothing is busy, at the start, and every FULL_EVERY ms. */
export async function pollList<T extends { id: string }>(
  url: string, key: string, prev: T[] | null, busy: (row: T) => boolean, lastFull: number, signal?: AbortSignal,
): Promise<{ rows: T[]; full: boolean }> {
  const ids = prev?.filter(busy).map((r) => r.id) ?? [];
  if (prev && ids.length && Date.now() - lastFull < FULL_EVERY) {
    const fresh = (await api<Record<string, T[]>>(`${url}?ids=${ids.map(encodeURIComponent).join(",")}`, { signal }))[key];
    const byId = new Map(fresh.map((r) => [r.id, r]));
    return { rows: prev.map((r) => byId.get(r.id) ?? r), full: false };
  }
  return { rows: (await api<Record<string, T[]>>(url, { signal }))[key], full: true };
}

/** One list for every useProjects() on the page (the sidebar and the page both show it): calls within
 *  a second share one request. */
const projectList: { rows: Project[] | null; full: number; inflight: Promise<Project[]> | null; at: number } = {
  rows: null, full: 0, inflight: null, at: 0,
};

function loadProjects(): Promise<Project[]> {
  if (projectList.inflight && Date.now() - projectList.at < 1000) return projectList.inflight;
  projectList.at = Date.now();
  projectList.inflight = pollList<Project>("/api/studio/projects", "projects", projectList.rows, isActive, projectList.full)
    .then((r) => {
      projectList.rows = r.rows;
      if (r.full) projectList.full = Date.now();
      return r.rows;
    })
    .catch((e) => {
      projectList.inflight = null;
      throw e;
    });
  return projectList.inflight;
}

export function useProjects() {
  const poll = usePolling<Project[]>(() => loadProjects(), (d) => (d?.some(isActive) ? 2000 : FULL_EVERY));
  const { refresh } = poll;
  const full = useCallback(() => {
    projectList.full = 0;              // a refresh the page asks for (new or deleted project) reloads everything
    projectList.inflight = null;
    return refresh();
  }, [refresh]);
  return { ...poll, refresh: full };
}

/** Live channels and their feed, refreshed every few seconds (404 = live mode isn't offered on this server). */
export function useLive() {
  return usePolling<LiveReply>((signal) => api<LiveReply>("/api/live", { signal }), (d) => (d?.channels.some((c) => c.state === "recording" || c.state === "waiting") ? 3000 : 10000));
}

const HISTORY = 40;

/** CPU / RAM / GPU load every 3 s, with the last few readings kept for the sparklines. */
export function useSystem() {
  const hist = useRef<{ cpu: number[]; ram: number[]; gpu: number[] }>({ cpu: [], ram: [], gpu: [] });
  const [, bump] = useState(0);
  const poll = usePolling<SystemStats>(
    async (signal) => {
      const d = await api<SystemStats>("/api/studio/system", { signal });
      const push = (k: "cpu" | "ram" | "gpu", v: number | null | undefined) => {
        if (v == null) return;
        hist.current[k] = [...hist.current[k], v].slice(-HISTORY);
      };
      push("cpu", d.cpu);
      push("ram", d.ram?.percent);
      push("gpu", d.gpu?.percent);
      bump((n) => n + 1);
      return d;
    },
    () => 3000,
  );
  return { ...poll, history: hist.current };
}

export function useHealth() {
  return usePolling<Health>((signal) => api<Health>("/api/clips/health", { signal }), () => 15000);
}

// ---------- one job, live ----------

/** A job that updates itself: the server pushes every change (progress, cuts, shorts) over SSE. */
export function useJob(id: string | undefined) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [gone, setGone] = useState(false);

  const refresh = useCallback(async () => {
    if (!id) return null;
    try {
      const j = await api<Job>(`/api/clipjob/${id}`);
      setJob(j);
      setError(null);
      return j;
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(String(e), 0));
      return null;
    }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    setJob(null);
    setGone(false);
    refresh();
    const es = new EventSource(`/api/clipjob/${id}/events`);
    es.onmessage = (ev) => {
      try {
        setJob(JSON.parse(ev.data) as Job);
        setError(null);
      } catch {
        /* a keep-alive or a partial frame */
      }
    };
    es.addEventListener("gone", () => setGone(true));
    return () => es.close();
  }, [id, refresh]);

  return { job, error, gone, refresh, loading: !job && !error };
}

/** Poll a job until `done(job)` is true (or it fails / times out). Used to chain "apply edit" before "export". */
export async function waitForJob(id: string, done: (j: Job) => boolean, timeoutMs = 300000): Promise<Job> {
  const t0 = Date.now();
  for (;;) {
    const j = await api<Job>(`/api/clipjob/${id}`);
    if (done(j)) return j;
    if (j.state === "failed") throw new ApiError(j.error || "The job failed.", 500);
    if (Date.now() - t0 > timeoutMs) throw new ApiError("This is taking too long. Check the project page.", 408);
    await new Promise((r) => setTimeout(r, 1500));
  }
}
