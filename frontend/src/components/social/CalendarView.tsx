import { ChevronLeft, ChevronRight, ExternalLink, Plus, RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  addDays, dayKey, fmtTimeOfDay, monthGrid, moveToDay, NET_LOOK, sameDay, startOfDay, STATUS_LABEL, STATUS_TONE, weekDays,
  type NetworkKey, type SocialPost,
} from "@/lib/social";
import { cn } from "@/lib/utils";

export type CalMode = "month" | "week";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Chip({ p, onOpen, compact }: { p: SocialPost; onOpen: (p: SocialPost) => void; compact?: boolean }) {
  const movable = ["queued", "draft", "failed", "partial", "cancelled"].includes(p.status);
  const nets = [...new Set(p.targets.map((t) => t.platform))] as NetworkKey[];
  return (
    <button type="button" draggable={movable}
      onDragStart={(e) => { e.dataTransfer.setData("text/post", p.group); e.dataTransfer.effectAllowed = "move"; }}
      onClick={() => onOpen(p)}
      title={`${p.title} · ${STATUS_LABEL[p.status] ?? p.status}`}
      className={cn("flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[13px] leading-tight ring-1 ring-inset", STATUS_TONE[p.status] ?? STATUS_TONE.draft, movable && "cursor-grab active:cursor-grabbing")}>
      <span className="shrink-0 font-medium tabular-nums">{fmtTimeOfDay(p.run_at)}</span>
      <span className="flex shrink-0 -space-x-1">
        {nets.map((n) => { const I = NET_LOOK[n].icon; return <I key={n} className="size-3.5" style={{ color: NET_LOOK[n].color }} />; })}
      </span>
      {!compact && <span className="truncate text-foreground">{p.title}</span>}
    </button>
  );
}

