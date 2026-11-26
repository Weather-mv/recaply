/**
 * Real-time preview maths. The Director draws each video frame into the output canvas with the same layouts
 * (and the same crop maths) as the exporter, recaply/render/reframe.py. `planFrame` is pure so it can be tested.
 *
 * What's on screen (the facecam box, the gameplay area, the face path, the action) is measured once per cut
 * by GET /api/clipjob/<id>/clip/<cid>/layout and passed in as `info`; without it the preview falls back to
 * centred crops and a guessed corner cam.
 */

export type Layout = "auto" | "split" | "split_bottom" | "pip" | "face" | "action" | "center" | "fit";
export const CAM_LAYOUTS: Layout[] = ["split", "split_bottom", "pip"];

/** [x, y, w, h] fractions of the source frame */
export type FracRect = [number, number, number, number];

export interface LayoutInfo {
  cam: FracRect | null;
  cam_face: FracRect | null;
  game: FracRect;
  /** [top, bottom] of the picture without burned-in letterbox bars */
  rows?: [number, number];
  face_keys: [number, number][];
  face_seen: number;
  action_cx: number;
  auto: Layout;
  w: number;
  h: number;
}

export interface LayoutOpts {
  /** the cam pane's share of the height (split) or its size (pip) */
  ratio: number;
  /** a cam box drawn by hand, overrides the detected one */
  camBox: FracRect | null;
  /** seconds into the clip (face layout pans over time) */
  time: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Draw {
  src: Rect; // in source pixels
  dst: Rect; // in output pixels
  blur?: boolean; // the blurred backdrop
  frame?: boolean; // draw a thin border (the pip cam)
}

export const SPLIT_RATIO = 0.42;

/** The part of a w x h source that fills an out-aspect box exactly (CSS object-fit: cover), panned by panX/panY in 0..1. */
export function coverCrop(srcW: number, srcH: number, outW: number, outH: number, panX = 0.5, panY = 0.5): Rect {
  const outAspect = outW / outH;
  const srcAspect = srcW / srcH;
  if (srcAspect > outAspect) {
    const w = srcH * outAspect;
    return { x: (srcW - w) * panX, y: 0, w, h: srcH };
  }
  const h = srcW / outAspect;
  return { x: 0, y: (srcH - h) * panY, w: srcW, h };
}

/** Where a w x h source sits, whole and centred, inside an out box (object-fit: contain). */
export function containRect(srcW: number, srcH: number, outW: number, outH: number): Rect {
  const scale = Math.min(outW / srcW, outH / srcH);
  const w = srcW * scale;
  const h = srcH * scale;
  return { x: (outW - w) / 2, y: (outH - h) / 2, w, h };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** reframe.py _crop_inside: the largest crop of `rect` with output aspect `aspect`, centred on the face if given. */
export function cropInside(rect: FracRect, aspect: number, face: FracRect | null, sw: number, sh: number): FracRect {
  const [x, y, w, h] = rect;
  let cw = w;
  let ch = h;
  if ((w * sw) / (h * sh) > aspect) cw = (h * sh * aspect) / sw;
  else ch = (w * sw) / aspect / sh;
  const fcx = face ? face[0] + face[2] / 2 : x + w / 2;
  const fcy = face ? face[1] + face[3] * 0.9 : y + h / 2;
  return [clamp(fcx - cw / 2, x, x + w - cw), clamp(fcy - ch / 2, y, y + h - ch), cw, ch];
}

/** reframe.py _crop_avoiding: a crop of `rect` that overlaps the cam as little as possible. */
export function cropAvoiding(rect: FracRect, aspect: number, avoid: FracRect | null, sw: number, sh: number): FracRect {
  const base = cropInside(rect, aspect, null, sw, sh);
  if (!avoid) return base;
  const [x, y, w, h] = rect;
  const ov = (a: FracRect, b: FracRect) => {
    const ow = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const oh = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return ow > 0 && oh > 0 ? ow * oh : 0;
  };
  const horiz = base[2] < w - 1e-4;
  let best = base;
  let bestO = ov(base, avoid);
  let bestD = 0;
  for (let i = 0; i <= 40; i++) {
    const c: FracRect = [...base];
    if (horiz) c[0] = x + ((w - c[2]) * i) / 40;
    else c[1] = y + ((h - c[3]) * i) / 40;
    const o = ov(c, avoid);
    const d = horiz ? Math.abs(c[0] - base[0]) : Math.abs(c[1] - base[1]);
    if (o < bestO - 1e-6 || (Math.abs(o - bestO) < 1e-6 && d < bestD)) {
      best = c;
      bestO = o;
      bestD = d;
    }
  }
  return best;
}

/** reframe.py pip_box: the cam box in "pip", centred near the top. */
export function pipBox(cam: FracRect, ratio: number, sw: number, sh: number, W: number, H: number): Rect {
  let bw = W * Math.min(0.8, ratio + 0.2);
  const aspect = (cam[2] * sw) / Math.max(1e-6, cam[3] * sh);
  let bh = bw / aspect;
  if (bh > H * 0.4) {
    bh = H * 0.4;
    bw = bh * aspect;
  }
  return { x: (W - bw) / 2, y: H * 0.07, w: bw, h: bh };
}

/** The face camera's x centre (fraction) at time t, eased between keyframes like the export. */
export function faceX(keys: [number, number][], t: number): number {
  if (!keys.length) return 0.5;
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t1, x1] = keys[i];
    if (t <= t1) {
      const [t0, x0] = keys[i - 1];
      const k = (t - t0) / Math.max(1e-6, t1 - t0);
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      return x0 + (x1 - x0) * e;
    }
  }
  return keys[keys.length - 1][1];
}

