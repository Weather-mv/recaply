import { Loader2, Scissors } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { planFrame, type Layout } from "@/lib/reframe";
import type { Word } from "@/lib/types";
import { activeWordIndex } from "@/lib/words";

const W = 540;
const H = 960;
const CAPTION_CHUNK = 4;

interface Props {
  /** returns the playing <video>; read every frame */
  getVideo: () => HTMLVideoElement | null;
  hasPicture: boolean;
  layout: Layout;
  words: Word[];
  time: number;
  showCaptions: boolean;
  hook: string;
  showHook: boolean;
  safeZone: boolean;
  onCut?: () => void;
  cutting?: boolean;
}

function wrapText(ctx: CanvasRenderingContext2D, tokens: { text: string; hot: boolean }[], maxW: number) {
  const lines: { text: string; hot: boolean }[][] = [[]];
  let w = 0;
  for (const t of tokens) {
    const tw = ctx.measureText(t.text + " ").width;
    if (w + tw > maxW && lines[lines.length - 1].length) {
      lines.push([]);
      w = 0;
    }
    lines[lines.length - 1].push(t);
    w += tw;
  }
  return lines;
}

/** The clip as it will be exported: 9:16, drawn live from the video with the chosen layout, karaoke captions and the hook. */
export function ReframeCanvas({ getVideo, hasPicture, layout, words, time, showCaptions, hook, showHook, safeZone, onCut, cutting }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  // the draw loop reads the latest props through a ref so it never restarts
  const live = useRef({ layout, words, time, showCaptions, hook, showHook, safeZone, hasPicture });
  live.current = { layout, words, time, showCaptions, hook, showHook, safeZone, hasPicture };

  useEffect(() => {
    const cv = canvas.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    let raf = 0;
    const draw = () => {
      const s = live.current;
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#050507";
      ctx.fillRect(0, 0, W, H);
      const v = getVideo();
      if (s.hasPicture && v && v.readyState >= 2 && v.videoWidth) {
        for (const d of planFrame(s.layout, v.videoWidth, v.videoHeight, W, H)) {
          ctx.save();
          if (d.blur) {
            ctx.filter = "blur(28px) brightness(.55)";
            ctx.drawImage(v, d.src.x, d.src.y, d.src.w, d.src.h, -40, -40, d.dst.w + 80, d.dst.h + 80);
          } else {
            ctx.beginPath();
            ctx.rect(d.dst.x, d.dst.y, d.dst.w, d.dst.h);
            ctx.clip();
            ctx.drawImage(v, d.src.x, d.src.y, d.src.w, d.src.h, d.dst.x, d.dst.y, d.dst.w, d.dst.h);
          }
          ctx.restore();
        }
        if (s.layout === "split") {
          ctx.fillStyle = "rgba(255,255,255,.18)";
          ctx.fillRect(0, Math.round(H * 0.38) - 1, W, 2);
        }
      } else {
        const g = ctx.createLinearGradient(0, 0, W, H);
        g.addColorStop(0, "#0d2a6b");
        g.addColorStop(1, "#050507");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
      }

      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";

      // hook: the first three seconds, in a box near the top
      if (s.showHook && s.hook && s.time < 3) {
        ctx.font = "800 44px 'Plus Jakarta Sans', system-ui, sans-serif";
        const toks = s.hook.split(/\s+/).map((text) => ({ text, hot: false }));
        const lines = wrapText(ctx, toks, W - 120);
        const lh = 56;
        const boxH = lines.length * lh + 36;
        const y0 = 130;
        ctx.fillStyle = "rgba(255,255,255,.96)";
        ctx.beginPath();
        ctx.roundRect(40, y0, W - 80, boxH, 22);
        ctx.fill();
        ctx.fillStyle = "#08080a";
        lines.forEach((ln, i) => ctx.fillText(ln.map((t) => t.text).join(" "), W / 2, y0 + 18 + lh / 2 + i * lh));
      }

      // captions: the word being spoken, in the brand colour, with its neighbours
      if (s.showCaptions && s.words.length) {
        const idx = activeWordIndex(s.words, s.time);
        if (idx >= 0) {
          const first = idx - (idx % CAPTION_CHUNK);
          const chunk = s.words.slice(first, first + CAPTION_CHUNK);
          ctx.font = "800 58px 'Plus Jakarta Sans', system-ui, sans-serif";
          const toks = chunk.map((w, i) => ({ text: w.w.replace(/[.,!?]+$/, "").toUpperCase(), hot: first + i === idx }));
          const lines = wrapText(ctx, toks, W - 70);
          const lh = 70;
          const yBase = H * 0.66 - ((lines.length - 1) * lh) / 2;
          lines.forEach((ln, li) => {
            const total = ln.reduce((a, t) => a + ctx.measureText(t.text + " ").width, 0);
            let x = W / 2 - total / 2;
            ctx.textAlign = "left";
            for (const t of ln) {
              ctx.lineWidth = 12;
              ctx.strokeStyle = "rgba(0,0,0,.92)";
              ctx.strokeText(t.text, x, yBase + li * lh);
              ctx.fillStyle = t.hot ? "#3984ff" : "#ffffff";
              ctx.fillText(t.text, x, yBase + li * lh);
              x += ctx.measureText(t.text + " ").width;
            }
            ctx.textAlign = "center";
          });
        }
      }

      // the area TikTok / Shorts / Reels cover with their own buttons and captions
      if (s.safeZone) {
        ctx.fillStyle = "rgba(255,80,60,.16)";
        ctx.fillRect(0, H * 0.75, W, H * 0.25);
        ctx.fillRect(W * 0.82, H * 0.45, W * 0.18, H * 0.3);
        ctx.fillRect(0, 0, W, H * 0.08);
        ctx.strokeStyle = "rgba(255,255,255,.4)";
        ctx.setLineDash([8, 6]);
        ctx.lineWidth = 2;
        ctx.strokeRect(W * 0.04, H * 0.08, W * 0.78, H * 0.67);
        ctx.setLineDash([]);
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [getVideo]);

  return (
    <div className="relative mx-auto aspect-[9/16] h-full max-h-full overflow-hidden rounded-[1.75rem] border-4 border-secondary bg-black shadow-2xl">
      <canvas ref={canvas} width={W} height={H} className="size-full" aria-label="9:16 preview of the exported short" />
      {!hasPicture && (
        <div className="absolute inset-0 grid place-items-center bg-black/55 p-6 text-center text-white">
          <div className="space-y-3">
            <p className="text-lg font-bold">No picture to preview yet</p>
            <p className="text-sm text-white/70">Cut this clip once and the 9:16 preview plays here in real time.</p>
            {onCut && (
              <Button onClick={onCut} disabled={cutting}>
                {cutting ? <Loader2 className="animate-spin" /> : <Scissors />}
                {cutting ? "Cutting…" : "Cut this clip"}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