/** Month / week grid of scheduled and sent posts. Drag a chip to another day to move it (same time of day). */
export function CalendarView({ posts, cursor, setCursor, mode, onOpen, onNew, onMove }: {
  posts: SocialPost[];
  cursor: Date;
  setCursor: (d: Date) => void;
  mode: CalMode;
  onOpen: (p: SocialPost) => void;
  onNew: (day: Date) => void;
  onMove: (p: SocialPost, runAt: number) => void;
}) {
  const [over, setOver] = useState<string | null>(null);
  const today = new Date();
  const days = mode === "month" ? monthGrid(cursor) : weekDays(cursor);
  const byDay = new Map<string, SocialPost[]>();
  for (const p of posts.filter((x) => x.status !== "draft")) {
    const k = dayKey(new Date(p.run_at * 1000));
    byDay.set(k, [...(byDay.get(k) ?? []), p]);
  }
  const step = (n: number) => setCursor(mode === "month" ? new Date(cursor.getFullYear(), cursor.getMonth() + n, 1) : addDays(cursor, 7 * n));
  const label = mode === "month"
    ? cursor.toLocaleDateString([], { month: "long", year: "numeric" })
    : `${days[0].toLocaleDateString([], { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;

  const drop = (e: React.DragEvent, day: Date) => {
    e.preventDefault();
    setOver(null);
    const g = e.dataTransfer.getData("text/post");
    const p = posts.find((x) => x.group === g);
    if (!p) return;
    const t = moveToDay(p.run_at, day);
    if (t < Date.now() / 1000 - 60) return;                       // can't move into the past
    if (!sameDay(new Date(p.run_at * 1000), day)) onMove(p, t);
  };

  return (
    <div className="surface overflow-hidden rounded-xl">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <Button size="icon" variant="ghost" aria-label="Previous" onClick={() => step(-1)}><ChevronLeft /></Button>
        <Button size="icon" variant="ghost" aria-label="Next" onClick={() => step(1)}><ChevronRight /></Button>
        <h2 className="text-lg font-semibold">{label}</h2>
        <Button size="sm" variant="secondary" className="ml-1" onClick={() => setCursor(new Date())}>Today</Button>
      </div>
      <div className="overflow-x-auto">
        <div className={cn("grid min-w-[720px] grid-cols-7")}>
          {WEEKDAYS.map((w) => <div key={w} className="border-b px-2 py-2 text-center text-sm font-medium text-muted-foreground">{w}</div>)}
          {days.map((d) => {
            const k = dayKey(d);
            const list = (byDay.get(k) ?? []).sort((a, b) => a.run_at - b.run_at);
            const outside = mode === "month" && d.getMonth() !== cursor.getMonth();
            const past = startOfDay(d) < startOfDay(today);
            const max = mode === "month" ? 3 : 12;
            return (
              <div key={k}
                onDragOver={(e) => { if (!past) { e.preventDefault(); setOver(k); } }}
                onDragLeave={() => setOver((o) => (o === k ? null : o))}
                onDrop={(e) => drop(e, d)}
                className={cn("group relative flex flex-col gap-1 border-b border-r p-1.5 [&:nth-child(7n)]:border-r-0",
                  mode === "month" ? "min-h-[118px]" : "min-h-[360px]",
                  outside && "bg-secondary/40", over === k && "bg-primary/10 ring-2 ring-inset ring-primary")}>
                <div className="flex items-center justify-between">
                  <span className={cn("grid size-7 place-items-center rounded-full text-sm", sameDay(d, today) ? "bg-primary font-semibold text-primary-foreground" : outside ? "text-muted-foreground" : "")}>
                    {d.getDate()}
                  </span>
                  {!past && (
                    <button aria-label={`New post on ${d.toDateString()}`} onClick={() => onNew(d)}
                      className="grid size-7 place-items-center rounded-md text-muted-foreground opacity-0 transition hover:bg-secondary hover:text-foreground focus:opacity-100 group-hover:opacity-100">
                      <Plus className="size-4" />
                    </button>
                  )}
                </div>
                {list.slice(0, max).map((p) => <Chip key={p.group} p={p} onOpen={onOpen} />)}
                {list.length > max && <span className="px-1 text-sm text-muted-foreground">+{list.length - max} more</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Details of one post: each target's status, the live link, retry / edit / delete. */
export function PostDetails({ post, onClose, onEdit, onRetry, onDelete }: {
  post: SocialPost;
  onClose: () => void;
  onEdit: () => void;
  onRetry: () => void;
  onDelete: () => void;
}) {
  const editable = ["queued", "draft", "failed", "partial", "cancelled"].includes(post.status);
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Post details" className="surface w-full max-w-lg space-y-4 rounded-xl bg-card p-5">
        <div>
          <h3 className="text-lg font-semibold">{post.title}</h3>
          <p className="text-sm text-muted-foreground">
            {new Date(post.run_at * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · {post.job_title}
          </p>
        </div>
        <ul className="space-y-2">
          {post.targets.map((t) => {
            const I = NET_LOOK[t.platform].icon;
            return (
              <li key={t.id} className="rounded-lg bg-secondary/60 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <I className="size-4" style={{ color: NET_LOOK[t.platform].color }} />
                  <span className="flex-1 truncate font-medium">{t.account || NET_LOOK[t.platform].label}</span>
                  <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", STATUS_TONE[t.status] ?? STATUS_TONE.draft)}>{STATUS_LABEL[t.status] ?? t.status}</span>
                </div>
                {t.result?.url && (
                  <a href={t.result.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-sm text-primary hover:underline">
                    View the post <ExternalLink className="size-3.5" />
                  </a>
                )}
                {t.result?.note && <p className="mt-1 text-sm text-muted-foreground">{t.result.note}</p>}
                {t.error && <p className="mt-1 text-sm text-red-600 dark:text-red-400">{t.error}{t.attempts > 1 ? ` (tried ${t.attempts}×)` : ""}</p>}
              </li>
            );
          })}
        </ul>
        <div className="flex flex-wrap gap-2">
          {editable && <Button onClick={onEdit}>Edit</Button>}
          {(post.status === "failed" || post.status === "partial" || post.status === "cancelled") && <Button variant="secondary" onClick={onRetry}><RotateCw /> Retry</Button>}
          {editable && <Button variant="ghost" className="text-destructive" onClick={onDelete}><Trash2 /> Delete</Button>}
          <Button variant="ghost" className="ml-auto" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}
