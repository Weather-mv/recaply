import { useEffect, useRef, useState } from "react";
import { useTimeline } from "@/components/Timeline";
import type { MediaSel } from "@/hooks/usePlayer";

const PER_SEC = 25;
const MAX_BYTES = 120 * 1024 * 1024; // decoding a whole long source in the browser isn't worth it

export type PeaksState = { status: "idle" | "loading" | "ready" | "skipped" | "error"; peaks: Float32Array | null; note?: string };

/** Loudness peaks (25 per second) of the clip's audio, decoded in the browser. Skipped for very large source files. */
export function useWaveform(media: MediaSel | null, duration: number): PeaksState {
  const [state, setState] = useState<PeaksState>({ status: "idle", peaks: null });
  useEffect(() => {
    if (!media || duration <= 0) {
      setState({ status: "idle", peaks: null });
      return;
    }
    const ctl = new AbortController();
    let ac: AudioContext | null = null;
    setState({ status: "loading", peaks: null });
    (async () => {
      try {
        const head = await fetch(media.url, { method: "HEAD", signal: ctl.signal });
        const size = Number(head.headers.get("content-length") || 0);
        if (size > MAX_BYTES) {
          setState({ status: "skipped", peaks: null, note: "Cut the clip to see its waveform (the source file is large)." });
          return;
        }
        const buf = await (await fetch(media.url, { signal: ctl.signal })).arrayBuffer();
        ac = new AudioContext();
        const audio = await ac.decodeAudioData(buf);
        const ch = audio.getChannelData(0);
        const from = Math.floor(media.offset * audio.sampleRate);
        const to = Math.min(ch.length, Math.floor((media.offset + duration) * audio.sampleRate));
        const n = Math.max(1, Math.ceil(duration * PER_SEC));
        const step = Math.max(1, Math.floor((to - from) / n));
        const peaks = new Float32Array(n);
        for (let i = 0; i < n; i++) {
          let m = 0;
          const a = from + i * step;
          for (let j = a; j < Math.min(a + step, to); j += 4) m = Math.max(m, Math.abs(ch[j]));
          peaks[i] = m;
        }
        const top = Math.max(...peaks, 0.05);
        for (let i = 0; i < n; i++) peaks[i] /= top;
        if (!ctl.signal.aborted) setState({ status: "ready", peaks });
      } catch (e) {
        if (!ctl.signal.aborted) setState({ status: "error", peaks: null, note: "The waveform couldn't be read for this file." });
      } finally {
        void ac?.close();
      }
    })();
    return () => ctl.abort();
  }, [media?.url, media?.offset, duration]); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}

/** Draws the peaks across the track (a child of <Track>). */
export function Waveform({ state, color = "hsl(var(--primary))" }: { state: PeaksState; color?: string }) {
  const { pps, duration } = useTimeline();
  const ref = useRef<HTMLCanvasElement>(null);
  const cssW = Math.max(1, duration * pps);
  const px = Math.min(12000, Math.ceil(cssW));

  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!state.peaks) return;
    const peaks = state.peaks;
    const c = getComputedStyle(cv).getPropertyValue("--wf").trim() || "#3984ff";
    ctx.fillStyle = c;
    const mid = cv.height / 2;
    for (let x = 0; x < cv.width; x += 2) {
      const i = Math.min(peaks.length - 1, Math.floor((x / cv.width) * peaks.length));
      const h = Math.max(2, peaks[i] * (cv.height - 8));
      ctx.fillRect(x, mid - h / 2, 1.4, h);
    }
  }, [state.peaks, px]);

  if (!state.peaks) {
    return (
      <div className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">
        {state.status === "loading" ? "Reading the audio…" : state.note || "No audio to draw"}
      </div>
    );
  }
  return <canvas ref={ref} width={px} height={44} style={{ width: cssW, height: "100%", ["--wf" as string]: color }} className="absolute inset-0" />;
}
