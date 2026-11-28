/** Social hub: types, network looks and small date helpers shared by the Calendar, Accounts and Composer. */
import { Instagram, Linkedin, Twitter, Youtube, type LucideIcon } from "lucide-react";

export type NetworkKey = "youtube" | "linkedin" | "x" | "instagram";

export interface NetworkInfo {
  key: NetworkKey;
  label: string;
  configured: boolean;
  caption_max: number;
  title_max: number;
  hashtag_max: number;
  setup_url: string;
  note: string;
  env: string;
  redirect_uri: string;
}

export interface SocialAccount {
  id: number;
  network: NetworkKey;
  name: string;
  handle: string;
  avatar: string;
  status: "ok" | "reconnect";
  error: string | null;
  created: number;
}

export interface PostableVideo {
  job_id: string;
  job_title: string;
  clip_id: number;
  file: string;
  kind: "short" | "cut";
  format: string;
  duration: number;
  title: string;
  hook: string;
  description: string;
  hashtags: string[];
}

export interface PostTarget {
  id: number;
  platform: NetworkKey;
  account_id: number;
  account: string;
  avatar: string;
  title: string;
  caption: string;
  tags: string[];
  privacy: string;
  thumbnail: string | null;
  status: string;
  error: string | null;
  result: { url?: string; note?: string } | null;
  attempts: number;
}

export interface SocialPost {
  group: string;
  job_id: string;
  job_title: string;
  clip_id: number;
  file: string;
  run_at: number;
  status: string;
  title: string;
  thumb_at: number | null;
  targets: PostTarget[];
}

export const NET_LOOK: Record<NetworkKey, { icon: LucideIcon; color: string; label: string }> = {
  youtube: { icon: Youtube, color: "#ff0033", label: "YouTube" },
  linkedin: { icon: Linkedin, color: "#0a66c2", label: "LinkedIn" },
  x: { icon: Twitter, color: "#71767b", label: "X" },
  instagram: { icon: Instagram, color: "#e1306c", label: "Instagram" },
};

/** Status colours (light + dark readable). */
export const STATUS_TONE: Record<string, string> = {
  done: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 ring-emerald-500/25",
  queued: "bg-primary/15 text-primary ring-primary/25",
  uploading: "bg-amber-400/15 text-amber-600 dark:text-amber-300 ring-amber-400/25",
  draft: "bg-zinc-500/10 text-muted-foreground ring-zinc-500/20",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400 ring-red-500/25",
  partial: "bg-red-500/15 text-red-600 dark:text-red-400 ring-red-500/25",
  cancelled: "bg-zinc-500/10 text-muted-foreground ring-zinc-500/20",
};
export const STATUS_LABEL: Record<string, string> = {
  done: "Posted", queued: "Scheduled", uploading: "Posting…", draft: "Draft", failed: "Failed", partial: "Partly failed", cancelled: "Cancelled",
};

export const thumbFileUrl = (jobId: string, name: string): string => `/api/social/thumb/${jobId}/${encodeURIComponent(name.replace(/^thumbs\//, ""))}`;

/** Which thumbnail size each network uses. */
export const THUMB_SIZE: Record<NetworkKey, string> = { youtube: "youtube", linkedin: "linkedin", x: "youtube", instagram: "vertical" };

// ---------- dates ----------

export const startOfDay = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const addDays = (d: Date, n: number): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
export const sameDay = (a: Date, b: Date): boolean => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
export const dayKey = (d: Date): string => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

/** Monday-first weeks covering the month (6 rows × 7 days, like most calendars). */
export function monthGrid(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;
  const start = addDays(first, -lead);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

export function weekDays(day: Date): Date[] {
  const d = startOfDay(day);
  const start = addDays(d, -((d.getDay() + 6) % 7));
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

/** "2026-11-28T18:30" for <input type="datetime-local">, in local time. */
export function toLocalInput(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Keep a post's time of day, move it to another day (drag and drop on the calendar). */
export function moveToDay(epochSec: number, day: Date): number {
  const t = new Date(epochSec * 1000);
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), t.getHours(), t.getMinutes()).getTime() / 1000;
}

export const fmtTimeOfDay = (epochSec: number): string => new Date(epochSec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

/** Characters left, with a warning colour near the limit. */
export function counterTone(len: number, max: number): string {
  if (len > max) return "text-red-500";
  if (len > max * 0.9) return "text-amber-500";
  return "text-muted-foreground";
}
