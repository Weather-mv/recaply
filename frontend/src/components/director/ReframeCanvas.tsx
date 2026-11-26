import { Loader2, Scissors } from "lucide-react";
import { useEffect, useMemo, useRef, type PointerEvent as RPointerEvent } from "react";
import { Button } from "@/components/ui/button";
import { bandY, captionBox, chunkWords, drawCaptions, type CaptionStyle } from "@/lib/captions";
import { planFrame, resolveLayout, type FracRect, type Layout, type LayoutInfo } from "@/lib/reframe";
import type { Word } from "@/lib/types";

const W = 540;
const H = 960;

interface Props {
  /** returns the playing <video>; read every frame */
  getVideo: () => HTMLVideoElement | null;
  hasPicture: boolean;
  layout: Layout;
  /** what's on screen in this clip (facecam, face path, action), measured by the server; null until known */
  info: LayoutInfo | null;
  ratio: number;
  camBox: FracRect | null;
  words: Word[];
  time: number;
  showCaptions: boolean;
  /** the caption look (preset + Captions panel changes) and the loaded CSS font family to draw it with */
  capStyle: CaptionStyle;
  capFont: string;
  hook: string;
  showHook: boolean;
  /** the stakes line for the first second ("" = none); the hook text then starts after it */
  stakes: string;
  safeZone: boolean;
  onCut?: () => void;
  cutting?: boolean;
  /** drag the captions on the preview: their new centre as shares of the width and height */
  onMoveCaptions?: (pos: { x: number; y: number }) => void;
}