/** Without a measurement: a corner cam guessed in the top-right, where most streamers put it. */
const GUESS_CAM: FracRect = [0.72, 0.04, 0.26, 0.3];

/** The layout actually drawn: "auto" resolves to what the export will pick for this clip. */
export function resolveLayout(layout: Layout, info: LayoutInfo | null, camBox: FracRect | null): Layout {
  if (layout !== "auto") return layout;
  if (camBox) return "split";
  return info?.auto ?? "fit";
}

const px = (r: FracRect, sw: number, sh: number): Rect => ({ x: r[0] * sw, y: r[1] * sh, w: r[2] * sw, h: r[3] * sh });

export function planFrame(layout: Layout, srcW: number, srcH: number, outW: number, outH: number,
  info: LayoutInfo | null = null, opts: LayoutOpts = { ratio: SPLIT_RATIO, camBox: null, time: 0 }): Draw[] {
  const full: Rect = { x: 0, y: 0, w: outW, h: outH };
  const backdrop: Draw = { src: coverCrop(srcW, srcH, outW, outH), dst: full, blur: true };
  const lay = resolveLayout(layout, info, opts.camBox);
  const cam = opts.camBox ?? info?.cam ?? GUESS_CAM;
  const camFace = opts.camBox ? null : info?.cam_face ?? null;
  const game: FracRect = opts.camBox ? [0, 0, 1, 1] : info?.game ?? [0, 0, 1, 1];
  const ratio = clamp(opts.ratio || SPLIT_RATIO, 0.25, 0.6);
  // full-height crops (face, action, centre) leave out the letterbox bars, like the export
  const [top, bottom] = info?.rows ?? [0, 1];
  const picH = srcH * Math.max(0.05, bottom - top);
  const stripShare = Math.min(1, (outW / outH) / (srcW / picH));
  const panFor = (cx: number) => (stripShare >= 1 ? 0.5 : clamp((cx - stripShare / 2) / (1 - stripShare), 0, 1));
  const pictureCrop = (pan: number): Rect => {
    const r = coverCrop(srcW, picH, outW, outH, pan);
    return { ...r, y: r.y + top * srcH };
  };

  switch (lay) {
    case "fit":
      return [backdrop, { src: { x: 0, y: 0, w: srcW, h: srcH }, dst: containRect(srcW, srcH, outW, outH) }];
    case "split":
    case "split_bottom": {
      const ch = Math.round(outH * ratio);
      const gh = outH - ch;
      const camCrop = cropInside(cam, outW / ch, camFace, srcW, srcH);
      const gameCrop = cropAvoiding(game, outW / gh, cam, srcW, srcH);
      const [camY, gameY] = lay === "split" ? [0, ch] : [gh, 0];
      return [
        backdrop,
        { src: px(gameCrop, srcW, srcH), dst: { x: 0, y: gameY, w: outW, h: gh } },
        { src: px(camCrop, srcW, srcH), dst: { x: 0, y: camY, w: outW, h: ch } },
      ];
    }
    case "pip": {
      const base = cropInside(game, outW / outH, null, srcW, srcH);
      const cx = info?.action_cx ?? 0.5;
      base[0] = clamp(cx - base[2] / 2, game[0], game[0] + game[2] - base[2]);
      return [
        backdrop,
        { src: px(base, srcW, srcH), dst: full },
        { src: px(cam, srcW, srcH), dst: pipBox(cam, ratio, srcW, srcH, outW, outH), frame: true },
      ];
    }
    case "face":
      return [{ src: pictureCrop(panFor(faceX(info?.face_keys ?? [], opts.time))), dst: full }];
    case "action":
      return [{ src: pictureCrop(panFor(info?.action_cx ?? 0.5)), dst: full }];
    default: // center
      return [{ src: pictureCrop(0.5), dst: full }];
  }
}

export const LAYOUT_META: Record<Layout, { label: string; hint: string }> = {
  auto: { label: "Auto", hint: "Finds the facecam; else follows the face or action" },
  split: { label: "Cam on top", hint: "Facecam above, gameplay below" },
  split_bottom: { label: "Cam below", hint: "Gameplay above, facecam below" },
  pip: { label: "Cam box", hint: "Full gameplay, facecam in a box" },
  face: { label: "Follow face", hint: "The camera pans with the speaker" },
  action: { label: "Follow action", hint: "Crops to where things move" },
  center: { label: "Centre", hint: "A fixed centre crop" },
  fit: { label: "Whole frame", hint: "All of it, blurred sides" },
};

export const LAYOUTS = Object.keys(LAYOUT_META) as Layout[];
