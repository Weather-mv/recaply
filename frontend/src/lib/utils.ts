import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** 83.4 -> "1:23", 3723 -> "1:02:03" */
export function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

/** 83.4 -> "1:23.4" (timeline playhead) */
export function fmtPrecise(sec: number): string {
  const t = Math.max(0, sec || 0);
  const m = Math.floor(t / 60);
  const r = t - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
}

export function ago(epochSec: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - epochSec);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));