const SNAP = 0.025;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

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
export function ReframeCanvas({ getVideo, hasPicture, layout, info, ratio, camBox, words, time, showCaptions, capStyle, capFont, hook, showHook, stakes, safeZone, onCut, cutting, onMoveCaptions }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chunks = useMemo(() => chunkWords(words, capStyle.per), [words, capStyle.per]);
  // the draw loop reads the latest props through a ref so it never restarts
  const live = useRef({ layout, info, ratio, camBox, words, time, showCaptions, capStyle, capFont, chunks, hook, showHook, stakes, safeZone, hasPicture });
  live.current = { layout, info, ratio, camBox, words, time, showCaptions, capStyle, capFont, chunks, hook, showHook, stakes, safeZone, hasPicture };
  // dragging the captions: pointer offset from their centre, and whether they snapped to the middle
  const ui = useRef<{ hover: boolean; drag: { dx: number; dy: number } | null; snapX: boolean; snapY: boolean }>({ hover: false, drag: null, snapX: false, snapY: false });
  const moveRef = useRef(onMoveCaptions);
  moveRef.current = onMoveCaptions;

  const capBox = () => {
    const s = live.current;
    const cy = H * (s.capStyle.y ?? bandY(resolveLayout(s.layout, s.info, s.camBox), s.ratio));
    return captionBox(s.capStyle, W, H, cy);
  };
  const toCanvas = (e: RPointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { px: ((e.clientX - r.left) / r.width) * W, py: ((e.clientY - r.top) / r.height) * H };
  };
  const inBox = (px: number, py: number) => {
    const b = capBox();
    return px >= b.x - 8 && px <= b.x + b.w + 8 && py >= b.y - 8 && py <= b.y + b.h + 8;
  };
  const onPointerDown = (e: RPointerEvent<HTMLCanvasElement>) => {
    if (!moveRef.current || !live.current.showCaptions) return;
    const { px, py } = toCanvas(e);
    if (!inBox(px, py)) return;
    const b = capBox();
    ui.current.drag = { dx: px - (b.x + b.w / 2), dy: py - (b.y + b.h / 2) };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  const onPointerMove = (e: RPointerEvent<HTMLCanvasElement>) => {
    const { px, py } = toCanvas(e);
    const u = ui.current;
    if (u.drag) {
      let x = clamp((px - u.drag.dx) / W, 0.15, 0.85);
      let y = clamp((py - u.drag.dy) / H, 0.08, 0.92);
      u.snapX = Math.abs(x - 0.5) < SNAP;
      u.snapY = Math.abs(y - 0.5) < SNAP;
      if (u.snapX) x = 0.5;
      if (u.snapY) y = 0.5;
      moveRef.current?.({ x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 });
    } else {
      u.hover = !!moveRef.current && live.current.showCaptions && inBox(px, py);
    }
    e.currentTarget.style.cursor = u.drag ? "grabbing" : u.hover ? "grab" : "";
  };
  const onPointerUp = (e: RPointerEvent<HTMLCanvasElement>) => {
    const u = ui.current;
    if (!u.drag) return;
    u.drag = null;
    u.snapX = u.snapY = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    e.currentTarget.style.cursor = u.hover ? "grab" : "";
  };
  const onPointerLeave = () => {
    if (!ui.current.drag) ui.current.hover = false;
  };

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
      const drawn = resolveLayout(s.layout, s.info, s.camBox);
      if (s.hasPicture && v && v.readyState >= 2 && v.videoWidth) {
        const t = v.currentTime || s.time;
        for (const d of planFrame(s.layout, v.videoWidth, v.videoHeight, W, H, s.info, { ratio: s.ratio, camBox: s.camBox, time: t })) {
          ctx.save();
          if (d.blur) {
            ctx.filter = "blur(28px) brightness(.55)";
            ctx.drawImage(v, d.src.x, d.src.y, d.src.w, d.src.h, -40, -40, d.dst.w + 80, d.dst.h + 80);
          } else {
            ctx.beginPath();
            ctx.rect(d.dst.x, d.dst.y, d.dst.w, d.dst.h);
            ctx.clip();
            ctx.drawImage(v, d.src.x, d.src.y, d.src.w, d.src.h, d.dst.x, d.dst.y, d.dst.w, d.dst.h);
            if (d.frame) {
              ctx.strokeStyle = "rgba(255,255,255,.85)";
              ctx.lineWidth = 4;
              ctx.strokeRect(d.dst.x + 2, d.dst.y + 2, d.dst.w - 4, d.dst.h - 4);
            }
          }
          ctx.restore();
        }
        if (drawn === "split" || drawn === "split_bottom") {
          ctx.fillStyle = "rgba(255,255,255,.18)";
          const seam = drawn === "split" ? s.ratio : 1 - s.ratio;
          ctx.fillRect(0, Math.round(H * seam) - 1, W, 2);
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
      // stakes: the first second of a clip that starts mid-action (subtitles.context_layer)
      if (s.stakes && s.time < 1) {
        ctx.font = "800 42px 'Plus Jakarta Sans', system-ui, sans-serif";
        const text = s.stakes.toUpperCase();
        const tw = Math.min(W - 60, ctx.measureText(text).width + 60);
        ctx.fillStyle = "rgba(11,11,15,.88)";
        ctx.beginPath();
        ctx.roundRect((W - tw) / 2, 96, tw, 76, 18);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.fillText(text, W / 2, 134, W - 80);
      }
      // hook: three seconds in a box near the top, after the stakes line when there is one
      const hookFrom = s.stakes ? 1 : 0;
      if (s.showHook && s.hook && s.time >= hookFrom && s.time < hookFrom + 3) {
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

      // captions: the export's look and timing (lib/captions follows the renderer)
      if (s.showCaptions && s.chunks.length) {
        const y = s.capStyle.y ?? bandY(drawn, s.ratio);
        drawCaptions(ctx, s.chunks, s.time, s.capStyle, W, H, H * y, s.capFont);
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        // the move handle: the caption box while hovered or dragged, plus centre guides when snapped
        const u = ui.current;
        if (u.hover || u.drag) {
          const b = captionBox(s.capStyle, W, H, H * y);
          ctx.save();
          ctx.strokeStyle = u.drag ? "#0865ff" : "rgba(255,255,255,.75)";
          ctx.lineWidth = 2;
          ctx.setLineDash([8, 6]);
          ctx.strokeRect(b.x + 1, b.y + 1, b.w - 2, b.h - 2);
          ctx.setLineDash([]);
          ctx.strokeStyle = "#ff3d8b";
          if (u.drag && u.snapX) ctx.strokeRect(W / 2, 0, 0.5, H);
          if (u.drag && u.snapY) ctx.strokeRect(0, H / 2, W, 0.5);
          if (!u.drag) {
            ctx.font = "700 20px 'Plus Jakarta Sans', system-ui, sans-serif";
            ctx.fillStyle = "rgba(8,8,10,.8)";
            const label = "Drag to move";
            const lw = ctx.measureText(label).width + 20;
            ctx.beginPath();
            ctx.roundRect(b.x + b.w / 2 - lw / 2, b.y - 34, lw, 30, 8);
            ctx.fill();
            ctx.fillStyle = "#ffffff";
            ctx.fillText(label, b.x + b.w / 2, b.y - 19);
          }
          ctx.restore();
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
      <canvas
        ref={canvas}
        width={W}
        height={H}
        className="size-full touch-none"
        aria-label="9:16 preview of the exported short. Drag the captions to move them."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerLeave}
      />
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
