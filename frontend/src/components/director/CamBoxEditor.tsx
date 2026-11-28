import { Check, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { FracRect } from "@/lib/reframe";

interface Props {
  getVideo: () => HTMLVideoElement | null;
  /** the box in use now (drawn earlier or detected) */
  value: FracRect | null;
  detected: FracRect | null;
  onSave: (box: FracRect | null) => void;
  onClose: () => void;
}

const CW = 960;

/** The whole source frame with the facecam box on it: drag on the picture to draw a new box. */
export function CamBoxEditor({ getVideo, value, detected, onSave, onClose }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState<FracRect | null>(value ?? detected);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const live = useRef(box);
  live.current = box;
  const [aspect, setAspect] = useState(16 / 9);

  useEffect(() => {
    const cv = canvas.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    let raf = 0;
    const draw = () => {
      const v = getVideo();
      const H = cv.height;
      ctx.fillStyle = "#050507";
      ctx.fillRect(0, 0, CW, H);
      if (v && v.readyState >= 2 && v.videoWidth) {
        const a = v.videoWidth / v.videoHeight;
        if (Math.abs(a - aspect) > 0.01) setAspect(a);
        ctx.drawImage(v, 0, 0, CW, H);
      }
      const b = live.current;
      if (b) {
        const [x, y, w, h] = [b[0] * CW, b[1] * H, b[2] * CW, b[3] * H];
        ctx.fillStyle = "rgba(0,0,0,.5)";
        ctx.fillRect(0, 0, CW, y);
        ctx.fillRect(0, y + h, CW, H - y - h);
        ctx.fillRect(0, y, x, h);
        ctx.fillRect(x + w, y, CW - x - w, h);
        ctx.strokeStyle = "#3984ff";
        ctx.lineWidth = 4;
        ctx.strokeRect(x, y, w, h);
        ctx.fillStyle = "#3984ff";
        ctx.font = "700 22px system-ui, sans-serif";
        ctx.fillText("Facecam", x + 8, Math.max(26, y - 10));
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [getVideo, aspect]);

  const at = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const down = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = at(e);
  };
  const move = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const p = at(e);
    const s = drag.current;
    setBox([Math.min(s.x, p.x), Math.min(s.y, p.y), Math.abs(p.x - s.x), Math.abs(p.y - s.y)]);
  };
  const up = () => {
    drag.current = null;
    if (box && (box[2] < 0.03 || box[3] < 0.03)) setBox(value ?? detected); // a click, not a drag
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" role="dialog" aria-label="Draw the facecam box">
      <div className="w-full max-w-4xl space-y-3 rounded-xl border bg-card p-4 shadow-2xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold">Draw the facecam box</h3>
            <p className="text-sm text-muted-foreground">Drag on the picture around the streamer's camera. Pause on a frame where the cam is easy to see.</p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X /></Button>
        </div>
        <canvas
          ref={canvas}
          width={CW}
          height={Math.round(CW / aspect)}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          className="w-full cursor-crosshair touch-none rounded-md bg-black"
        />
        <div className="flex flex-wrap items-center justify-end gap-2">
          {detected && (
            <Button variant="ghost" onClick={() => { onSave(null); onClose(); }}><RotateCcw /> Use the detected box</Button>
          )}
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => { onSave(box ? (box.map((v) => Math.round(v * 10000) / 10000) as FracRect) : null); onClose(); }} disabled={!box}><Check /> Use this box</Button>
        </div>
      </div>
    </div>
  );
}
