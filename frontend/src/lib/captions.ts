/**
 * Caption styles for the Director: the preset values from the server, the overrides of the Captions panel,
 * and a canvas drawing of them that follows the export's renderer (editor.caption_state / _draw_caption).
 */
import type { Word } from "@/lib/types";

export type HiMode = "color" | "box" | "none";
export type Enter = "pop" | "fade" | "none";
export type Motion = "none" | "bounce" | "stagger" | "zoom";

/** a full caption style; sizes are at 1080 px wide (subtitles.PRESETS) */
export interface CaptionStyle {
  per: number;
  size: number;
  upper: boolean;
  color: string;
  hi: string;
  hi_mode: HiMode;
  hi_bg: string;
  kw?: string;
  stroke: string;
  stroke_w: number;
  bg: string;
  bg_op: number;
  pop: boolean;
  motion?: Motion;
  enter?: Enter;
  /** centre of the caption block, share of the height (absent = the layout's safe band) */
  y?: number;
  /** centre of the caption block, share of the width (absent = the middle) */
  x?: number;
  /** width of the caption block, share of the width (absent = 0.84) */
  w?: number;
  /** a caption font key (absent = the brand / default font) */
  font?: string;
}

/** what the Captions panel changes on top of the preset (ShortOptions.caption_style) */
export type StyleOverrides = Partial<Omit<CaptionStyle, "pop">>;

export const FALLBACK: CaptionStyle = {
  per: 3, size: 84, upper: true, color: "#ffffff", hi: "#ffd21f", hi_mode: "color", hi_bg: "#0865ff",
  stroke: "#000000", stroke_w: 7, bg: "#000000", bg_op: 0, pop: true, motion: "none",
};

export const ENTERS: { key: Enter; label: string }[] = [
  { key: "pop", label: "Pop" }, { key: "fade", label: "Fade" }, { key: "none", label: "Cut" },
];
export const MOTIONS: { key: Motion; label: string; hint: string }[] = [
  { key: "none", label: "None", hint: "Words sit still; only the colour moves" },
  { key: "bounce", label: "Bounce", hint: "The spoken word hops" },
  { key: "zoom", label: "Zoom", hint: "The spoken word grows" },
  { key: "stagger", label: "Word by word", hint: "Words appear as they are said" },
];
export const HI_MODES: { key: HiMode; label: string }[] = [
  { key: "color", label: "Colour" }, { key: "box", label: "Box" }, { key: "none", label: "Off" },
];

/** the preset with the panel's overrides on top */
export function resolveStyle(preset: Partial<CaptionStyle> | undefined, o: StyleOverrides | undefined): CaptionStyle {
  const st: CaptionStyle = { ...FALLBACK, ...(preset || {}) };
  if (!st.enter) st.enter = st.pop ? "pop" : "none";
  if (!st.motion) st.motion = "none";
  const out = { ...st, ...(o || {}) } as CaptionStyle;
  out.pop = out.enter === "pop";
  return out;
}

/** the same band as subtitles.safe_band (9:16), as a share of the height */
export function bandY(drawn: string, ratio: number): number {
  return drawn === "split" ? ratio : drawn === "split_bottom" ? 1 - ratio - 0.1 : 0.68;
}

/** the caption block's box on a W x H canvas, as subtitles.captions_layer places it (centred on cy) */
export function captionBox(st: Pick<CaptionStyle, "x" | "w" | "size">, W: number, H: number, cy: number) {
  const cx = W * (st.x ?? 0.5);
  const w = Math.min(W * (st.w ?? 0.84), 2 * Math.min(cx, W - cx));
  const h = Math.max(H * 0.16, st.size * (W / 1080) * 2.6);
  return { x: cx - w / 2, y: cy - h / 2, w, h };
}

export interface Chunk { words: Word[] }

/** captions.chunk_words with the sizes subtitles.build_chunks uses */
export function chunkWords(words: Word[], per: number): Chunk[] {
  const maxChars = Math.max(10, per * 9);
  const out: Chunk[] = [];
  let cur: Word[] = [];
  for (const w of words) {
    if (cur.length) {
      const last = cur[cur.length - 1];
      const gap = w.s - last.e;
      const chars = cur.reduce((a, x) => a + x.w.length + 1, 0) + w.w.length;
      const ends = /[.?!]$/.test(last.w);
      if (cur.length >= per || chars > maxChars || gap > 0.6 || ends) {
        out.push({ words: cur });
        cur = [];
      }
    }
    cur.push(w);
  }
  if (cur.length) out.push({ words: cur });
  return out;
}

const POP = 0.2;
const WORD = 0.22;
const GAP = 0.6;
const TAIL = 0.3;
const backOut = (u: number) => 1 + 2.70158 * (u - 1) ** 3 + 1.70158 * (u - 1) ** 2;

/** (chunk, word, scale, opacity, word phase) at time t, or null: editor.caption_state */
export function captionState(chunks: Chunk[], t: number, st: CaptionStyle) {
  let i = -1;
  for (let k = 0; k < chunks.length; k++) {
    if (chunks[k].words[0].s <= t) i = k;
    else break;
  }
  if (i < 0) return null;
  const c = chunks[i];
  const cs = c.words[0].s;
  const last = c.words[c.words.length - 1].e;
  const nxt = i + 1 < chunks.length ? chunks[i + 1].words[0].s : Infinity;
  const ce = nxt - last < GAP ? nxt : Math.min(nxt, last + TAIL);
  if (t >= ce) return null;
  let j = 0;
  c.words.forEach((w, k) => {
    if (w.s <= t) j = k;
  });
  const wp = st.motion && st.motion !== "none" ? Math.min(1, Math.max(0, (t - c.words[j].s) / WORD)) : 1;
  let sc = 1;
  let op = 1;
  const u = (t - cs) / POP;
  if (u < 1 && st.enter === "pop") {
    sc = 0.7 + 0.3 * backOut(u);
    op = Math.min(1, u * 2.5);
  } else if (u < 1 && st.enter === "fade") op = Math.max(0, u);
  return { i, j, sc, op, wp };
}

