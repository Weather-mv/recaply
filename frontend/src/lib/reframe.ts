/**
 * Real-time 9:16 preview maths. The Director draws each video frame into a vertical canvas using the same
 * layout names the exporter understands (recaply/render/reframe.py STRATEGIES). `planFrame` returns what to
 * draw; it is pure so it can be tested. Face tracking and the camera position are detected on export, so
 * "face" previews as a centred crop and "split" assumes a corner facecam; the UI says so.
 */

export type Layout = "auto" | "face" | "split" | "action" | "center" | "fit";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Draw {
  src: Rect; // in source pixels
  dst: Rect; // in output pixels
  blur?: boolean; // the blurred backdrop of "fit"
}

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

export const SPLIT_TOP = 0.38; // share of the height the camera gets in "split"

export function planFrame(layout: Layout, srcW: number, srcH: number, outW: number, outH: number): Draw[] {
  const full: Rect = { x: 0, y: 0, w: outW, h: outH };
  switch (layout) {
    case "fit":
      return [
        { src: coverCrop(srcW, srcH, outW, outH), dst: full, blur: true },
        { src: { x: 0, y: 0, w: srcW, h: srcH }, dst: containRect(srcW, srcH, outW, outH) },
      ];
    case "split": {
      const topH = Math.round(outH * SPLIT_TOP);
      const top: Rect = { x: 0, y: 0, w: outW, h: topH };
      const bottom: Rect = { x: 0, y: topH, w: outW, h: outH - topH };
      // the camera: the lower-right quarter of the frame, scaled to the top pane
      const quarter: Rect = { x: srcW / 2, y: srcH / 2, w: srcW / 2, h: srcH / 2 };
      const cam = coverCrop(quarter.w, quarter.h, top.w, top.h);
      return [
        { src: { x: quarter.x + cam.x, y: quarter.y + cam.y, w: cam.w, h: cam.h }, dst: top },
        { src: coverCrop(srcW, srcH, bottom.w, bottom.h), dst: bottom },
      ];
    }
    default: // auto, face, action, center
      return [{ src: coverCrop(srcW, srcH, outW, outH), dst: full }];
  }
}

export const LAYOUT_NOTES: Record<Layout, string> = {
  auto: "Picked per clip on export.",
  face: "Follows the speaker on export; the preview is centred.",
  split: "Camera on top, gameplay below. The camera spot is detected on export.",
  action: "Crops to the action on export; the preview is centred.",
  center: "A plain centre crop.",
  fit: "The whole frame over a blurred copy of itself.",
};
