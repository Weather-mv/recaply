import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cn, clamp, fmtPrecise, fmtTime } from "@/lib/utils";

interface Ctx {
  pps: number; // pixels per second
  duration: number;
}
const TimelineCtx = createContext<Ctx>({ pps: 10, duration: 1 });
export const useTimeline = () => useContext(TimelineCtx);

const LABEL_W = 96;

interface TimelineProps {
  duration: number;
  time: number;
  playing?: boolean;
  onSeek: (t: number) => void;
  /** 1 = the whole clip fits the width; larger zooms in */
  zoom?: number;
  children: ReactNode;
  className?: string;
}

/** Ruler + playhead + stacked tracks. Click or drag anywhere on the ruler / a track to seek. Tracks are <Track> children. */
export function Timeline({ duration, time, playing, onSeek, zoom = 1, children, className }: TimelineProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(200, el.clientWidth - LABEL_W));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pps = (width / Math.max(duration, 0.1)) * Math.max(1, zoom);
  const total = Math.max(width, duration * pps);

  const timeFromX = (clientX: number) => {
    const r = body.current!.getBoundingClientRect();
    return clamp((clientX - r.left) / pps, 0, duration);
  };

  const dragging = useRef(false);
  const onDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("[data-no-seek]")) return;
    dragging.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    onSeek(timeFromX(e.clientX));
  };
  const onMove = (e: React.PointerEvent) => {
    if (dragging.current) onSeek(timeFromX(e.clientX));
  };
  const onUp = () => {
    dragging.current = false;
  };

  // keep the playhead in view while playing / zoomed
  useEffect(() => {
    const el = scroller.current;
    if (!el || (!playing && !dragging.current)) return;
    const x = time * pps;
    if (x < el.scrollLeft || x > el.scrollLeft + width - 40) el.scrollLeft = Math.max(0, x - width * 0.3);
  }, [time, playing, pps, width]);

  // ruler: a tick about every 70 px, on a round number of seconds
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
  const step = steps.find((s) => s * pps >= 70) ?? 600;
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 1e-6; t += step) ticks.push(t);

  return (
    <TimelineCtx.Provider value={{ pps, duration }}>
      <div ref={scroller} className={cn("relative overflow-x-auto overflow-y-hidden rounded-lg border bg-card", className)}>
        <div style={{ width: LABEL_W + total }} className="relative">
          {/* ruler */}
          <div className="flex h-8 border-b">
            <div data-no-seek style={{ width: LABEL_W }} className="sticky left-0 z-20 shrink-0 border-r bg-card px-3 py-1.5 text-sm font-semibold tabular text-muted-foreground">
              {fmtPrecise(time)}
            </div>
            <div
              ref={body}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              className="relative flex-1 cursor-col-resize no-select"
              style={{ width: total }}
            >
              {ticks.map((t) => (
                <div key={t} className="absolute top-0 h-full border-l border-border pl-1.5 pt-1.5 text-sm tabular text-muted-foreground" style={{ left: t * pps }}>
                  {fmtTime(t)}
                </div>
              ))}
            </div>
          </div>
          {/* tracks */}
          <div
            className="relative cursor-col-resize no-select"
            onPointerDown={(e) => {
              // tracks share the ruler's x-axis; map through the ruler's box
              if ((e.target as HTMLElement).closest("[data-no-seek]")) return;
              dragging.current = true;
              (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
              onSeek(timeFromX(e.clientX));
            }}
            onPointerMove={onMove}
            onPointerUp={onUp}
          >
            {children}
          </div>
          {/* playhead */}
          <div className="pointer-events-none absolute bottom-0 top-0 z-10" style={{ left: LABEL_W + time * pps }}>
            <div className="absolute -left-1.5 top-0 h-3 w-3 rotate-45 rounded-[2px] bg-primary" />
            <div className="h-full w-px bg-primary" />
          </div>
        </div>
      </div>
    </TimelineCtx.Provider>
  );
}

export function Track({ label, icon, height = 44, children, className }: { label: string; icon?: ReactNode; height?: number; children?: ReactNode; className?: string }) {
  const { duration, pps } = useTimeline();
  return (
    <div className="flex border-b last:border-b-0" style={{ height }}>
      <div data-no-seek style={{ width: LABEL_W }} className="sticky left-0 z-20 flex shrink-0 items-center gap-1.5 border-r bg-card px-3 text-sm font-semibold text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className={cn("relative", className)} style={{ width: Math.max(duration * pps, 1) }}>
        {children}
      </div>
    </div>
  );
}

/** A block on a track, positioned by seconds. Set `interactive` for blocks with their own click (they won't start a scrub). */
export function Block({
  start,
  end,
  className,
  children,
  title,
  onClick,
  inset = 4,
  style,
}: {
  start: number;
  end: number;
  className?: string;
  children?: ReactNode;
  title?: string;
  onClick?: () => void;
  inset?: number;
  style?: React.CSSProperties;
}) {
  const { pps } = useTimeline();
  return (
    <div
      title={title}
      data-no-seek={onClick ? "" : undefined}
      onClick={onClick}
      className={cn("absolute overflow-hidden rounded-md", onClick && "cursor-pointer", className)}
      style={{ left: start * pps, width: Math.max(2, (end - start) * pps - 1), top: inset, bottom: inset, ...style }}
    >
      {children}
    </div>
  );
}