/**
 * Draw the captions at time t on a canvas W wide (the export's 1080 px maths scaled down).
 * `family`: a loaded CSS font family for the chosen font.
 */
export function drawCaptions(ctx: CanvasRenderingContext2D, chunks: Chunk[], t: number, st: CaptionStyle, W: number, H: number, cy: number, family: string) {
  const state = captionState(chunks, t, st);
  if (!state) return;
  const { i, j, sc, op, wp } = state;
  const k1080 = W / 1080;
  const size = st.size * k1080;
  const font = (px: number) => `${px}px ${family}`;
  ctx.save();
  ctx.font = font(size);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.lineJoin = "round";
  const words = chunks[i].words.map((w) => (st.upper ? w.w.toUpperCase() : w.w));
  const widths = words.map((w) => ctx.measureText(w).width);
  const space = ctx.measureText(" ").width;
  const box = captionBox(st, W, H, cy);
  const boxX = box.x;
  const boxW = box.w;
  const maxW = boxW - 2 * size * 0.3;
  const lines: number[] = [];
  let cur = 0;
  let cw = 0;
  for (const wd of widths) {
    const add = cur === 0 ? wd : cw + space + wd;
    if (cur && add > maxW) {
      lines.push(cur);
      cur = 1;
      cw = wd;
    } else {
      cur += 1;
      cw = add;
    }
  }
  if (cur) lines.push(cur);
  const lh = size * 1.2;
  const block = lines.length * lh;
  const top = cy - block / 2;
  const items: { w: string; x: number; base: number; ww: number; lt: number }[] = [];
  let n0 = 0;
  lines.forEach((n, li) => {
    const lw = widths.slice(n0, n0 + n).reduce((a, b) => a + b, 0) + space * (n - 1);
    let x = boxX + (boxW - lw) / 2;
    for (let k = 0; k < n; k++) {
      items.push({ w: words[n0 + k], x, base: top + li * lh + size * 0.97, ww: widths[n0 + k], lt: top + li * lh });
      x += widths[n0 + k] + space;
    }
    n0 += n;
  });
  const cx = boxX + boxW / 2;
  const ccy = top + block / 2;
  ctx.globalAlpha = op;
  ctx.translate(cx, ccy);
  ctx.scale(sc, sc);
  ctx.translate(-cx, -ccy);
  if (st.bg_op > 0) {
    const pad = size * 0.3;
    const x0 = Math.min(...items.map((it) => it.x)) - pad;
    const x1 = Math.max(...items.map((it) => it.x + it.ww)) + pad;
    ctx.fillStyle = hexA(st.bg, st.bg_op);
    ctx.beginPath();
    ctx.roundRect(x0, top - pad * 0.5, x1 - x0, block + pad, size * 0.25);
    ctx.fill();
  }
  if (st.hi_mode === "box" && items[j]) {
    const it = items[j];
    ctx.fillStyle = st.hi_bg;
    ctx.beginPath();
    ctx.roundRect(it.x - size * 0.12, it.lt + size * 0.1, it.ww + size * 0.24, size * 1.06, size * 0.18);
    ctx.fill();
  }
  const sw = st.stroke_w * k1080;
  const drawWord = (pass: "stroke" | "fill") => {
    items.forEach((it, k) => {
      if (st.motion === "stagger" && k > j) return;
      let wsc = 1;
      let lift = 0;
      if (k === j && st.motion === "bounce") {
        wsc = 1 + 0.22 * Math.sin(Math.PI * wp);
        lift = size * 0.16 * Math.sin(Math.PI * wp);
      } else if (k === j && st.motion === "stagger") wsc = 0.55 + 0.45 * backOut(wp);
      else if (k === j && st.motion === "zoom") wsc = 1 + 0.18 * backOut(wp);
      ctx.font = font(size * wsc);
      let x = it.x;
      if (wsc !== 1) {
        ctx.textAlign = "center";
        x = it.x + it.ww / 2;
      } else ctx.textAlign = "left";
      const y = it.base - lift;
      if (pass === "stroke") {
        if (sw > 0) {
          ctx.lineWidth = sw * wsc * 2;
          ctx.strokeStyle = st.stroke;
          ctx.strokeText(it.w, x, y);
        }
      } else {
        ctx.fillStyle = k === j && st.hi_mode === "color" ? st.hi : st.color;
        ctx.fillText(it.w, x, y);
      }
    });
  };
  drawWord("stroke");
  drawWord("fill");
  ctx.restore();
}

function hexA(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

const loaded = new Map<string, Promise<string>>();
/** load a caption font for the canvas; resolves to its CSS family (or a fallback when it can't load) */
export function loadCaptionFont(key: string): Promise<string> {
  const fallback = "'Arial Black', Arial, sans-serif";
  if (!loaded.has(key)) {
    const family = `capfont-${key}`;
    const face = new FontFace(family, `url(/api/studio/caption-font/${key})`);
    loaded.set(
      key,
      face
        .load()
        .then((f) => {
          document.fonts.add(f);
          return `'${family}', ${fallback}`;
        })
        .catch(() => fallback),
    );
  }
  return loaded.get(key)!;
}
