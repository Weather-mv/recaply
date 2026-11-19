import { useCallback, useEffect, useRef, useState } from "react";
import { clipFileUrl, mediaUrl } from "@/lib/api";
import type { Clip, Job } from "@/lib/types";
import { clamp } from "@/lib/utils";

/** What to play for a clip. `offset` is the clip's start inside the file (0 for a cut clip, the clip's start for the source). */
export interface MediaSel {
  url: string;
  kind: "cut" | "source" | "audio";
  offset: number;
  video: boolean;
}

/** Best preview first: the cut clip, then the source video, then the analysis audio. null = nothing to play yet. */
export function pickMedia(job: Job, clip: Clip): MediaSel | null {
  const cut = job.cuts[String(clip.id)];
  if (cut?.state === "done" && cut.file) return { url: clipFileUrl(job.id, cut.file), kind: "cut", offset: 0, video: true };
  if (job.media.source) return { url: mediaUrl(job.id, "source"), kind: "source", offset: clip.start, video: true };
  if (job.media.audio) return { url: mediaUrl(job.id, "audio"), kind: "audio", offset: clip.start, video: false };
  return null;
}

export interface Player {
  /** a callback ref: the listeners attach whenever the <video> actually mounts (it may appear after the hook first runs) */
  ref: (node: HTMLVideoElement | null) => void;
  /** seconds from the clip's start */
  time: number;
  duration: number;
  playing: boolean;
  ready: boolean;
  seek: (t: number) => void;
  toggle: () => void;
  pause: () => void;
  /** a stable getter for the media element (canvas drawing reads it every frame) */
  el: () => HTMLVideoElement | null;
}

/**
 * Drives one <video> as if it only contained the clip: time 0 is the clip's start, playback stops at its end.
 * Attach `ref` to a <video src={media.url}>. Works for the cut file, the whole source or the audio alone.
 */
export function usePlayer(media: MediaSel | null, clip: { id: number; duration: number } | null): Player {
  const nodeRef = useRef<HTMLVideoElement | null>(null);
  const [node, setNode] = useState<HTMLVideoElement | null>(null);
  const ref = useCallback((n: HTMLVideoElement | null) => {
    nodeRef.current = n;
    setNode(n);
  }, []);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const offset = media?.offset ?? 0;
  const duration = clip?.duration ?? 0;
  const live = useRef({ offset, duration });
  live.current = { offset, duration };

  useEffect(() => {
    const el = node;
    setTime(0);
    setPlaying(false);
    setReady(false);
    if (!el || !media) return;
    let raf = 0;
    const tick = () => {
      const { offset: o, duration: d } = live.current;
      const t = el.currentTime - o;
      if (!el.paused && t >= d - 0.03) {
        el.pause();
        el.currentTime = o + d;
        setTime(d);
      } else setTime(clamp(t, 0, d));
      raf = requestAnimationFrame(tick);
    };
    const onMeta = () => {
      el.currentTime = live.current.offset;
      setReady(true);
    };
    const onPlay = () => {
      setPlaying(true);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      setPlaying(false);
      cancelAnimationFrame(raf);
      setTime(clamp(el.currentTime - live.current.offset, 0, live.current.duration));
    };
    const onSeeked = () => setTime(clamp(el.currentTime - live.current.offset, 0, live.current.duration));
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onPause);
    el.addEventListener("seeked", onSeeked);
    if (el.readyState >= 1) onMeta();
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onPause);
      el.removeEventListener("seeked", onSeeked);
    };
    // a different clip in the same file only changes offset/duration: re-seek, don't reload
  }, [node, media?.url, offset, clip?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const seek = useCallback((t: number) => {
    const el = nodeRef.current;
    const { offset: o, duration: d } = live.current;
    const c = clamp(t, 0, d);
    if (el) el.currentTime = o + c;
    setTime(c);
  }, []);

  const toggle = useCallback(() => {
    const el = nodeRef.current;
    if (!el) return;
    if (el.paused) {
      const { offset: o, duration: d } = live.current;
      if (el.currentTime - o >= d - 0.05) el.currentTime = o;
      void el.play().catch(() => undefined);
    } else el.pause();
  }, []);

  const pause = useCallback(() => nodeRef.current?.pause(), []);
  const el = useCallback(() => nodeRef.current, []);

  return { ref, time, duration, playing, ready, seek, toggle, pause, el };
}

/** Space plays / pauses, arrows step, unless the user is typing. */
export function usePlayerKeys(p: Pick<Player, "toggle" | "seek" | "time">, enabled = true) {
  const latest = useRef(p);
  latest.current = p;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const { toggle, seek, time } = latest.current;
      if (e.code === "Space") {
        e.preventDefault();
        toggle();
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        seek(time - (e.shiftKey ? 5 : 1));
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        seek(time + (e.shiftKey ? 5 : 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
