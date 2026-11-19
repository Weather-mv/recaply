/* Clipforge editor — the page side of editor.py.
 *
 * The preview canvas draws the same document the server renders, with the same
 * geometry, font file, text wrapping, keyframe interpolation, in/out
 * animations and caption timing (the "mirror" sections below match editor.py
 * line for line), so what you see is what you export.
 */
(() => {
  "use strict";
  const CFG = window.EDITOR_CFG, PID = CFG.pid, FORMATS = CFG.formats, C = CFG.consts, HAS_FONT = CFG.hasFont;
  const FONT = '"RecaplyFont", Arial, sans-serif';
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const r2 = v => Math.round(v * 100) / 100;
  const r3 = v => Math.round(v * 1000) / 1000;
  const r4 = v => Math.round(v * 10000) / 10000;
  const tfmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return `${m}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
  const tparse = s => { const p = String(s).trim().split(":").map(Number); if (!p.length || p.some(isNaN)) return null; return p.reduce((a, b) => a * 60 + b, 0); };
  const uid = () => "l" + Math.random().toString(36).slice(2, 9);
  const svg = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  const ICON = {
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/></svg>',
    sound: svg('<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>'),
    muted: svg('<path d="M11 5 6 9H2v6h4l5 4z"/><path d="m22 9-6 6M16 9l6 6"/>'),
    eye: svg('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
    eyeOff: svg('<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
    lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
    unlock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>'),
    grip: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    twirl: svg('<path d="m9 6 6 6-6 6"/>'),
    watch: svg('<circle cx="12" cy="14" r="7"/><path d="M12 10v4l2 2M10 2h4M12 2v3"/>'),
    diamond: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3 21 12 12 21 3 12z"/></svg>',
    diamondO: svg('<path d="M12 3 21 12 12 21 3 12z"/>'),
    prev: svg('<path d="m15 6-6 6 6 6"/>'), next: svg('<path d="m9 6 6 6-6 6"/>'),
    up: svg('<path d="m6 15 6-6 6 6"/>'), down: svg('<path d="m6 9 6 6 6-6"/>'),
    copy: svg('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>'),
    trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
    close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    here: svg('<path d="M12 3v18M8 7l4-4 4 4"/>'),
    video: svg('<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="m10 9 5 3-5 3z"/>'),
    text: svg('<path d="M5 6V4h14v2M12 4v16M9 20h6"/>'),
    rect: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/></svg>',
    ellipse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"/></svg>',
    line: svg('<path d="M4 19 20 5"/>'),
    arrow: svg('<path d="M4 20 19 5M10 5h9v9"/>'),
    image: svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/>'),
    captions: svg('<rect x="2.5" y="5" width="19" height="14" rx="3"/><path d="M7 11h3M12 11h5M7 15h7M16 15h1"/>'),
    audio: svg('<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>'),
  };
  const KIND_NAMES = { rect: "Box", ellipse: "Circle", line: "Line", arrow: "Arrow" };
  const TYPE_COLOR = { video: "#56657d", text: "#8c6d47", shape: "#4b7872", image: "#6a5a8a", captions: "#b0561d" };
  const PROPS = { pos: "Position", sc: "Scale", rot: "Rotation", op: "Opacity" };
  const EASES = [["linear", "Linear"], ["ease", "Ease"], ["in", "Ease in"], ["out", "Ease out"], ["hold", "Hold"]];
  const ANIM_NAMES = [["none", "None"], ["fade", "Fade"], ["slide_up", "Slide up"], ["slide_down", "Slide down"],
                      ["slide_left", "Slide left"], ["slide_right", "Slide right"], ["pop", "Pop"], ["zoom", "Zoom"]];
  const CAP_STYLES = {
    bold: { label: "Bold", per: 3, size: 84, upper: true, color: "#ffffff", hi: "#ffd21f", hi_mode: "color", hi_bg: "#0865ff", stroke: "#000000", stroke_w: 7, bg: "#000000", bg_op: 0, pop: true },
    box: { label: "Box", per: 2, size: 80, upper: true, color: "#ffffff", hi: "#ffffff", hi_mode: "box", hi_bg: "#0865ff", stroke: "#000000", stroke_w: 0, bg: "#000000", bg_op: 0, pop: true },
    single: { label: "One word", per: 1, size: 110, upper: true, color: "#ffffff", hi: "#ffffff", hi_mode: "none", hi_bg: "#0865ff", stroke: "#000000", stroke_w: 9, bg: "#000000", bg_op: 0, pop: true },
    neon: { label: "Neon", per: 3, size: 80, upper: true, color: "#ffffff", hi: "#3dff8a", hi_mode: "color", hi_bg: "#0865ff", stroke: "#0a0a0a", stroke_w: 8, bg: "#000000", bg_op: 0, pop: true },
    clean: { label: "Clean", per: 5, size: 62, upper: false, color: "#ffffff", hi: "#ffd21f", hi_mode: "color", hi_bg: "#0865ff", stroke: "#000000", stroke_w: 4, bg: "#000000", bg_op: 0, pop: false },
    subtitle: { label: "Subtitle", per: 7, size: 52, upper: false, color: "#ffffff", hi: "#ffffff", hi_mode: "none", hi_bg: "#0865ff", stroke: "#000000", stroke_w: 0, bg: "#000000", bg_op: 0.62, pop: false },
  };
  const LANGS = [["", "Detect automatically"], ["en", "English"], ["hi", "Hindi"], ["es", "Spanish"], ["pt", "Portuguese"], ["fr", "French"],
                 ["de", "German"], ["it", "Italian"], ["tr", "Turkish"], ["ru", "Russian"], ["ar", "Arabic"], ["ja", "Japanese"], ["ko", "Korean"],
                 ["zh", "Chinese"], ["id", "Indonesian"], ["vi", "Vietnamese"], ["th", "Thai"], ["pl", "Polish"], ["nl", "Dutch"], ["tl", "Filipino"],
                 ["ur", "Urdu"], ["bn", "Bengali"], ["ta", "Tamil"], ["te", "Telugu"], ["mr", "Marathi"]];
  const capture = (node, e) => { try { node.setPointerCapture(e.pointerId); } catch (_) { /* synthetic or already released */ } };
  const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k) || "null"); return v ?? d; } catch (_) { return d; } };
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };

  // ---------- state ----------
  let P = null, meta = null, doc = null, sel = null, selKey = null, T = 0, playing = false, userMuted = false, looping = true;
  let hist = [], hi = -1, saveTimer = null, dirty = false, peaks = [], asrApplied = false;
  const expanded = new Set();
  const images = {};
  const vid = document.createElement("video");
  vid.preload = "auto"; vid.playsInline = true;
  const cv = $("cv"), ctx = cv.getContext("2d");
  let k = 1, dpr = 1;

  const WH = () => FORMATS[doc.format];
  const Wd = () => WH()[0], Hd = () => WH()[1];
  const D = () => Math.max(0.1, (doc.trim[1] - doc.trim[0]) / doc.speed);
  const layerById = id => doc.layers.find(L => L.id === id);
  const selL = () => sel ? layerById(sel) : null;
  const isLine = L => L.type === "shape" && (L.kind === "line" || L.kind === "arrow");
  const layerEnd = L => L.end == null ? D() : Math.min(D(), L.end);
  const activeAt = (L, t) => !L.hidden && t >= L.start - 1e-6 && t <= layerEnd(L) + 1e-6;
  const outT = s => (s - doc.trim[0]) / doc.speed;
  const layerName = L => L.name || (L.type === "text" ? (L.text || "Text").split("\n")[0].slice(0, 30) :
                          L.type === "shape" ? KIND_NAMES[L.kind] : L.type === "image" ? "Image" : L.type === "captions" ? "Captions" : "Video");
  const iconOf = L => ICON[L.type === "shape" ? L.kind : L.type];
  const propsFor = L => L.type === "captions" ? [] : L.type === "video" ? ["pos", "op"] : ["pos", "sc", "rot", "op"];

  // ================= animation (mirrors editor.py) =================
  function ease(kind, u) {
    if (kind === "linear") return u;
    if (kind === "in") return u ** 3;
    if (kind === "out") return 1 - (1 - u) ** 3;
    if (kind === "hold") return 0;
    return u < 0.5 ? 4 * u ** 3 : 1 - (-2 * u + 2) ** 3 / 2;
  }
  const backOut = u => { const c1 = 1.70158; return 1 + (c1 + 1) * (u - 1) ** 3 + c1 * (u - 1) ** 2; };
  function kfValue(keys, t) {
    const n = keys[0].length - 2;
    if (t <= keys[0][0]) return keys[0].slice(1, 1 + n);
    const last = keys[keys.length - 1];
    if (t >= last[0]) return last.slice(1, 1 + n);
    for (let i = 0; i < keys.length - 1; i++) {
      const a = keys[i], b = keys[i + 1];
      if (a[0] <= t && t < b[0]) {
        const e = ease(a[a.length - 1], (t - a[0]) / (b[0] - a[0]));
        return a.slice(1, 1 + n).map((va, j) => va + (b[1 + j] - va) * e);
      }
    }
    return last.slice(1, 1 + n);
  }
  const isAnimated = L => (L.kf && Object.keys(L.kf).some(p => (L.kf[p] || []).length)) ||
                          ["anim_in", "anim_out"].some(key => L[key] && L[key].type !== "none");
  function pose(L, t) {
    if (L.type === "captions") return L;
    const W = Wd(), H = Hd(), kf = L.kf || {};
    let [x, y] = kf.pos && kf.pos.length ? kfValue(kf.pos, t) : [L.x, L.y];
    let sc = kf.sc && kf.sc.length ? kfValue(kf.sc, t)[0] : 1;
    const rot = kf.rot && kf.rot.length ? kfValue(kf.rot, t)[0] : (L.rot || 0);
    let op = kf.op && kf.op.length ? kfValue(kf.op, t)[0] : 1;
    const slide = 0.1 * Math.min(W, H);
    for (const [key, left] of [["anim_in", t - L.start], ["anim_out", layerEnd(L) - t]]) {
      const a = L[key];
      if (!a || a.type === "none") continue;
      const u = left / a.dur;
      if (u < 0 || u >= 1) continue;
      const p = 1 - (1 - u) ** 3;
      if (a.type === "fade") op *= p;
      else if (a.type.startsWith("slide")) {
        op *= p;
        let [dx, dy] = { slide_up: [0, 1], slide_down: [0, -1], slide_left: [1, 0], slide_right: [-1, 0] }[a.type];
        if (key === "anim_out") { dx = -dx; dy = -dy; }
        x += dx * (1 - p) * slide; y += dy * (1 - p) * slide;
      } else if (a.type === "pop") { sc *= 0.5 + 0.5 * backOut(u); op *= Math.min(1, u * 3); }
      else if (a.type === "zoom") { sc *= 1.3 - 0.3 * p; op *= p; }
    }
    const Q = { ...L, x, y, rot, opacity: L.opacity * clamp(op, 0, 1) };
    if (Math.abs(sc - 1) > 1e-4 && L.type !== "video") {
      const cx = x + L.w / 2, cy = y + L.h / 2;
      Q.w = L.w * sc; Q.h = L.h * sc; Q.x = cx - Q.w / 2; Q.y = cy - Q.h / 2;
      for (const key of ["size", "stroke_w", "radius"]) if (key in Q) Q[key] = Q[key] * sc;
    }
    return Q;
  }

  // ================= captions (mirrors editor.py) =================
  const CAP_POP = 0.2, CAP_GAP = 0.6, CAP_TAIL = 0.3;
  function captionState(L, t) {
    const ch = L.chunks || [];
    let i = -1;
    for (let q = 0; q < ch.length; q++) { if (outT(ch[q].words[0].s) <= t) i = q; else break; }
    if (i < 0) return null;
    const c = ch[i], cs = outT(c.words[0].s), last = outT(c.words[c.words.length - 1].e);
    const nxt = i + 1 < ch.length ? outT(ch[i + 1].words[0].s) : Infinity;
    const ce = nxt - last < CAP_GAP ? nxt : Math.min(nxt, last + CAP_TAIL);
    if (t >= ce) return null;
    let j = 0;
    c.words.forEach((w, q) => { if (outT(w.s) <= t) j = q; });
    let sc = 1, op = 1;
    if (L.pop) { const u = (t - cs) / CAP_POP; if (u < 1) { sc = 0.7 + 0.3 * backOut(u); op = Math.min(1, u * 2.5); } }
    return [i, j, sc, op];
  }
  function captionLines(L, chunk, m) {
    const size = L.size, words = chunk.words.map(w => L.upper ? w.w.toUpperCase() : w.w);
    const widths = words.map(m), space = m(" "), maxw = L.w - 2 * size * 0.3;
    const lines = []; let cur = 0, cw = 0;
    for (const wd of widths) {
      const add = cur === 0 ? wd : cw + space + wd;
      if (cur && add > maxw) { lines.push(cur); cur = 1; cw = wd; } else { cur++; cw = add; }
    }
    if (cur) lines.push(cur);
    return lines;
  }
  function captionLayout(L, chunk, m) {
    const size = L.size, words = chunk.words.map(w => L.upper ? w.w.toUpperCase() : w.w);
    const widths = words.map(m), space = m(" ");
    const lines = chunk.lines && chunk.lines.reduce((a, b) => a + b, 0) === words.length ? chunk.lines : captionLines(L, chunk, m);
    const lh = size * C.LINE_H, block = lines.length * lh, top = L.y + (L.h - block) / 2;
    const items = []; let i = 0;
    lines.forEach((n, li) => {
      const lw = widths.slice(i, i + n).reduce((a, b) => a + b, 0) + space * (n - 1);
      let x = L.x + (L.w - lw) / 2;
      for (let q = 0; q < n; q++) { items.push([words[i + q], x, top + li * lh + size * 0.97, widths[i + q], top + li * lh]); x += widths[i + q] + space; }
      i += n;
    });
    return { items, top, block };
  }
  function relayoutCaptions(L) {
    const m = measurer(L.size);
    for (const c of L.chunks || []) c.lines = captionLines(L, c, m);
  }
  function chunkWords(words, maxWords) {
    const maxChars = maxWords <= 1 ? 40 : maxWords * 7 + 3;
    const chunks = []; let cur = [];
    for (const w of words) {
      if (cur.length) {
        const gap = w.s - cur[cur.length - 1].e;
        const chars = cur.reduce((a, x) => a + x.w.length + 1, 0) + w.w.length;
        const ends = /[.?!]$/.test(cur[cur.length - 1].w);
        if (cur.length >= maxWords || chars > maxChars || gap > 0.6 || ends) { chunks.push({ words: cur }); cur = []; }
      }
      cur.push({ w: w.w, s: w.s, e: w.e, p: w.p ?? 1 });
    }
    if (cur.length) chunks.push({ words: cur });
    return chunks;
  }

  // ================= text =================
  const mctx = document.createElement("canvas").getContext("2d");
  function measurer(size) { const f = `${Math.round(size)}px ${FONT}`; return s => { mctx.font = f; return mctx.measureText(s).width; }; }
  function wrapText(text, width, measure) {
    const lines = [];
    for (const para of text.split("\n")) {
      let cur = "";
      for (let word of para.split(" ")) {
        if (word === "") continue;
        while (measure(word) > width && word.length > 1) {
          if (cur) { lines.push(cur); cur = ""; }
          let n = word.length - 1;
          while (n > 1 && measure(word.slice(0, n)) > width) n--;
          lines.push(word.slice(0, n)); word = word.slice(n);
        }
        if (!cur) cur = word;
        else if (measure(cur + " " + word) <= width) cur += " " + word;
        else { lines.push(cur); cur = word; }
      }
      lines.push(cur);
    }
    return lines;
  }
  function layoutText(L) {
    L.lines = wrapText(L.text || "", L.w - 2 * L.size * C.TEXT_PAD, measurer(L.size));
    L.h = r2(2 * L.size * C.TEXT_PAD + Math.max(1, L.lines.length) * L.size * C.LINE_H);
  }

  // ================= drawing =================
  const tiny = document.createElement("canvas");
  const off = document.createElement("canvas"), offCtx = off.getContext("2d");
  const hex = (h, a = 1) => `rgba(${parseInt(h.slice(1, 3), 16)},${parseInt(h.slice(3, 5), 16)},${parseInt(h.slice(5, 7), 16)},${a})`;
  const vready = () => vid.readyState >= 2 && vid.videoWidth > 0;
  const blurSize = (b, W) => b <= 0 ? W : Math.max(12, Math.round(W / (1 + 0.3 * b) / 2) * 2);
  const colorFilter = () => {
    const c = doc.color;
    return (c.brightness === 1 && c.contrast === 1 && c.saturation === 1) ? "none"
      : `brightness(${c.brightness}) contrast(${c.contrast}) saturate(${c.saturation})`;
  };
  function rrect(g, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  function arrowHead(x1, y1, x2, y2, sw) {
    const dx = x2 - x1, dy = y2 - y1, ln = Math.hypot(dx, dy) || 1, ux = dx / ln, uy = dy / ln;
    const head = Math.min(Math.max(sw * 3.5, 18), ln), half = head * 0.6, bx = x2 - ux * head, by = y2 - uy * head;
    return [[bx, by], [[x2, y2], [bx - uy * half, by + ux * half], [bx + uy * half, by - ux * half]]];
  }
  function drawShape(g, L) {
    const sw = L.stroke_w, stroke = sw > 0 && L.stroke_op > 0;
    if (isLine(L)) {
      if (!stroke) return;
      const x1 = L.x, y1 = L.y, x2 = L.x + L.w, y2 = L.y + L.h;
      g.strokeStyle = g.fillStyle = hex(L.stroke, L.stroke_op); g.lineWidth = sw; g.lineCap = "butt";
      if (L.kind === "arrow") {
        const [[bx, by], tri] = arrowHead(x1, y1, x2, y2, sw);
        if (Math.hypot(bx - x1, by - y1) > 0.5) { g.beginPath(); g.moveTo(x1, y1); g.lineTo(bx, by); g.stroke(); }
        g.beginPath(); g.moveTo(...tri[0]); g.lineTo(...tri[1]); g.lineTo(...tri[2]); g.closePath(); g.fill();
      } else { g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); }
      return;
    }
    const { x, y, w, h } = L, s = stroke ? sw : 0;
    const path = inset => {
      if (L.kind === "ellipse") { g.beginPath(); g.ellipse(x + w / 2, y + h / 2, Math.max(0, w / 2 - inset), Math.max(0, h / 2 - inset), 0, 0, Math.PI * 2); }
      else rrect(g, x + inset, y + inset, w - 2 * inset, h - 2 * inset, Math.min(L.radius, w / 2, h / 2) - inset);
    };
    if (L.fill_op > 0 && w - 2 * s > 0 && h - 2 * s > 0) { path(s); g.fillStyle = hex(L.fill, L.fill_op); g.fill(); }
    if (stroke) { path(s / 2); g.strokeStyle = hex(L.stroke, L.stroke_op); g.lineWidth = s; g.stroke(); }
  }
  function drawText(g, L) {
    const size = L.size, pad = size * C.TEXT_PAD, lh = size * C.LINE_H;
    if (L.bg_op > 0) { rrect(g, L.x, L.y, L.w, L.h, size * 0.2); g.fillStyle = hex(L.bg, L.bg_op); g.fill(); }
    g.font = `${size}px ${FONT}`; g.textBaseline = "alphabetic"; g.textAlign = L.align;
    const ax = L.align === "left" ? L.x + pad : L.align === "right" ? L.x + L.w - pad : L.x + L.w / 2;
    const lines = L.lines || [];
    if (L.stroke_w > 0) {
      g.lineJoin = "round"; g.lineWidth = L.stroke_w * 2; g.strokeStyle = hex(L.stroke);
      lines.forEach((ln, i) => ln && g.strokeText(ln, ax, L.y + pad + i * lh + size * C.BASELINE));
    }
    g.fillStyle = hex(L.color);
    lines.forEach((ln, i) => ln && g.fillText(ln, ax, L.y + pad + i * lh + size * C.BASELINE));
    g.textAlign = "left";
  }
  function drawCaption(g, L, st) {
    const [i, j, sc] = st, size = L.size, chunk = L.chunks[i];
    if (!chunk) return;
    const { items, top, block } = captionLayout(L, chunk, measurer(size));
    const cx = L.x + L.w / 2, cy = top + block / 2;
    g.save(); g.translate(cx, cy); g.scale(sc, sc); g.translate(-cx, -cy);
    if (L.bg_op > 0) {
      const pad = size * 0.3, x0 = Math.min(...items.map(it => it[1])) - pad, x1 = Math.max(...items.map(it => it[1] + it[3])) + pad;
      rrect(g, x0, top - pad * 0.5, x1 - x0, block + pad, size * 0.25); g.fillStyle = hex(L.bg, L.bg_op); g.fill();
    }
    if (L.hi_mode === "box" && items[j]) {
      const [, x, , ww, lt] = items[j];
      rrect(g, x - size * 0.12, lt + size * 0.1, ww + size * 0.24, size * 1.06, size * 0.18); g.fillStyle = L.hi_bg; g.fill();
    }
    g.font = `${size}px ${FONT}`; g.textBaseline = "alphabetic"; g.textAlign = "left";
    if (L.stroke_w > 0) {
      g.lineJoin = "round"; g.lineWidth = L.stroke_w * 2; g.strokeStyle = L.stroke;
      items.forEach(([w, x, base]) => g.strokeText(w, x, base));
    }
    items.forEach(([w, x, base], q) => {
      const kw = chunk.words[q] && chunk.words[q].k;
      g.fillStyle = q === j && L.hi_mode === "color" ? L.hi : kw ? (L.kw || L.hi) : L.color; g.fillText(w, x, base);
    });
    if (chunk.emoji) {               // same place as the export: centred above the caption block
      g.font = `${Math.round(size * 1.1)}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`;
      g.textAlign = "center"; g.textBaseline = "bottom";
      g.fillText(chunk.emoji, cx, top - size * 0.25);
      g.textAlign = "left"; g.textBaseline = "alphabetic";
    }
    g.restore();
  }
  function drawVideoLayer(g, L) {
    if (!vready()) { g.fillStyle = "rgba(255,255,255,.08)"; g.fillRect(L.x, L.y, L.w, L.h); return; }
    const [cx, cy, cw, ch] = L.crop, vw = vid.videoWidth, vh = vid.videoHeight;
    const cf = colorFilter(), b = L.bright ?? 1;
    const filt = [cf === "none" ? "" : cf, Math.abs(b - 1) > 1e-3 ? `brightness(${b})` : ""].filter(Boolean).join(" ") || "none";
    let img = vid, a = [cx * vw, cy * vh, cw * vw, ch * vh];
    g.save(); g.globalAlpha = L.opacity; g.filter = filt;
    if ((L.blur || 0) > 0) {
      // like the export: shrink (blur_size), soften, scale back up
      const f = blurSize(L.blur, Math.max(2, L.w)) / Math.max(2, L.w);
      const tw = Math.max(2, Math.min(Math.round(L.w * f), Math.ceil(L.w * k * dpr))), th = Math.max(2, Math.round(L.h * tw / Math.max(1, L.w)));
      if (tw < L.w * k * dpr) {
        tiny.width = tw; tiny.height = th;
        const t = tiny.getContext("2d"); t.imageSmoothingEnabled = true; t.filter = filt;
        t.drawImage(vid, ...a, 0, 0, tw, th);
        // the export softens the shrunken copy with boxblur=2:1 (about 1.3 of its pixels); same here, in canvas pixels
        img = tiny; a = [0, 0, tw, th]; g.filter = `blur(${(1.3 * L.w * k * dpr / tw).toFixed(2)}px)`; g.imageSmoothingQuality = "high";
      }
    }
    if (L.flip) { g.translate(L.x + L.w, L.y); g.scale(-1, 1); g.drawImage(img, ...a, 0, 0, L.w, L.h); }
    else g.drawImage(img, ...a, L.x, L.y, L.w, L.h);
    g.restore();
  }
  function drawBackground(g, W, H) {
    const bg = doc.bg;
    if (bg.type === "color") { g.fillStyle = bg.color; g.fillRect(0, 0, W, H); return; }
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    if (!vready()) return;
    const vw = vid.videoWidth, vh = vid.videoHeight;
    let sw = vw, sh = vh;
    if (vw / vh > W / H) sw = vh * W / H; else sh = vw * H / W;
    const bw = Math.min(blurSize(bg.blur, W), Math.ceil(W * k * dpr)), bh = Math.max(2, Math.round(bw * H / W));
    tiny.width = bw; tiny.height = bh;
    const t = tiny.getContext("2d");
    t.imageSmoothingEnabled = true; t.filter = colorFilter();
    t.drawImage(vid, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, bw, bh);
    g.save(); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    if (bg.blur > 0) g.filter = "blur(1px)";
    g.drawImage(tiny, 0, 0, W, H); g.restore();
    if (bg.opacity < 0.999) { g.fillStyle = `rgba(0,0,0,${1 - bg.opacity})`; g.fillRect(0, 0, W, H); }
  }
  const centerOf = L => [L.x + L.w / 2, L.y + L.h / 2];
  function withRotation(g, Q, fn) {
    const rot = Q.rot || 0;
    if (!rot) return fn(g);
    const [cx, cy] = centerOf(Q);
    g.save(); g.translate(cx, cy); g.rotate(rot * Math.PI / 180); g.translate(-cx, -cy); fn(g); g.restore();
  }
  function paintFaded(g, alpha, fn) {
    if (alpha >= 0.999) return fn(g);
    if (alpha <= 0.001) return;
    if (off.width !== cv.width || off.height !== cv.height) { off.width = cv.width; off.height = cv.height; }
    offCtx.setTransform(1, 0, 0, 1, 0, 0); offCtx.clearRect(0, 0, off.width, off.height);
    offCtx.setTransform(k * dpr, 0, 0, k * dpr, 0, 0);
    fn(offCtx);
    g.save(); g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = alpha; g.drawImage(off, 0, 0); g.restore();
  }
  function drawLayerAt(g, L, t) {
    if (L.type === "captions") {
      const st = captionState(L, t);
      if (st) paintFaded(g, L.opacity * st[3], gg => drawCaption(gg, L, st));
      return;
    }
    const Q = pose(L, t);
    if (Q.type === "video") return drawVideoLayer(g, Q);
    if (Q.type === "image") {
      const im = images[Q.asset];
      if (im && im.complete && im.naturalWidth) withRotation(g, Q, gg => { gg.save(); gg.globalAlpha = Q.opacity; gg.drawImage(im, Q.x, Q.y, Q.w, Q.h); gg.restore(); });
      return;
    }
    paintFaded(g, Q.opacity, gg => withRotation(gg, Q, g2 => Q.type === "text" ? drawText(g2, Q) : drawShape(g2, Q)));
  }
  function fadeAt(t) {
    let f = 1;
    if (doc.fade_in > 0) f = Math.min(f, t / doc.fade_in);
    if (doc.fade_out > 0) f = Math.min(f, (D() - t) / doc.fade_out);
    return clamp(f, 0, 1);
  }
  let guides = [];
  function draw() {
    if (!doc) return;
    const W = Wd(), H = Hd();
    ctx.setTransform(k * dpr, 0, 0, k * dpr, 0, 0);
    ctx.globalAlpha = 1; ctx.filter = "none";
    drawBackground(ctx, W, H);
    for (const L of doc.layers) if (activeAt(L, T)) drawLayerAt(ctx, L, T);
    const f = fadeAt(T);
    if (f < 1) { ctx.fillStyle = `rgba(0,0,0,${1 - f})`; ctx.fillRect(0, 0, W, H); }
    drawUI();
  }
  const ROT_HANDLE = 30;                        // css px above the box
  function handles(Q) {
    if (isLine(Q)) return { p1: [Q.x, Q.y], p2: [Q.x + Q.w, Q.y + Q.h] };
    const { x, y, w, h } = Q;
    const hs = { nw: [x, y], ne: [x + w, y], se: [x + w, y + h], sw: [x, y + h], e: [x + w, y + h / 2], w: [x, y + h / 2] };
    if (Q.type !== "text" && Q.type !== "captions") Object.assign(hs, { n: [x + w / 2, y], s: [x + w / 2, y + h] });
    if (Q.type === "text" || Q.type === "image" || (Q.type === "shape" && !isLine(Q))) hs.rot = [x + w / 2, y - ROT_HANDLE / k];
    return hs;
  }
  function rotPt([px, py], [cx, cy], deg) {
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return [cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c];
  }
  function drawUI() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineWidth = 1;
    if (guides.length) {
      ctx.strokeStyle = "rgba(8,101,255,.95)"; ctx.setLineDash([]);
      for (const [axis, v] of guides) {
        ctx.beginPath();
        if (axis === "x") { ctx.moveTo(v * k, 0); ctx.lineTo(v * k, Hd() * k); } else { ctx.moveTo(0, v * k); ctx.lineTo(Wd() * k, v * k); }
        ctx.stroke();
      }
    }
    const L = selL();
    if (!L || L.hidden) return;
    const Q = pose(L, T), on = activeAt(L, T), rot = Q.rot || 0, c = centerOf(Q);
    const S = pt => { const [x, y] = rotPt(pt, c, rot); return [x * k, y * k]; };
    ctx.strokeStyle = L.locked ? "rgba(255,255,255,.5)" : on ? "#0865ff" : "rgba(8,101,255,.55)";
    ctx.setLineDash(on ? [] : [5, 4]); ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (isLine(Q)) { ctx.moveTo(...S([Q.x, Q.y])); ctx.lineTo(...S([Q.x + Q.w, Q.y + Q.h])); }
    else { const pts = [[Q.x, Q.y], [Q.x + Q.w, Q.y], [Q.x + Q.w, Q.y + Q.h], [Q.x, Q.y + Q.h]].map(S); ctx.moveTo(...pts[0]); pts.slice(1).forEach(p => ctx.lineTo(...p)); ctx.closePath(); }
    ctx.stroke(); ctx.setLineDash([]);
    if (L.locked) return;
    const hs = handles(Q);
    if (hs.rot) { ctx.beginPath(); ctx.moveTo(...S([Q.x + Q.w / 2, Q.y])); ctx.lineTo(...S(hs.rot)); ctx.stroke(); }
    for (const [name, pt] of Object.entries(hs)) {
      const [hx, hy] = S(pt);
      ctx.fillStyle = name === "rot" ? "#0865ff" : "#fff"; ctx.strokeStyle = name === "rot" ? "#fff" : "#0865ff"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(hx, hy, name === "rot" ? 6 : 5.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
  }

  // ================= canvas size =================
  function fit() {
    if (!doc) return;
    const box = $("cbox"), W = Wd(), H = Hd();
    const aw = box.clientWidth - 28, ah = box.clientHeight - 28;
    k = Math.max(0.05, Math.min(aw / W, ah / H));
    dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.style.width = `${Math.round(W * k)}px`; cv.style.height = `${Math.round(H * k)}px`;
    cv.width = Math.round(W * k * dpr); cv.height = Math.round(H * k * dpr);
    draw();
  }
  new ResizeObserver(() => fit()).observe($("cbox"));

  // ================= keyframes =================
  const hasKf = (L, prop) => !!(L.kf && L.kf[prop] && L.kf[prop].length);
  function baseValue(L, prop) { return prop === "pos" ? [L.x, L.y] : prop === "rot" ? [L.rot || 0] : [1]; }
  const valueAt = (L, prop, t) => hasKf(L, prop) ? kfValue(L.kf[prop], t) : baseValue(L, prop);
  const keyIndex = (L, prop, t) => hasKf(L, prop) ? L.kf[prop].findIndex(q => Math.abs(q[0] - t) < 0.02) : -1;
  function setKey(L, prop, t, vals) {
    L.kf = L.kf || {};
    const ks = (L.kf[prop] = L.kf[prop] || []);
    t = r3(clamp(t, 0, D()));
    const i = ks.findIndex(q => Math.abs(q[0] - t) < 0.02);
    if (i >= 0) ks[i] = [ks[i][0], ...vals.map(r4), ks[i][ks[i].length - 1]];
    else { ks.push([t, ...vals.map(r4), "ease"]); ks.sort((a, b) => a[0] - b[0]); }
  }
  function bake(L, prop, vals) { if (prop === "pos") { L.x = r2(vals[0]); L.y = r2(vals[1]); } else if (prop === "rot") L.rot = r2(vals[0]); }
  function removeKey(L, prop, i) {
    const ks = L.kf[prop], [q] = ks.splice(i, 1);
    if (!ks.length) { delete L.kf[prop]; bake(L, prop, q.slice(1, -1)); }
    if (selKey && selKey.id === L.id && selKey.prop === prop) selKey = null;
  }
  function toggleAnimate(L, prop) {
    if (hasKf(L, prop)) { bake(L, prop, valueAt(L, prop, T)); delete L.kf[prop]; if (selKey && selKey.prop === prop && selKey.id === L.id) selKey = null; }
    else { setKey(L, prop, clamp(T, L.start, layerEnd(L)), valueAt(L, prop, T)); expanded.add(L.id); }
  }
  function toggleKeyHere(L, prop) {
    const i = keyIndex(L, prop, T);
    if (i >= 0) removeKey(L, prop, i); else { setKey(L, prop, T, valueAt(L, prop, T)); expanded.add(L.id); }
  }
  function keyTimes(L, prop) {
    const props = prop ? [prop] : propsFor(L);
    return [...new Set(props.flatMap(p => hasKf(L, p) ? L.kf[p].map(q => q[0]) : []))].sort((a, b) => a - b);
  }
  // Values that the canvas / inspector edit: write a keyframe at the playhead when the property is animated.
  function setProp(L, prop, vals) { if (hasKf(L, prop)) setKey(L, prop, T, vals); else bake(L, prop, vals); }
  function shiftKeys(L, dt) { for (const p in L.kf || {}) for (const q of L.kf[p]) q[0] = r3(Math.max(0, q[0] + dt)); }
  function fmtProp(prop, v) { return prop === "pos" ? `${Math.round(v[0])}, ${Math.round(v[1])}` : prop === "rot" ? `${Math.round(v[0])}°` : `${Math.round(v[0] * 100)}%`; }

  // ================= canvas interaction =================
  const toDesign = e => { const r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k }; };
  function segDist(p, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
    const t = l2 ? clamp(((p.x - x1) * dx + (p.y - y1) * dy) / l2, 0, 1) : 0;
    return Math.hypot(p.x - (x1 + t * dx), p.y - (y1 + t * dy));
  }
  const toLocal = (p, Q) => { const [x, y] = rotPt([p.x, p.y], centerOf(Q), -(Q.rot || 0)); return { x, y }; };
  function hitLayer(p) {
    for (let i = doc.layers.length - 1; i >= 0; i--) {
      const L = doc.layers[i];
      if (!activeAt(L, T) || L.locked) continue;
      if (L.type === "captions" && !captionState(L, T)) continue;
      const Q = pose(L, T), lp = toLocal(p, Q);
      if (isLine(Q)) { if (segDist(lp, Q.x, Q.y, Q.x + Q.w, Q.y + Q.h) <= Math.max(Q.stroke_w / 2, 10 / k)) return L; }
      else if (lp.x >= Q.x && lp.x <= Q.x + Q.w && lp.y >= Q.y && lp.y <= Q.y + Q.h) return L;
    }
    return null;
  }
  function hitHandle(L, p) {
    const Q = pose(L, T), lp = toLocal(p, Q);
    for (const [name, [hx, hy]] of Object.entries(handles(Q))) if (Math.hypot(lp.x - hx, lp.y - hy) <= 11 / k) return name;
    return null;
  }
  const CURSORS = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize",
                    e: "ew-resize", w: "ew-resize", p1: "move", p2: "move", rot: "grab" };
  let drag = null;
  cv.addEventListener("pointerdown", e => {
    if (!doc || e.button > 0) return;
    closeCtx();
    const p = toDesign(e);
    let L = selL(), h = L && !L.hidden && !L.locked && activeAt(L, T) ? hitHandle(L, p) : null;
    if (!h) {
      L = hitLayer(p);
      if (!L || L.id !== sel) select(L ? L.id : null);
      if (!L) return;
    }
    capture(cv, e);
    const Q = pose(L, T);
    drag = { id: L.id, h, p0: p, s: JSON.parse(JSON.stringify(L)), q: Q, pos0: valueAt(L, "pos", T), rot0: valueAt(L, "rot", T)[0],
             a0: Math.atan2(p.y - centerOf(Q)[1], p.x - centerOf(Q)[0]), moved: false };
  });
  cv.addEventListener("pointermove", e => {
    if (!doc) return;
    const p = toDesign(e);
    if (!drag) {
      const L = selL(), h = L && !L.hidden && !L.locked && activeAt(L, T) ? hitHandle(L, p) : null;
      cv.style.cursor = h ? CURSORS[h] : hitLayer(p) ? "move" : "default";
      return;
    }
    const L = layerById(drag.id);
    if (!L) return;
    if (!drag.moved && Math.hypot(p.x - drag.p0.x, p.y - drag.p0.y) * k < 3) return;
    drag.moved = true;
    if (!drag.h) moveLayer(L, p.x - drag.p0.x, p.y - drag.p0.y, e.altKey);
    else if (drag.h === "rot") {
      const [cx, cy] = centerOf(drag.q);
      let a = drag.rot0 + (Math.atan2(p.y - cy, p.x - cx) - drag.a0) * 180 / Math.PI;
      if (e.shiftKey) a = Math.round(a / 15) * 15;
      a = Math.round(((a + 180) % 360 + 360) % 360 - 180);
      setProp(L, "rot", [a]);
    } else resizeLayer(L, drag.h, p, e.shiftKey);
    changed(false);
  });
  const endDrag = () => { if (drag && drag.moved) { commit(); renderTimeline(); } drag = null; guides = []; draw(); };
  cv.addEventListener("pointerup", endDrag);
  cv.addEventListener("pointercancel", endDrag);
  cv.addEventListener("dblclick", () => {
    const L = selL();
    if (L && (L.type === "text" || L.type === "captions")) {
      const el = L.type === "text" ? document.querySelector("#insp textarea") : document.querySelector(".chunk-row.now input");
      if (el) { el.focus(); el.select && el.select(); }
    }
  });
  function snap1(vals, targets, th) {
    let best = null;
    for (const v of vals) for (const t of targets) { const d = t - v; if (Math.abs(d) <= th && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, t }; }
    return best;
  }
  function moveLayer(L, dx, dy, free) {
    const s = drag.s, q = drag.q;
    let ox = dx, oy = dy;
    guides = [];
    if (!free) {
      const W = Wd(), H = Hd(), th = 7 / k;
      const x0 = Math.min(q.x, q.x + q.w) + dx, x1 = Math.max(q.x, q.x + q.w) + dx, y0 = Math.min(q.y, q.y + q.h) + dy, y1 = Math.max(q.y, q.y + q.h) + dy;
      const sx = snap1([x0, (x0 + x1) / 2, x1], [0, W / 2, W], th), sy = snap1([y0, (y0 + y1) / 2, y1], [0, H / 2, H], th);
      if (sx) { ox += sx.d; guides.push(["x", sx.t]); }
      if (sy) { oy += sy.d; guides.push(["y", sy.t]); }
    }
    if (hasKf(L, "pos")) setKey(L, "pos", T, [drag.pos0[0] + ox, drag.pos0[1] + oy]);
    else { L.x = r2(s.x + ox); L.y = r2(s.y + oy); }
  }
  function resizeLayer(L, h, pw, shift) {
    const s = drag.s, q = drag.q, W = Wd(), H = Hd(), th = 7 / k, MIN = 8;
    guides = [];
    // pointer in the layer's own (unrotated, unscaled) frame
    const lp = toLocal(pw, q), f = (q.w && s.w) ? q.w / s.w : 1;
    const bx = drag.pos0[0] + (lp.x - q.x) / f, by = drag.pos0[1] + (lp.y - q.y) / f;
    const rot = q.rot || 0;
    const base = { x: drag.pos0[0], y: drag.pos0[1], w: s.w, h: s.h };
    const snapX = v => { if (rot) return v; const r = snap1([v], [0, W / 2, W], th); if (r) { guides.push(["x", r.t]); return v + r.d; } return v; };
    const snapY = v => { if (rot) return v; const r = snap1([v], [0, H / 2, H], th); if (r) { guides.push(["y", r.t]); return v + r.d; } return v; };
    let nx = base.x, ny = base.y, nw = base.w, nh = base.h;
    if (h === "p1" || h === "p2") {
      let x = snapX(bx), y = snapY(by);
      const ox = h === "p1" ? base.x + s.w : base.x, oy = h === "p1" ? base.y + s.h : base.y;
      if (shift) { const a = Math.round(Math.atan2(y - oy, x - ox) / (Math.PI / 4)) * Math.PI / 4, d = Math.hypot(x - ox, y - oy); x = ox + Math.cos(a) * d; y = oy + Math.sin(a) * d; }
      if (h === "p1") { nx = x; ny = y; nw = ox - x; nh = oy - y; } else { nw = x - base.x; nh = y - base.y; }
      L.w = r2(nw); L.h = r2(nh); setProp(L, "pos", [nx, ny]); return;
    }
    const corner = h.length === 2;
    if (L.type === "video" && !corner) return cropSide(L, s, base, h, h === "e" || h === "w" ? snapX(bx) : snapY(by));
    let x0 = base.x, y0 = base.y, x1 = base.x + base.w, y1 = base.y + base.h;
    if (h.includes("w")) x0 = Math.min(snapX(bx), x1 - MIN);
    if (h.includes("e")) x1 = Math.max(snapX(bx), x0 + MIN);
    if (h.includes("n")) y0 = Math.min(snapY(by), y1 - MIN);
    if (h.includes("s")) y1 = Math.max(snapY(by), y0 + MIN);
    if (L.type === "text") {
      if (corner) {
        const fz = Math.max(0.1, (x1 - x0) / s.w);
        L.size = clamp(Math.round(s.size * fz), 8, 400); L.stroke_w = r2(s.stroke_w * fz); L.w = r2(s.w * fz);
        nx = h.includes("w") ? base.x + s.w - L.w : base.x;
        layoutText(L);
        ny = h.includes("n") ? base.y + s.h - L.h : base.y;
      } else { nx = x0; L.w = r2(Math.max(L.size * 1.2, x1 - x0)); layoutText(L); ny = base.y; }
      return placeRotated(L, base, nx, ny, L.w, L.h, rot);
    }
    if (L.type === "captions") {
      nx = x0; ny = y0; L.w = r2(Math.max(L.size * 2, x1 - x0)); L.h = r2(Math.max(L.size * 1.3, y1 - y0));
      relayoutCaptions(L);
      L.x = r2(nx); L.y = r2(ny); return;
    }
    const keep = corner && (L.type === "video" ? true : L.type === "image" ? !shift : shift);
    if (keep) {
      const fz = Math.max((x1 - x0) / s.w, (y1 - y0) / s.h, MIN / Math.min(s.w, s.h));
      const w = s.w * fz, hh = s.h * fz;
      x0 = h.includes("w") ? base.x + s.w - w : base.x; y0 = h.includes("n") ? base.y + s.h - hh : base.y;
      x1 = x0 + w; y1 = y0 + hh;
    }
    L.w = r2(x1 - x0); L.h = r2(y1 - y0);
    placeRotated(L, base, x0, y0, L.w, L.h, rot);
  }
  // When a rotated box is resized in its own frame, move it so the opposite side stays put on screen.
  function placeRotated(L, base, nx, ny, nw, nh, rot) {
    if (!rot) return setProp(L, "pos", [nx, ny]);
    const c0 = [base.x + base.w / 2, base.y + base.h / 2], c1 = [nx + nw / 2, ny + nh / 2];
    const [wx, wy] = rotPt(c1, c0, rot);
    setProp(L, "pos", [wx - nw / 2, wy - nh / 2]);
  }
  // A video layer's side handles crop: the edge of the box and the edge of the crop move together.
  function cropSide(L, s, base, h, v) {
    const [cx, cy, cw, ch] = s.crop, kx = s.w / cw, ky = s.h / ch, MINC = 0.02;
    let nx = base.x, ny = base.y;
    if (h === "e" || h === "w") {
      const right = h === "e", ext = right ? v - (base.x + s.w) : base.x - v, cropRight = right !== !!s.flip;
      const df = clamp(ext / kx, MINC - cw, cropRight ? 1 - (cx + cw) : cx);
      L.crop = cropRight ? [cx, cy, cw + df, ch] : [cx - df, cy, cw + df, ch];
      L.w = r2(s.w + df * kx); nx = right ? base.x : base.x - df * kx;
    } else {
      const bottom = h === "s", ext = bottom ? v - (base.y + s.h) : base.y - v;
      const df = clamp(ext / ky, MINC - ch, bottom ? 1 - (cy + ch) : cy);
      L.crop = bottom ? [cx, cy, cw, ch + df] : [cx, cy - df, cw, ch + df];
      L.h = r2(s.h + df * ky); ny = bottom ? base.y : base.y - df * ky;
    }
    L.crop = L.crop.map(c => Math.round(c * 1e5) / 1e5);
    setProp(L, "pos", [nx, ny]);
  }

  // ================= layers =================
  const baseLayer = () => ({ id: uid(), name: "", hidden: false, locked: false, opacity: 1, start: 0, end: null, kf: {},
                             anim_in: { type: "none", dur: 0.5 }, anim_out: { type: "none", dur: 0.5 } });
  function fitVideo(L, mode) {
    const W = Wd(), H = Hd(), sw = meta.w, sh = meta.h;
    if (mode === "fill") {
      if (sw / sh > W / H) { const cw = (sh * W / H) / sw; L.crop = [(1 - cw) / 2, 0, cw, 1]; }
      else { const ch = (sw * H / W) / sh; L.crop = [0, (1 - ch) / 2, 1, ch]; }
      Object.assign(L, { x: 0, y: 0, w: W, h: H });
    } else {
      const [, , cw, ch] = L.crop, a = (cw * sw) / (ch * sh);
      let w = W, h = W / a;
      if (mode === "fit" && h > H) { h = H; w = H * a; }
      Object.assign(L, { x: r2((W - w) / 2), y: r2((H - h) / 2), w: r2(w), h: r2(h) });
    }
    L.crop = L.crop.map(c => Math.round(c * 1e5) / 1e5);
    if (L.kf) delete L.kf.pos;
  }
  const backdropCount = () => { let n = 0; while (n < doc.layers.length && doc.layers[n].backdrop) n++; return n; };
  function addFullVideo() {
    const L = { ...baseLayer(), type: "video", name: "Video", crop: [0, 0, 1, 1], flip: false, blur: 0, bright: 1 };
    fitVideo(L, "fit");
    doc.layers.splice(backdropCount(), 0, L); selKey = null; select(L.id); changed(); commit();
    toast("Added the video back (full width, above the background).");
  }
  function addBackdrop() {
    const L = { ...baseLayer(), id: "bg" + Math.random().toString(36).slice(2, 5), type: "video", name: "Background", crop: [0, 0, 1, 1],
                flip: false, blur: 50, bright: 0.82, backdrop: true };
    fitVideo(L, "fill");
    doc.layers.unshift(L); selKey = null; select(L.id); changed(); commit();
    toast("Added a blurred background layer at the bottom.");
    return false;
  }
  function newLayer(kind) {
    const W = Wd(), H = Hd(), u = Math.min(W, H), base = baseLayer();
    if (kind === "text") {
      const size = Math.round(u * 0.07);
      const L = { ...base, type: "text", text: "Your text", size, color: "#ffffff", stroke: "#000000", stroke_w: Math.round(size * 0.08),
                  bg: "#000000", bg_op: 0, align: "center", rot: 0, x: r2(W * 0.1), y: r2(H * 0.12), w: r2(W * 0.8), h: 100 };
      layoutText(L); return L;
    }
    if (kind === "rect") return { ...base, type: "shape", kind, rot: 0, x: r2(W / 2 - u * 0.2), y: r2(H / 2 - u * 0.125), w: r2(u * 0.4), h: r2(u * 0.25),
                                  fill: "#0865ff", fill_op: 1, stroke: "#ffffff", stroke_op: 1, stroke_w: 0, radius: Math.round(u * 0.02) };
    if (kind === "ellipse") return { ...base, type: "shape", kind, rot: 0, x: r2(W / 2 - u * 0.15), y: r2(H / 2 - u * 0.15), w: r2(u * 0.3), h: r2(u * 0.3),
                                     fill: "#0865ff", fill_op: 0, stroke: "#0865ff", stroke_op: 1, stroke_w: Math.round(u * 0.012), radius: 0 };
    if (kind === "line" || kind === "arrow") return { ...base, type: "shape", kind, rot: 0, x: r2(W * 0.3), y: r2(H * 0.55), w: r2(W * 0.35),
                                     h: kind === "arrow" ? r2(-u * 0.2) : 0, fill: "#ffffff", fill_op: 0,
                                     stroke: kind === "arrow" ? "#0865ff" : "#ffffff", stroke_op: 1, stroke_w: Math.round(u * 0.013), radius: 0 };
    if (kind === "video") {
      const L = { ...base, type: "video", crop: [0, 0, 1, 1], flip: false, blur: 0, bright: 1 };
      fitVideo(L, "fit");
      L.w = r2(L.w * 0.45); L.h = r2(L.h * 0.45); L.x = r2(W - L.w - u * 0.04); L.y = r2(u * 0.04);
      return L;
    }
  }
  function addLayer(L, msg) {
    if (doc.layers.length >= C.MAX_LAYERS) return toast(`A project can have up to ${C.MAX_LAYERS} layers.`);
    doc.layers.push(L); selKey = null; select(L.id); changed(); commit();
    if (msg) toast(msg);
  }
  document.querySelectorAll("[data-add]").forEach(b => b.addEventListener("click", () => addKind(b.dataset.add)));
  function addKind(kind) {
    if (!doc) return;
    if (kind === "image") return $("img-file").click();
    if (kind === "text" && !HAS_FONT) return toast("No font found on the server for text (set RECAPLY_FONT).");
    if (kind === "video" && !doc.layers.some(x => x.type === "video" && !x.backdrop)) return addFullVideo();
    const L = newLayer(kind);
    if (L.type !== "video") { L.start = r2(Math.min(T, Math.max(0, D() - 1))); L.end = null; }
    addLayer(L, L.start > 0 ? `Added at ${tfmt(L.start)} (the playhead). Drag its bar in the timeline to change when it shows.` : null);
  }
  $("img-file").addEventListener("change", async e => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    toast("Adding the picture…");
    const r = await fetch(`/api/editor/${PID}/asset`, { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return toast(j.error || "Couldn't add that picture.");
    const W = Wd(), H = Hd(), u = Math.min(W, H);
    let w = Math.min(j.w, u * 0.35), h = w * j.h / j.w;
    if (h > H * 0.5) { h = H * 0.5; w = h * j.w / j.h; }
    loadImage(j.asset);
    addLayer({ ...baseLayer(), name: f.name.replace(/\.[^.]+$/, "").slice(0, 40), type: "image", asset: j.asset, rot: 0,
               x: r2(W - w - u * 0.05), y: r2(u * 0.05), w: r2(w), h: r2(h) });
  });
  function loadImage(name) {
    if (!images[name]) { const im = new Image(); im.src = `/editor/${PID}/assets/${name}`; im.onload = () => draw(); images[name] = im; }
    return images[name];
  }
  function select(id, rebuild = true) {
    const was = sel;
    sel = id && layerById(id) ? id : null;
    if (sel !== was) selKey = selKey && selKey.id === sel ? selKey : null;
    if (rebuild) { buildInspector(); renderTimeline(); }
    paintHint(); draw();
  }
  function removeLayer(id) {
    const L = layerById(id); if (!L) return;
    doc.layers = doc.layers.filter(x => x.id !== id);
    if (sel === id) sel = null;
    if (selKey && selKey.id === id) selKey = null;
    expanded.delete(id);
    buildInspector(); changed(); commit();
    toast(`Deleted "${layerName(L)}".`,
          { label: "Undo", fn: () => $("undo").click() });
  }
  function moveZ(id, dir) {
    const i = doc.layers.findIndex(L => L.id === id), j = dir === "top" ? doc.layers.length - 1 : dir === "bottom" ? 0 : i + dir;
    if (i < 0 || j < 0 || j >= doc.layers.length || i === j) return;
    const [L] = doc.layers.splice(i, 1); doc.layers.splice(j, 0, L);
    changed(); commit();
  }
  function duplicate(id) {
    const L = layerById(id); if (!L) return;
    if (doc.layers.length >= C.MAX_LAYERS) return toast(`A project can have up to ${C.MAX_LAYERS} layers.`);
    const c = JSON.parse(JSON.stringify(L)); c.id = uid(); c.name = L.name ? `${L.name} copy` : "";
    if (L.type !== "captions") { c.x = r2(c.x + 30); c.y = r2(c.y + 30); if (c.kf && c.kf.pos) c.kf.pos.forEach(q => { q[1] += 30; q[2] += 30; }); }
    doc.layers.splice(doc.layers.indexOf(L) + 1, 0, c); select(c.id); changed(); commit();
  }
  function split(id) {
    const L = layerById(id || sel);
    if (!L) return toast("Select a layer to split.");
    if (!(T > L.start + 0.05 && T < layerEnd(L) - 0.05)) return toast("Move the playhead inside the layer to split it there.");
    if (doc.layers.length >= C.MAX_LAYERS) return toast(`A project can have up to ${C.MAX_LAYERS} layers.`);
    const c = JSON.parse(JSON.stringify(L)); c.id = uid(); c.start = r2(T); c.anim_in = { type: "none", dur: 0.5 };
    L.end = r2(T); L.anim_out = { type: "none", dur: 0.5 };
    doc.layers.splice(doc.layers.indexOf(L) + 1, 0, c); select(c.id); changed(); commit();
    toast("Split at the playhead.");
  }

  // ================= changes, history, saving =================
  function changed(structural = true) {
    draw(); refreshInspector();
    if (structural) renderTimeline(); else updateTimeline(true);
    dirty = true; paintSaved("Unsaved", "");
    clearTimeout(saveTimer); saveTimer = setTimeout(save, 900);
  }
  function commit() {
    const s = JSON.stringify(doc);
    if (hist[hi] === s) return;
    hist = hist.slice(0, hi + 1); hist.push(s);
    if (hist.length > 200) hist.shift();
    hi = hist.length - 1; paintUndo();
  }
  function paintUndo() { $("undo").disabled = hi <= 0; $("redo").disabled = hi >= hist.length - 1; }
  function restore(i) {
    hi = i; doc = JSON.parse(hist[hi]);
    if (sel && !layerById(sel)) sel = null;
    selKey = null;
    T = Math.min(T, D()); fit(); paintFormat(); buildInspector(); renderTimeline(); seekVideo(); paintUndo();
    dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(save, 600);
  }
  $("undo").onclick = () => hi > 0 && restore(hi - 1);
  $("redo").onclick = () => hi < hist.length - 1 && restore(hi + 1);
  const paintSaved = (txt, cls) => { const s = $("saved"); s.textContent = txt; s.className = `saved ${cls}`; };
  async function save() {
    if (!doc) return;
    clearTimeout(saveTimer);
    paintSaved("Saving…", "busy");
    try {
      const r = await fetch(`/api/editor/${PID}`, { method: "PUT", headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ doc, name: $("name").value, asr_applied: asrApplied || undefined }) });
      if (!r.ok) throw new Error();
      dirty = false; paintSaved("Saved", "ok");
    } catch (_) { paintSaved("Couldn't save", ""); saveTimer = setTimeout(save, 5000); }
  }
  $("name").addEventListener("input", () => { clearTimeout(saveTimer); saveTimer = setTimeout(save, 900); });
  $("name").addEventListener("keydown", e => { if (e.key === "Enter") e.target.blur(); });
  window.addEventListener("beforeunload", e => { if (dirty) { save(); e.preventDefault(); } });

  // ================= format =================
  function paintFormat() {
    const btns = [...$("fmts").querySelectorAll("button")];
    $("fmts").style.setProperty("--i", btns.findIndex(b => b.dataset.fmt === doc.format));
    btns.forEach(b => b.classList.toggle("on", b.dataset.fmt === doc.format));
  }
  $("fmts").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b || !doc || b.dataset.fmt === doc.format) return;
    const [Wo, Ho] = WH(); doc.format = b.dataset.fmt; const [Wn, Hn] = WH();
    // positions follow each axis; sizes use the geometric mean (so 9:16 -> 16:9 -> 9:16 comes back the same),
    // shrunk further only where a layer would be wider or taller than the new frame
    const sx = Wn / Wo, sy = Hn / Ho, sm = Math.sqrt(sx * sy);
    const reacted = doc.layers.some(L => L.id === "cam") && doc.layers.some(L => L.id === "game") && P.detect && P.detect.cam;
    const mapPt = (x, y, w, h, s) => [((x + w / 2 - Wo / 2) * sx + Wn / 2) - w * s / 2, ((y + h / 2 - Ho / 2) * sy + Hn / 2) - h * s / 2];
    for (const L of doc.layers) {
      if (L.type === "video") {
        const cover = L.x <= 1 && L.y <= 1 && L.x + L.w >= Wo - 1 && L.y + L.h >= Ho - 1, full = Math.abs(L.w - Wo) < 2 && Math.abs(L.x) < 2;
        if (cover) { fitVideo(L, "fill"); continue; }
        if (full) { fitVideo(L, "fit"); continue; }
      }
      const s = Math.min(sm, Wn / Math.max(1, Math.abs(L.w)), Hn / Math.max(1, Math.abs(L.h)));
      if (L.kf && L.kf.pos) L.kf.pos.forEach(q => { const [nx, ny] = mapPt(q[1], q[2], L.w, L.h, s); q[1] = r2(nx); q[2] = r2(ny); });
      const [nx, ny] = mapPt(L.x, L.y, L.w, L.h, s);
      L.x = r2(nx); L.y = r2(ny); L.w = r2(L.w * s); L.h = r2(L.h * s);
      if (L.type === "text") { L.size = clamp(Math.round(L.size * s), 8, 400); L.stroke_w = r2(L.stroke_w * s); layoutText(L); }
      if (L.type === "captions") { L.size = clamp(Math.round(L.size * s), 12, 300); L.stroke_w = r2(L.stroke_w * s); relayoutCaptions(L); }
      if (L.type === "shape") { L.stroke_w = r2(L.stroke_w * s); L.radius = r2(L.radius * s); }
    }
    if (reacted) applyReaction(reactOpts, P.detect);
    paintFormat(); fit(); buildInspector(); changed(); commit();
  });

  // ================= inspector =================
  let ups = [];
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  function section(title, children, reset, extra) {
    const s = el("section", "sec"), h = el("div", "sec-h", `<b>${esc(title)}</b>`);
    if (extra) h.appendChild(extra);
    if (reset) { const b = el("button", "link", "Reset"); b.type = "button"; b.onclick = () => { reset(); changed(); commit(); }; h.appendChild(b); }
    s.appendChild(h); children.filter(Boolean).forEach(c => s.appendChild(c)); return s;
  }
  function slider(label, min, max, step, get, set, fmt = v => v, kfOf) {
    const w = el("label", "ctl"), out = el("output"), inp = el("input");
    w.append(el("span", "", esc(label)), out);
    if (kfOf) w.appendChild(kbtn(kfOf.L, kfOf.prop));
    w.appendChild(inp);
    if (kfOf) w.style.gridTemplateColumns = "1fr auto auto";
    Object.assign(inp, { type: "range", min, max, step });
    inp.addEventListener("input", () => { set(parseFloat(inp.value)); out.textContent = fmt(get()); changed(false); });
    inp.addEventListener("change", () => { commit(); renderTimeline(); });
    ups.push(() => { const v = get(); if (document.activeElement !== inp) inp.value = v; out.textContent = fmt(v); });
    return w;
  }
  function kbtn(L, prop) {
    const b = el("button", "kbtn", ICON.diamondO); b.type = "button";
    b.onclick = e => { e.preventDefault(); toggleKeyHere(L, prop); buildInspector(); changed(); commit(); };
    ups.push(() => {
      const here = keyIndex(L, prop, T) >= 0;
      b.innerHTML = here ? ICON.diamond : ICON.diamondO; b.classList.toggle("on", hasKf(L, prop));
      b.title = here ? `Remove the ${PROPS[prop].toLowerCase()} keyframe here` : `${hasKf(L, prop) ? "Add a" : "Animate: add a"} ${PROPS[prop].toLowerCase()} keyframe at the playhead`;
    });
    return b;
  }
  function colorRow(label, get, set) {
    const w = el("label", "crow"), inp = el("input");
    inp.type = "color"; w.append(el("span", "", esc(label)), inp);
    inp.addEventListener("input", () => { set(inp.value); changed(false); });
    inp.addEventListener("change", commit);
    ups.push(() => { if (document.activeElement !== inp) inp.value = get(); });
    return w;
  }
  function switchRow(label, get, set) {
    const w = el("label", "switch-row"), inp = el("input");
    inp.type = "checkbox"; w.append(el("span", "", esc(label)), inp, el("span", "sw"));
    inp.addEventListener("change", () => { set(inp.checked); changed(); commit(); });
    ups.push(() => { inp.checked = !!get(); });
    return w;
  }
  function seg(options, get, set) {
    const w = el("div", "seg2");
    for (const [v, label] of options) {
      const b = el("button", "", esc(label)); b.type = "button"; b.dataset.v = v;
      b.onclick = () => { set(v); changed(); commit(); };
      w.appendChild(b);
    }
    ups.push(() => w.querySelectorAll("button").forEach(b => b.classList.toggle("on", String(get()) === b.dataset.v)));
    return w;
  }
  function selectIn(options, get, set) {
    const s = el("select", "sel-in");
    for (const [v, label] of options) { const o = el("option", "", esc(label)); o.value = v; s.appendChild(o); }
    s.onchange = () => { set(s.value); changed(); commit(); };
    ups.push(() => { s.value = get(); });
    return s;
  }
  function buttons(list) {
    const w = el("div", "btns");
    for (const [label, fn, title] of list) {
      const b = el("button", "btn ghost sm", esc(label)); b.type = "button"; if (title) b.title = title;
      b.onclick = () => { if (fn() !== false) { changed(); commit(); } }; w.appendChild(b);
    }
    return w;
  }
  function numbers(L) {
    const w = el("div", "nums");
    const fields = isLine(L) ? [["X1", "x"], ["Y1", "y"], ["X2", "x2"], ["Y2", "y2"]] : [["X", "x"], ["Y", "y"], ["W", "w"], ["H", "h"]];
    for (const [label, key] of fields) {
      const lab = el("label", "", label), inp = el("input");
      inp.type = "number"; inp.step = "1"; lab.appendChild(inp);
      const get = () => { const [px, py] = valueAt(L, "pos", T); return key === "x" ? px : key === "y" ? py : key === "x2" ? px + L.w : key === "y2" ? py + L.h : L[key]; };
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value); if (isNaN(v)) return;
        const [px, py] = valueAt(L, "pos", T);
        if (key === "x") setProp(L, "pos", [v, py]);
        else if (key === "y") setProp(L, "pos", [px, v]);
        else if (key === "x2") L.w = r2(v - px);
        else if (key === "y2") L.h = r2(v - py);
        else {
          const nv = Math.max(8, v);
          if (L.type === "video" || L.type === "image") { const a = L.w / L.h; if (key === "w") { L.w = nv; L.h = r2(nv / a); } else { L.h = nv; L.w = r2(nv * a); } }
          else if (L.type === "text") { if (key === "w") { L.w = nv; layoutText(L); } }
          else if (L.type === "captions") { L[key] = nv; relayoutCaptions(L); }
          else L[key] = nv;
        }
        changed(); commit();
      });
      if (L.type === "text" && key === "h") inp.disabled = true;
      ups.push(() => { if (document.activeElement !== inp) inp.value = Math.round(get()); });
      w.appendChild(lab);
    }
    return w;
  }
  function timing(L) {
    const w = el("div", "time2");
    for (const [label, key] of [["Starts", "start"], ["Ends", "end"]]) {
      const lab = el("label", "", label), row = el("div", "row"), inp = el("input", "tin"), b = el("button", "ib sm", ICON.here);
      b.title = "Set to the playhead"; b.type = "button";
      row.append(inp, b); lab.appendChild(row); w.appendChild(lab);
      const setV = v => {
        if (key === "start") { const d = r2(clamp(v, 0, layerEnd(L) - 0.1)) - L.start; L.start = r2(L.start + d); }
        else L.end = v >= D() - 0.05 ? null : r2(clamp(v, L.start + 0.1, D()));
        changed(); commit();
      };
      inp.addEventListener("change", () => { const v = tparse(inp.value); if (v != null) setV(v); else refreshInspector(); });
      b.onclick = () => setV(T);
      ups.push(() => { if (document.activeElement !== inp) inp.value = tfmt(key === "start" ? L.start : layerEnd(L)); });
    }
    return w;
  }
  function header(title, sub, L) {
    const h = el("div", "ih");
    h.appendChild(el("div", "ttl", `<b>${esc(title)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}`));
    if (L) {
      const mk = (icon, tip, fn) => { const b = el("button", "ib sm", icon); b.title = tip; b.type = "button"; b.onclick = fn; h.appendChild(b); };
      mk(ICON.up, "Bring forward (Ctrl+])", () => moveZ(L.id, 1));
      mk(ICON.down, "Send backward (Ctrl+[)", () => moveZ(L.id, -1));
      mk(ICON.copy, "Duplicate (Ctrl+D)", () => duplicate(L.id));
      mk(ICON.trash, "Delete (Del)", () => removeLayer(L.id));
      mk(ICON.close, "Deselect (Esc)", () => select(null));
    }
    return h;
  }
  function buildInspector() {
    const box = $("insp"), scroll = box.scrollTop; box.innerHTML = ""; ups = [];
    if (!doc) return;
    const L = selL();
    if (!L) buildProject(box);
    else {
      const title = L.type === "text" ? "Text" : L.type === "shape" ? KIND_NAMES[L.kind] : L.type === "image" ? "Image" : L.type === "captions" ? "Captions" : L.backdrop ? "Background" : "Video";
      const idx = doc.layers.length - doc.layers.indexOf(L);
      box.appendChild(header(title, `Layer ${idx} of ${doc.layers.length}${L.locked ? " · locked" : ""}`, L));
      if (selKey && selKey.id === L.id) box.appendChild(keyframeSection(L));
      if (L.type === "captions") buildCaptions(box, L);
      else {
        if (L.type === "text") buildText(box, L);
        else if (L.type === "shape") buildShape(box, L);
        else if (L.type === "video") buildVideo(box, L);
        else if (L.type === "image") box.appendChild(section("Picture", [buttons([["Original shape", () => {
          const im = images[L.asset]; if (im && im.naturalWidth) L.h = r2(L.w * im.naturalHeight / im.naturalWidth);
        }]])]));
        buildTransform(box, L);
        buildAnimation(box, L);
      }
      box.appendChild(section("Timing", [timing(L), el("div", "note", "Or drag the layer's bar in the timeline; drag its ends to trim.")], () => { L.start = 0; L.end = null; }));
    }
    refreshInspector();
    box.scrollTop = scroll;
  }
  function buildTransform(box, L) {
    const kids = [numbers(L)];
    const posRow = el("div", "crow"); posRow.append(el("span", "", "Position keyframes"), kbtn(L, "pos"));
    kids.push(posRow);
    if (L.type !== "video") {
      kids.push(slider("Rotation", -180, 180, 1, () => Math.round(valueAt(L, "rot", T)[0]), v => setProp(L, "rot", [v]), v => `${v}°`, { L, prop: "rot" }));
      kids.push(slider("Scale (animation)", 0.1, 3, 0.01, () => valueAt(L, "sc", T)[0], v => { setKey(L, "sc", T, [v]); expanded.add(L.id); }, v => `${Math.round(v * 100)}%`, { L, prop: "sc" }));
    }
    kids.push(slider("Opacity", 0, 1, 0.01, () => L.opacity, v => L.opacity = v, v => `${Math.round(v * 100)}%`));
    if (propsFor(L).includes("op")) kids.push(slider("Fade (animation)", 0, 1, 0.01, () => valueAt(L, "op", T)[0], v => { setKey(L, "op", T, [v]); expanded.add(L.id); }, v => `${Math.round(v * 100)}%`, { L, prop: "op" }));
    if (L.type !== "video") kids.push(buttons([["Centre", () => { const [cx, cy] = [Wd() / 2 - L.w / 2, Hd() / 2 - L.h / 2]; setProp(L, "pos", [r2(cx), r2(cy)]); }]]));
    kids.push(el("div", "note", "◆ adds a keyframe at the playhead: move the playhead, change the value, and it animates between them. The timeline (▸ next to the layer) shows every keyframe."));
    box.appendChild(section("Transform", kids));
  }
  function buildAnimation(box, L) {
    const opts = ANIM_NAMES.filter(([v]) => L.type !== "video" || !["pop", "zoom"].includes(v));
    const row = (label, key) => {
      const r = el("div", "animrow"), num = el("input", "tin");
      num.type = "number"; num.step = "0.1"; num.min = "0.05"; num.max = "5"; num.title = "Duration in seconds";
      num.onchange = () => { const v = parseFloat(num.value); if (!isNaN(v)) { L[key].dur = clamp(v, 0.05, 5); changed(); commit(); } };
      ups.push(() => { if (document.activeElement !== num) num.value = L[key].dur; num.disabled = L[key].type === "none"; });
      L[key] = L[key] || { type: "none", dur: 0.5 };
      r.append(el("span", "", label), selectIn(opts, () => L[key].type, v => { L[key].type = v; }), num);
      return r;
    };
    const n = keyTimes(L).length;
    const extra = n ? (() => { const b = el("button", "link", "Clear keyframes"); b.type = "button"; b.onclick = () => { L.kf = {}; selKey = null; buildInspector(); changed(); commit(); }; return b; })() : null;
    box.appendChild(section("Animation", [row("In", "anim_in"), row("Out", "anim_out"),
      el("div", "note", `Plays when the layer appears / disappears.${n ? ` It also has ${n} keyframe time${n > 1 ? "s" : ""}.` : ""}`)], null, extra));
  }
  function keyframeSection(L) {
    const { prop } = selKey, i = keyIndex(L, prop, selKey.t);
    if (i < 0) { selKey = null; return el("div"); }
    const q = L.kf[prop][i];
    const time = el("input", "tin"); time.value = tfmt(q[0]);
    time.onchange = () => { const v = tparse(time.value); if (v == null) return; q[0] = r3(clamp(v, 0, D())); L.kf[prop].sort((a, b) => a[0] - b[0]); selKey.t = q[0]; changed(); commit(); };
    const del = el("button", "btn ghost sm", "Delete keyframe"); del.type = "button";
    del.onclick = () => { removeKey(L, prop, keyIndex(L, prop, selKey.t)); buildInspector(); changed(); commit(); };
    return section(`${PROPS[prop]} keyframe`, [el("div", "note", `Value ${fmtProp(prop, q.slice(1, -1))}. Easing controls how it moves towards the next keyframe.`),
      seg(EASES, () => q[q.length - 1], v => { q[q.length - 1] = v; }),
      (() => { const r = el("div", "time2"); const a = el("label", "", "Time"); a.appendChild(time); const b = el("label", "", "&nbsp;"); b.appendChild(del); r.append(a, b); return r; })()]);
  }
  function buildText(box, L) {
    const ta = el("textarea", "tin"); ta.maxLength = C.TEXT_MAX; ta.rows = 3;
    ta.addEventListener("input", () => { L.text = ta.value; layoutText(L); changed(false); });
    ta.addEventListener("change", () => { commit(); renderTimeline(); });
    ups.push(() => { if (document.activeElement !== ta) ta.value = L.text; });
    box.appendChild(section("Text", [ta,
      slider("Size", 8, 300, 1, () => L.size, v => { L.size = v; layoutText(L); }, v => `${v}px`),
      seg([["left", "Left"], ["center", "Centre"], ["right", "Right"]], () => L.align, v => L.align = v),
      colorRow("Colour", () => L.color, v => L.color = v),
      el("div", "note", "Emoji aren't in the export font and would show as boxes.")]));
    box.appendChild(section("Outline", [colorRow("Colour", () => L.stroke, v => L.stroke = v),
      slider("Width", 0, 30, 0.5, () => L.stroke_w, v => L.stroke_w = v, v => `${v}px`)]));
    box.appendChild(section("Background", [colorRow("Colour", () => L.bg, v => L.bg = v),
      slider("Opacity", 0, 1, 0.01, () => L.bg_op, v => L.bg_op = v, v => `${Math.round(v * 100)}%`)]));
  }
  function buildShape(box, L) {
    if (!isLine(L)) box.appendChild(section("Fill", [colorRow("Colour", () => L.fill, v => L.fill = v),
      slider("Opacity", 0, 1, 0.01, () => L.fill_op, v => L.fill_op = v, v => v <= 0 ? "None" : `${Math.round(v * 100)}%`)]));
    box.appendChild(section(isLine(L) ? "Line" : "Stroke", [colorRow("Colour", () => L.stroke, v => L.stroke = v),
      slider("Width", 0, 80, 0.5, () => L.stroke_w, v => L.stroke_w = v, v => v <= 0 ? "None" : `${v}px`),
      slider("Opacity", 0, 1, 0.01, () => L.stroke_op, v => L.stroke_op = v, v => `${Math.round(v * 100)}%`)]));
    if (L.kind === "rect") box.appendChild(section("Corners", [slider("Roundness", 0, 400, 1, () => L.radius, v => L.radius = v, v => `${v}px`)]));
  }
  function buildVideo(box, L) {
    box.appendChild(section("Effects", [
      slider("Blur", 0, 100, 1, () => L.blur || 0, v => L.blur = v, v => v <= 0 ? "Off" : v),
      slider("Brightness", 0, 1.5, 0.01, () => L.bright ?? 1, v => L.bright = v, v => `${Math.round(v * 100)}%`),
      el("div", "note", L.backdrop ? "This is the blurred background: a normal video layer at the bottom, filling the frame. Drag, trim, crop, animate or delete it like any layer."
                                   : "Blur hides details (like a kill feed) or makes a background; lower brightness darkens it.")],
      () => { L.blur = L.backdrop ? 50 : 0; L.bright = L.backdrop ? 0.82 : 1; }));
    box.appendChild(section("Crop", [cropEditor(L), buttons([["Fit", () => fitVideo(L, "fit")], ["Fill", () => fitVideo(L, "fill")],
      ["Full width", () => fitVideo(L, "width")]]), buttons([["Flip", () => { L.flip = !L.flip; }], ["Reset crop", () => { L.crop = [0, 0, 1, 1]; fitVideo(L, "fit"); }]]),
      el("div", "note", "Drag the box on the frame to choose what this view shows. On the canvas, corner handles resize the view and side handles crop it.")]));
  }
  // ---------- captions inspector ----------
  function styleCards(get, set) {
    const w = el("div", "styles");
    for (const [key, st] of Object.entries(CAP_STYLES)) {
      const b = el("button", "style-card"); b.type = "button"; b.dataset.v = key;
      const words = key === "single" ? ["WOW"] : ["SO", "CLEAN"];
      const sample = words.map((wd, i) => {
        const t = st.upper ? wd : wd[0] + wd.slice(1).toLowerCase(), hiW = i === 1 || words.length === 1;
        const style = st.hi_mode === "box" && hiW ? `background:${st.hi_bg}` : st.hi_mode === "color" && hiW ? `color:${st.hi}` : "";
        const stroke = st.stroke_w ? "text-shadow:0 0 2px #000,0 0 2px #000" : "";
        return `<i style="${style};${stroke}">${esc(t)}</i>`;
      }).join("");
      b.innerHTML = `<span class="smp" style="${st.bg_op ? `background:rgba(0,0,0,${st.bg_op});padding:2px 5px;border-radius:4px` : ""}">${sample}</span>${esc(st.label)}`;
      b.onclick = () => { set(key); w.querySelectorAll(".style-card").forEach(x => x.classList.toggle("on", x.dataset.v === key)); };
      w.appendChild(b);
    }
    const paint = () => w.querySelectorAll(".style-card").forEach(x => x.classList.toggle("on", x.dataset.v === get()));
    paint(); ups.push(paint);
    return w;
  }
  function applyStyle(L, key) {
    const st = CAP_STYLES[key]; if (!st) return;
    const u = Math.min(Wd(), Hd()) / 1080;
    Object.assign(L, { size: Math.round(st.size * u), upper: st.upper, color: st.color, hi: st.hi, hi_mode: st.hi_mode, hi_bg: st.hi_bg,
                       stroke: st.stroke, stroke_w: r2(st.stroke_w * u), bg: st.bg, bg_op: st.bg_op, pop: st.pop, preset: key });
    L.h = r2(Math.max(L.h, L.size * C.LINE_H * 2.4));
    rechunk(L, st.per);
  }
  function rechunk(L, per) {
    const words = (L.chunks || []).flatMap(c => c.words);
    L.per = per; L.chunks = chunkWords(words, per); relayoutCaptions(L);
  }
  function editChunk(L, i, text) {
    const c = L.chunks[i], toks = text.split(/\s+/).filter(Boolean);
    if (!toks.length) { L.chunks.splice(i, 1); return; }
    if (toks.length === c.words.length) c.words.forEach((w, q) => { w.w = toks[q].slice(0, 40); });
    else {
      const s0 = c.words[0].s, e1 = c.words[c.words.length - 1].e, total = toks.reduce((a, t) => a + t.length + 1, 0);
      let t = s0;
      c.words = toks.map(tok => { const d = (e1 - s0) * (tok.length + 1) / total, w = { w: tok.slice(0, 40), s: r3(t), e: r3(t + d), p: 1 }; t += d; return w; });
    }
    const m = measurer(L.size); c.lines = captionLines(L, c, m);
  }
  function buildCaptions(box, L) {
    box.appendChild(section("Style", [styleCards(() => L.preset, v => { applyStyle(L, v); changed(); commit(); buildInspector(); }),
      el("div", "note", "Words on screen at once"),
      seg([[1, "1"], [2, "2"], [3, "3"], [4, "4"], [6, "6"]], () => L.per || 3, v => rechunk(L, +v)),
      slider("Size", 20, 200, 1, () => L.size, v => { L.size = v; relayoutCaptions(L); }, v => `${v}px`),
      switchRow("UPPERCASE", () => L.upper, v => { L.upper = v; relayoutCaptions(L); }),
      switchRow("Pop in", () => L.pop, v => L.pop = v),
      buttons([["Top", () => { L.y = r2(Hd() * 0.14 - L.h / 2); }], ["Middle", () => { L.y = r2(Hd() * 0.5 - L.h / 2); }], ["Bottom", () => { L.y = r2(Hd() * (Hd() > Wd() ? 0.74 : 0.82) - L.h / 2); }]])]));
    box.appendChild(section("Colours", [colorRow("Text", () => L.color, v => L.color = v),
      el("div", "note", "Spoken word"),
      seg([["color", "Colour"], ["box", "Box"], ["none", "None"]], () => L.hi_mode, v => { L.hi_mode = v; setTimeout(buildInspector); }),
      L.hi_mode === "color" ? colorRow("Highlight colour", () => L.hi, v => L.hi = v) : null,
      L.hi_mode === "box" ? colorRow("Box colour", () => L.hi_bg, v => L.hi_bg = v) : null,
      colorRow("Outline", () => L.stroke, v => L.stroke = v),
      slider("Outline width", 0, 20, 0.5, () => L.stroke_w, v => L.stroke_w = v, v => v ? `${v}px` : "None"),
      colorRow("Background", () => L.bg, v => L.bg = v),
      slider("Background opacity", 0, 1, 0.01, () => L.bg_op, v => L.bg_op = v, v => v ? `${Math.round(v * 100)}%` : "None"),
      slider("Opacity", 0, 1, 0.01, () => L.opacity, v => L.opacity = v, v => `${Math.round(v * 100)}%`)]));
    const list = el("div", "chunks");
    L.chunks.forEach((c, i) => {
      const r = el("div", "chunk-row"), tm = el("button", "tm", tfmt(outT(c.words[0].s))), inp = el("input"), x = el("button", "kbtn", ICON.close);
      tm.type = "button"; tm.title = "Go to this caption"; x.type = "button"; x.title = "Delete this caption";
      inp.value = c.words.map(w => w.w).join(" "); inp.maxLength = 300; r.dataset.i = i;
      if (c.words.some(w => (w.p ?? 1) < 0.5)) { r.classList.add("low"); r.title = "Some words here were unclear in the audio: check them."; }
      tm.onclick = () => { pause(); T = clamp(outT(c.words[0].s) + 0.01, 0, D()); seekVideo(); updateTimeline(); };
      inp.onfocus = () => tm.onclick();
      inp.onchange = () => { editChunk(L, i, inp.value); changed(); commit(); buildInspector(); };
      inp.onkeydown = e => { if (e.key === "Enter") inp.blur(); };
      x.onclick = () => { L.chunks.splice(i, 1); changed(); commit(); buildInspector(); };
      r.append(tm, inp, x); list.appendChild(r);
    });
    ups.push(() => {
      const st = captionState(L, T);
      list.querySelectorAll(".chunk-row").forEach(r => r.classList.toggle("now", !!st && +r.dataset.i === st[0]));
    });
    const again = el("button", "link", "Transcribe again"); again.type = "button"; again.onclick = () => openCaptions();
    box.appendChild(section(`Transcript · ${L.chunks.length} captions`, [list,
      el("div", "note", "Click a caption to jump to it; edit the words and press Enter. Wavy lines mark words the speech model wasn't sure about.")], null, again));
  }
  function buildProject(box) {
    box.appendChild(header("Video", `${tfmt(D())} · ${doc.format}`, null));
    const t0 = el("div", "time2");
    for (const [label, i] of [["Start", 0], ["End", 1]]) {
      const lab = el("label", "", label), row = el("div", "row"), inp = el("input", "tin"), b = el("button", "ib sm", ICON.here);
      b.type = "button"; b.title = "Set to the playhead"; row.append(inp, b); lab.appendChild(row); t0.appendChild(lab);
      const setV = v => { setTrim(i, v); changed(); commit(); };
      inp.addEventListener("change", () => { const v = tparse(inp.value); if (v != null) setV(v); else refreshInspector(); });
      b.onclick = () => setV(doc.trim[0] + T * doc.speed);
      ups.push(() => { if (document.activeElement !== inp) inp.value = tfmt(doc.trim[i]); });
    }
    const len = el("div", "note");
    ups.push(() => { len.textContent = `Source ${tfmt(meta.duration)} · result ${tfmt(D())}${doc.speed !== 1 ? ` at ${doc.speed}×` : ""}. Or drag the white handles on the Trim row.`; });
    box.appendChild(section("Trim", [t0, len], () => { doc.trim = [0, meta.duration]; }));
    box.appendChild(section("Speed", [seg([[0.5, "0.5×"], [0.75, "0.75×"], [1, "1×"], [1.25, "1.25×"], [1.5, "1.5×"], [2, "2×"]],
      () => doc.speed, v => { const s0 = doc.trim[0] + T * doc.speed; doc.speed = +v; T = clamp((s0 - doc.trim[0]) / doc.speed, 0, D()); vid.playbackRate = doc.speed; })]));
    const hasBack = doc.layers.some(L => L.backdrop);
    box.appendChild(section("Background", [
      colorRow("Base colour", () => doc.bg.color, v => { doc.bg.type = "color"; doc.bg.color = v; }),
      el("div", "note", hasBack ? "The blurred background is the Background layer at the bottom of the timeline. This colour shows only where no layer covers the frame."
                               : "Shows wherever no layer covers the frame."),
      hasBack ? null : buttons([["Add blurred background", () => addBackdrop()]])],
      () => { doc.bg = { type: "color", color: "#000000", blur: 50, opacity: 0.82 }; }));
    box.appendChild(section("Colour", [["Brightness", "brightness"], ["Contrast", "contrast"], ["Saturation", "saturation"]].map(([label, key]) =>
      slider(label, 0, 2, 0.01, () => doc.color[key], v => doc.color[key] = v, v => `${Math.round(v * 100)}%`)),
      () => { doc.color = { brightness: 1, contrast: 1, saturation: 1 }; }));
    box.appendChild(section("Sound & fades", [
      slider("Volume", 0, 2, 0.01, () => doc.volume, v => doc.volume = v, v => v <= 0 ? "Muted" : `${Math.round(v * 100)}%`),
      slider("Fade in", 0, 5, 0.1, () => doc.fade_in, v => doc.fade_in = v, v => v <= 0 ? "Off" : `${v.toFixed(1)}s`),
      slider("Fade out", 0, 5, 0.1, () => doc.fade_out, v => doc.fade_out = v, v => v <= 0 ? "Off" : `${v.toFixed(1)}s`),
      el("div", "note", "Fades apply to the picture (from black) and the sound.")],
      () => { doc.volume = 1; doc.fade_in = 0; doc.fade_out = 0; }));
    box.appendChild(el("section", "sec", `<div class="sec-h"><b>Getting started</b></div><div class="note">
      <b style="color:var(--text)">1.</b> Add captions, text, shapes or pictures from the left.<br>
      <b style="color:var(--text)">2.</b> Click a layer on the canvas or in the timeline to edit it. Drag to move, pull the handles to resize, the round handle to rotate.<br>
      <b style="color:var(--text)">3.</b> Animate: pick an In / Out animation, or add ◆ keyframes at different times.<br>
      <b style="color:var(--text)">4.</b> Drag layers up and down in the timeline to change what's in front. Right-click a layer for more.<br>
      <b style="color:var(--text)">5.</b> Export when it looks right. Press <b style="color:var(--text)">?</b> for shortcuts.</div>`));
  }
  function refreshInspector() { ups.forEach(u => u()); }
  function setTrim(i, v) {
    const s0 = doc.trim[0] + T * doc.speed;
    if (i === 0) doc.trim[0] = r2(clamp(v, 0, doc.trim[1] - 0.2)); else doc.trim[1] = r2(clamp(v, doc.trim[0] + 0.2, meta.duration));
    T = clamp((s0 - doc.trim[0]) / doc.speed, 0, D());
    seekVideo();
  }
  function paintHint() {
    const L = selL();
    $("hint").textContent = !L ? "Click a layer to select it · right-click in the timeline for more"
      : L.locked ? "This layer is locked (unlock it in the timeline)"
      : L.type === "captions" ? "Drag to move the captions · double-click to edit the words"
      : L.type === "video" ? "Drag to move · corners resize · sides crop"
      : "Drag to move · handles resize · round handle rotates (Shift: 15° steps)";
  }

  // ================= rectangle editor on a source frame (crop / reaction boxes) =================
  function rectEditor(canvas, rects, onChange, onDone) {
    const g = canvas.getContext("2d");
    let dr = null;
    function size() {
      const w = canvas.clientWidth || 280, h = w * meta.h / meta.w, d = Math.min(2, devicePixelRatio || 1);
      canvas.style.height = `${h}px`; canvas.width = Math.round(w * d); canvas.height = Math.round(h * d);
    }
    function paint() {
      if (!canvas.isConnected) return;
      if (!canvas.width || Math.abs(canvas.width - canvas.clientWidth * Math.min(2, devicePixelRatio || 1)) > 2) size();
      const W = canvas.width, H = canvas.height, u = W / (canvas.clientWidth || W);
      g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
      if (vready()) g.drawImage(vid, 0, 0, W, H);
      g.fillStyle = "rgba(0,0,0,.45)"; g.fillRect(0, 0, W, H);
      for (const r of rects) { const [x, y, w, h] = r.get(); if (vready()) g.drawImage(vid, x * vid.videoWidth, y * vid.videoHeight, w * vid.videoWidth, h * vid.videoHeight, x * W, y * H, w * W, h * H); }
      for (const r of rects) {
        const [x, y, w, h] = r.get();
        g.strokeStyle = r.color; g.lineWidth = 2 * u; g.strokeRect(x * W, y * H, w * W, h * H);
        g.fillStyle = r.color;
        for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) { g.beginPath(); g.arc(hx * W, hy * H, 5 * u, 0, 7); g.fill(); }
        if (r.label) { g.font = `600 ${12 * u}px Inter, sans-serif`; g.fillText(r.label, x * W + 6 * u, y * H + 16 * u); }
      }
    }
    const pos = e => { const b = canvas.getBoundingClientRect(); return [(e.clientX - b.left) / b.width, (e.clientY - b.top) / b.height]; };
    canvas.onpointerdown = e => {
      const [px, py] = pos(e), th = 12 / canvas.clientWidth, thy = th * meta.w / meta.h;
      for (let i = rects.length - 1; i >= 0 && !dr; i--) {
        const [x, y, w, h] = rects[i].get();
        const corners = { nw: [x, y], ne: [x + w, y], sw: [x, y + h], se: [x + w, y + h] };
        for (const [c, [cx, cy]] of Object.entries(corners)) if (Math.abs(px - cx) < th && Math.abs(py - cy) < thy) { dr = { i, c, r0: [x, y, w, h], p0: [px, py] }; break; }
        if (!dr && px >= x && px <= x + w && py >= y && py <= y + h) dr = { i, c: "move", r0: [x, y, w, h], p0: [px, py] };
      }
      if (dr) capture(canvas, e);
    };
    canvas.onpointermove = e => {
      if (!dr) return;
      const [px, py] = pos(e), dx = px - dr.p0[0], dy = py - dr.p0[1], MIN = 0.03;
      let [x, y, w, h] = dr.r0;
      if (dr.c === "move") { x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h); }
      else if (rects[dr.i].lock) {
        const af = rects[dr.i].lock(), left = dr.c.includes("w"), top = dr.c.includes("n");
        const ax = left ? x + w : x, ay = top ? y + h : y;
        let nw = Math.max(Math.abs(px - ax), Math.abs(py - ay) * af, MIN), nh = nw / af;
        const mw = left ? ax : 1 - ax, mh = top ? ay : 1 - ay;
        if (nw > mw) { nw = mw; nh = nw / af; }
        if (nh > mh) { nh = mh; nw = nh * af; }
        w = nw; h = nh; x = left ? ax - nw : ax; y = top ? ay - nh : ay;
      } else {
        let x0 = x, y0 = y, x1 = x + w, y1 = y + h;
        if (dr.c.includes("w")) x0 = clamp(px, 0, x1 - MIN); else x1 = clamp(px, x0 + MIN, 1);
        if (dr.c.includes("n")) y0 = clamp(py, 0, y1 - MIN); else y1 = clamp(py, y0 + MIN, 1);
        x = x0; y = y0; w = x1 - x0; h = y1 - y0;
      }
      rects[dr.i].set([x, y, w, h].map(v => Math.round(v * 1e5) / 1e5));
      onChange(); paint();
    };
    canvas.onpointerup = canvas.onpointercancel = () => { if (dr) { dr = null; onDone && onDone(); } };
    size(); paint();
    return paint;
  }
  let cropPaint = null;
  function cropEditor(L) {
    const c = el("canvas", "mini");
    requestAnimationFrame(() => {
      // the box on the canvas stays put; the crop keeps its shape (side handles on the canvas change the shape)
      cropPaint = rectEditor(c, [{ get: () => L.crop, set: r => { L.crop = r; }, color: "#0865ff",
                                   lock: () => (L.w / L.h) * meta.h / meta.w }], () => changed(false), commit);
    });
    return c;
  }

  // ================= timeline =================
  const body = $("tl-body");
  let zoomF = 1, pps = 50, tlDrag = null, labW = 236;
  const snapOn = () => $("t-snap").classList.contains("on");
  function computeScale() {
    const vis = Math.max(200, body.clientWidth - labW - 24);
    pps = vis / D() * zoomF;
  }
  function tickStep() {
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    return steps.find(s => s * pps >= 70) || 600;
  }
  function renderTimeline() {
    if (!doc) return;
    labW = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--lab")) || 236;
    computeScale();
    const d = D(), tw = Math.max(body.clientWidth - labW - 2, d * pps + 24);
    const rows = [...doc.layers].reverse();
    let h = `<div class="tl-grid" id="tl-grid" style="--tw:${tw}px"><div class="ruler-l">Layers · ${doc.layers.length}</div><div class="ruler" id="ruler"></div>`;
    const hasVideo = doc.layers.some(L => L.type === "video" && !L.backdrop && !L.hidden);
    if (!rows.length) h += `<div class="tl-empty">No layers. <button class="tbtn" data-addvideo>Add the video back</button> or add text, shapes and captions from the left.</div>`;
    else if (!hasVideo) h += `<div class="tl-empty">There's no sharp video layer, only the background. <button class="tbtn" data-addvideo>Add the video back</button></div>`;
    for (const L of rows) {
      const isSel = L.id === sel, col = TYPE_COLOR[L.type], canKf = propsFor(L).length > 0, open = expanded.has(L.id) && canKf;
      h += `<div class="cell-l ${isSel ? "sel" : ""} ${L.hidden ? "off" : ""}" data-row="${L.id}">
        <button class="mini-b grip" data-grip="${L.id}" title="Drag up or down to change the layer order">${ICON.grip}</button>
        <button class="mini-b twirl ${open ? "open" : ""}" data-twirl="${L.id}" title="${canKf ? "Show keyframes" : ""}" ${canKf ? "" : "style=visibility:hidden"}>${ICON.twirl}</button>
        <span class="swatch" style="background:${col}"></span>
        <span class="ic">${iconOf(L)}</span><span class="nm" data-name="${L.id}" title="Double-click to rename">${esc(layerName(L))}</span>
        <button class="mini-b" data-eye="${L.id}" title="${L.hidden ? "Show" : "Hide"}">${L.hidden ? ICON.eyeOff : ICON.eye}</button>
        <button class="mini-b ${L.locked ? "on" : ""}" data-lock="${L.id}" title="${L.locked ? "Unlock" : "Lock (can't be selected on the canvas)"}">${L.locked ? ICON.lock : ICON.unlock}</button>
      </div><div class="cell-t ${isSel ? "sel" : ""}" data-track="${L.id}">${barHtml(L, isSel, col)}</div>`;
      if (open) for (const prop of propsFor(L)) {
        const on = hasKf(L, prop), ks = on ? L.kf[prop] : [];
        const sk = selKey && selKey.id === L.id && selKey.prop === prop ? ks.find(q => Math.abs(q[0] - selKey.t) < 1e-3) : null;
        h += `<div class="cell-l prop ${isSel ? "sel" : ""}" data-row="${L.id}" data-prop="${prop}">
          <button class="mini-b ${on ? "on" : ""}" data-sw="${prop}" data-id="${L.id}" title="${on ? "Stop animating (removes its keyframes)" : "Animate this property"}">${ICON.watch}</button>
          <span class="pn">${PROPS[prop]}</span>
          <button class="mini-b" data-kprev="${prop}" data-id="${L.id}" title="Previous keyframe">${ICON.prev}</button>
          <button class="mini-b" data-ktog="${prop}" data-id="${L.id}" title="Add / remove a keyframe at the playhead">${keyIndex(L, prop, T) >= 0 ? ICON.diamond : ICON.diamondO}</button>
          <button class="mini-b" data-knext="${prop}" data-id="${L.id}" title="Next keyframe">${ICON.next}</button>
          ${sk ? `<select data-ease="${prop}" data-id="${L.id}" title="Easing towards the next keyframe">${EASES.map(([v, n]) => `<option value="${v}" ${sk[sk.length - 1] === v ? "selected" : ""}>${n}</option>`).join("")}</select>` : ""}
          <span class="pv" data-pv="${prop}" data-id="${L.id}"></span>
        </div><div class="cell-t prop" data-ktrack="${L.id}" data-prop="${prop}">${ks.length > 1 ? `<div class="kline" style="left:${ks[0][0] * pps}px;width:${(ks[ks.length - 1][0] - ks[0][0]) * pps}px"></div>` : ""}
          ${ks.map(q => `<div class="kf ${q[q.length - 1] === "hold" ? "hold" : ""} ${sk === q ? "sel" : ""}" data-kf="${q[0]}" data-prop="${prop}" data-id="${L.id}" style="left:${q[0] * pps}px" title="${tfmt(q[0])} · ${fmtProp(prop, q.slice(1, -1))}"></div>`).join("")}</div>`;
      }
    }
    h += `<div class="cell-l"><span class="ic" style="margin-left:34px">${ICON.audio}</span><span class="nm">Audio</span></div><div class="cell-t"><canvas class="wave" id="tl-wave"></canvas></div>`;
    h += `<div class="playhead" id="playhead"></div></div>`;
    body.innerHTML = h;
    drawRuler(); drawWaves(); updateTimeline();
  }
  function barHtml(L, isSel, col) {
    const s = Math.min(L.start, D()), e = layerEnd(L), inner = [];
    if (L.type === "captions") {
      for (const c of L.chunks || []) {
        const a = outT(c.words[0].s), b = outT(c.words[c.words.length - 1].e);
        if (b < s || a > e) continue;
        inner.push(`<span class="chunk" style="left:${(Math.max(a, s) - s) * pps}px;width:${Math.max(2, (Math.min(b, e) - Math.max(a, s)) * pps - 1)}px">${esc(c.words.map(w => w.w).join(" "))}</span>`);
      }
    } else {
      inner.push(esc(layerName(L)));
      for (const t of keyTimes(L)) if (t >= s && t <= e) inner.push(`<span class="mark" style="left:${(t - s) * pps}px"></span>`);
    }
    return `<div class="lbar ${isSel ? "sel" : ""} ${L.hidden ? "dim" : ""}" data-bar="${L.id}" style="left:${s * pps}px;width:${Math.max(6, (e - s) * pps)}px;--lc:${col}cc"><i class="h a"></i>${inner.join("")}<i class="h b"></i></div>`;
  }
  function drawRuler() {
    const ruler = $("ruler"); if (!ruler) return;
    const d = D(), step = tickStep(), minor = step / 5;
    let html = "";
    if (minor * pps >= 12) for (let t = 0; t <= d + 1e-6; t += minor) html += `<b class="s" style="left:${t * pps}px"></b>`;
    for (let t = 0; t <= d + 1e-6; t += step) html += `<b style="left:${t * pps}px"></b><span style="left:${t * pps}px">${step < 1 ? tfmt(t) : tfmt(t).replace(/\.0$/, "")}</span>`;
    ruler.innerHTML = html;
  }
  function drawWaveInto(canvas, from, to) {
    if (!canvas || !peaks.length) return;
    const w = Math.min(16000, Math.max(1, Math.round(canvas.clientWidth))), h = Math.max(1, canvas.clientHeight), d2 = Math.min(2, devicePixelRatio || 1);
    canvas.width = w * d2; canvas.height = h * d2;
    const g = canvas.getContext("2d"); g.scale(d2, d2); g.clearRect(0, 0, w, h);
    const n = peaks.length, dur = meta.duration;
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "rgba(127,127,127,.4)";
    for (let x = 0; x < w; x++) {
      const t0 = from + (to - from) * x / w, t1 = from + (to - from) * (x + 1) / w;
      let i0 = Math.floor(t0 / dur * n), i1 = Math.max(i0 + 1, Math.ceil(t1 / dur * n)), m = 0;
      for (let i = i0; i < i1 && i < n; i++) if (i >= 0) m = Math.max(m, peaks[i]);
      const bh = Math.max(1, m * (h - 2));
      g.fillRect(x, (h - bh) / 2, 1, bh);
    }
  }
  function drawWaves() {
    const tw = $("tl-wave");
    if (tw) { tw.style.width = `${D() * pps}px`; drawWaveInto(tw, doc.trim[0], doc.trim[1]); }
    drawWaveInto($("src-wave"), 0, meta.duration);
  }
  function updateTimeline(values) {
    if (!doc) return;
    const d = D(), dur = meta.duration, [a, b] = doc.trim;
    $("src-win").style.cssText = `left:${a / dur * 100}%;width:${(b - a) / dur * 100}%`;
    $("dim-a").style.cssText = `left:0;width:${a / dur * 100}%`;
    $("dim-b").style.cssText = `left:${b / dur * 100}%;right:0`;
    const hs = document.querySelectorAll("#tl-src .h");
    hs[0].style.left = `${a / dur * 100}%`; hs[1].style.left = `${b / dur * 100}%`;
    $("src-ph").style.left = `${(a + T * doc.speed) / dur * 100}%`;
    $("trim-info").textContent = `${tfmt(a)} – ${tfmt(b)}`;
    const ph = $("playhead");
    if (ph) ph.style.left = `${labW + T * pps}px`;
    $("time").textContent = `${tfmt(T)} / ${tfmt(d)}`;
    if (values) {
      for (const pv of body.querySelectorAll("[data-pv]")) { const L = layerById(pv.dataset.id); if (L) pv.textContent = fmtProp(pv.dataset.pv, valueAt(L, pv.dataset.pv, T)); }
      for (const bar of body.querySelectorAll(".lbar")) {
        const L = layerById(bar.dataset.bar); if (!L) continue;
        const s = Math.min(L.start, d); bar.style.left = `${s * pps}px`; bar.style.width = `${Math.max(6, (layerEnd(L) - s) * pps)}px`;
      }
    } else for (const pv of body.querySelectorAll("[data-pv]")) { const L = layerById(pv.dataset.id); if (L) pv.textContent = fmtProp(pv.dataset.pv, valueAt(L, pv.dataset.pv, T)); }
    for (const kb of body.querySelectorAll("[data-ktog]")) { const L = layerById(kb.dataset.id); if (L) kb.innerHTML = keyIndex(L, kb.dataset.ktog, T) >= 0 ? ICON.diamond : ICON.diamondO; }
    if (playing) {                          // keep the playhead in view
      const x = labW + T * pps, left = body.scrollLeft, w = body.clientWidth;
      if (x > left + w - 40 || x < left + labW) body.scrollLeft = Math.max(0, x - labW - 40);
    }
  }
  function setZoom(f, anchorT) {
    const oldPps = pps, ax = anchorT == null ? null : labW + anchorT * oldPps - body.scrollLeft;
    zoomF = clamp(f, 1, 60);
    $("t-zoom").value = Math.round(Math.log(zoomF) / Math.log(60) * 100);
    renderTimeline();
    if (ax != null) body.scrollLeft = Math.max(0, labW + anchorT * pps - ax);
  }
  $("t-zoom").addEventListener("input", e => setZoom(Math.pow(60, e.target.value / 100), T));
  $("t-zin").onclick = () => setZoom(zoomF * 1.5, T);
  $("t-zout").onclick = () => setZoom(zoomF / 1.5, T);
  $("t-fit").onclick = () => setZoom(1);
  $("t-snap").onclick = e => e.currentTarget.classList.toggle("on");
  $("t-split").onclick = () => split();
  $("t-kf").onclick = () => {
    const L = selL(); if (!L) return toast("Select a layer first.");
    if (!propsFor(L).length) return toast("Captions can't have keyframes; use their style settings.");
    for (const p of propsFor(L)) setKey(L, p, T, valueAt(L, p, T));
    expanded.add(L.id); changed(); commit(); toast(`Keyframes added at ${tfmt(T)}. Move the playhead and change the layer to animate it.`);
  };
  $("t-prevk").onclick = () => jumpKey(-1);
  $("t-nextk").onclick = () => jumpKey(1);
  function jumpKey(dir) {
    const L = selL(), times = L ? keyTimes(L) : [...new Set(doc.layers.flatMap(x => keyTimes(x)))].sort((a, b) => a - b);
    const t = dir > 0 ? times.find(x => x > T + 1e-3) : [...times].reverse().find(x => x < T - 1e-3);
    if (t == null) return;
    pause(); T = t; seekVideo(); updateTimeline(true); refreshInspector();
  }
  body.addEventListener("wheel", e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const rect = $("ruler").getBoundingClientRect(), t = clamp((e.clientX - rect.left) / pps, 0, D());
    setZoom(zoomF * (e.deltaY < 0 ? 1.2 : 1 / 1.2), t);
  }, { passive: false });
  function snapTargets(exceptId) {
    const ts = [0, D(), T];
    for (const L of doc.layers) { if (L.id === exceptId) continue; ts.push(L.start, layerEnd(L)); }
    const L = layerById(exceptId); if (L) ts.push(...keyTimes(L));
    return ts;
  }
  function snapT(v, exceptId, alt) {
    if (!snapOn() || alt) { showSnap(null); return v; }
    let best = null;
    for (const t of snapTargets(exceptId)) { const d = Math.abs(v - t) * pps; if (d < 7 && (!best || d < best.d)) best = { t, d }; }
    showSnap(best ? best.t : null);
    return best ? best.t : v;
  }
  function showSnap(t) {
    let s = document.querySelector(".snapline");
    if (t == null) { if (s) s.remove(); return; }
    const grid = $("tl-grid"); if (!grid) return;
    if (!s) { s = el("div", "snapline"); grid.appendChild(s); }
    s.style.left = `${labW + t * pps}px`;
  }
  const timeAt = clientX => { const r = $("ruler").getBoundingClientRect(); return clamp((clientX - r.left) / pps, 0, D()); };
  body.addEventListener("dblclick", e => {
    const nm = e.target.closest("[data-name]"); if (!nm) return;
    const L = layerById(nm.dataset.name); if (!L) return;
    const inp = el("input"); inp.value = layerName(L); inp.maxLength = 40;
    nm.innerHTML = ""; nm.appendChild(inp); inp.focus(); inp.select();
    const done = ok => { if (ok) L.name = inp.value.trim().slice(0, 40); changed(); if (ok) commit(); };
    inp.onkeydown = ev => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); ev.stopPropagation(); };
    inp.onblur = () => done(true);
  });
  body.addEventListener("pointerdown", e => {
    if (!doc || e.button > 0) return;
    closeCtx();
    const t = e.target;
    if (t.closest("input, select")) return;
    const btn = sel_ => t.closest(sel_);
    let b;
    if ((b = btn("[data-eye]"))) { const L = layerById(b.dataset.eye); L.hidden = !L.hidden; if (L.hidden && sel === L.id) sel = null; buildInspector(); changed(); commit(); return; }
    if ((b = btn("[data-lock]"))) { const L = layerById(b.dataset.lock); L.locked = !L.locked; buildInspector(); changed(); commit(); return; }
    if ((b = btn("[data-twirl]"))) { const id = b.dataset.twirl; expanded.has(id) ? expanded.delete(id) : expanded.add(id); renderTimeline(); return; }
    if ((b = btn("[data-sw]"))) { const L = layerById(b.dataset.id); toggleAnimate(L, b.dataset.sw); buildInspector(); changed(); commit(); return; }
    if ((b = btn("[data-ktog]"))) { const L = layerById(b.dataset.id); toggleKeyHere(L, b.dataset.ktog); buildInspector(); changed(); commit(); return; }
    if ((b = btn("[data-kprev]")) || (b = btn("[data-knext]"))) {
      const L = layerById(b.dataset.id), prop = b.dataset.kprev || b.dataset.knext, times = keyTimes(L, prop);
      const tt = b.dataset.knext ? times.find(x => x > T + 1e-3) : [...times].reverse().find(x => x < T - 1e-3);
      if (tt != null) { pause(); T = tt; seekVideo(); updateTimeline(true); refreshInspector(); }
      return;
    }
    if ((b = btn("[data-addvideo]"))) { addFullVideo(); return; }
    if ((b = btn("[data-grip]"))) { tlDrag = { kind: "reorder", id: b.dataset.grip, y0: e.clientY }; capture(body, e); return; }
    if ((b = btn(".kf"))) {
      const L = layerById(b.dataset.id), prop = b.dataset.prop, kt = +b.dataset.kf;
      if (sel !== L.id) select(L.id, false);
      selKey = { id: L.id, prop, t: kt };
      tlDrag = { kind: "key", id: L.id, prop, t0: kt, x0: e.clientX, moved: false };
      capture(body, e); buildInspector(); renderTimeline(); return;
    }
    if ((b = btn(".lbar"))) {
      const L = layerById(b.dataset.bar);
      if (sel !== L.id) { selKey = null; select(L.id); }
      const kind = t.matches("i.h.a") ? "start" : t.matches("i.h.b") ? "end" : "bar";
      tlDrag = { kind, id: L.id, x0: e.clientX, s: L.start, e: layerEnd(L), kf: JSON.parse(JSON.stringify(L.kf || {})), moved: false, chunkT: t.closest(".chunk") ? timeAt(e.clientX) : null };
      capture(body, e); return;
    }
    if ((b = btn(".cell-l"))) { if (b.dataset.row) { selKey = null; select(b.dataset.row); } return; }
    if (btn(".ruler") || btn(".cell-t")) {
      tlDrag = { kind: "seek" }; capture(body, e); tlMove(e);
      if (selKey) { selKey = null; buildInspector(); renderTimeline(); }
    }
  });
  function tlMove(e) {
    if (!tlDrag) return;
    const d = D();
    if (tlDrag.kind === "seek") { pause(); T = timeAt(e.clientX); seekVideo(); updateTimeline(true); refreshInspector(); return; }
    if (tlDrag.kind === "reorder") {
      const rows = [...body.querySelectorAll(".cell-l[data-row]:not(.prop)")];
      let idx = rows.length;
      for (let i = 0; i < rows.length; i++) { const r = rows[i].getBoundingClientRect(); if (e.clientY < r.top + r.height / 2) { idx = i; break; } }
      tlDrag.to = idx;
      let line = body.querySelector(".dropline");
      if (!line) { line = el("div", "dropline"); $("tl-grid").appendChild(line); }
      const gr = $("tl-grid").getBoundingClientRect(), ref = rows[Math.min(idx, rows.length - 1)].getBoundingClientRect();
      line.style.top = `${(idx < rows.length ? ref.top : ref.bottom) - gr.top - 1}px`;
      return;
    }
    const L = layerById(tlDrag.id); if (!L) return;
    const dt = (e.clientX - tlDrag.x0) / pps;
    if (Math.abs(e.clientX - tlDrag.x0) > 2) tlDrag.moved = true;
    if (!tlDrag.moved) return;
    if (tlDrag.kind === "key") {
      const ks = L.kf[tlDrag.prop], q = ks.find(x => Math.abs(x[0] - selKey.t) < 1e-3);
      if (!q) return;
      const nt = r3(clamp(snapT(tlDrag.t0 + dt, L.id, e.altKey), 0, d));
      if (ks.some(x => x !== q && Math.abs(x[0] - nt) < 0.02)) return;
      q[0] = nt; selKey.t = nt; ks.sort((a, b) => a[0] - b[0]);
      changed(false); renderTimeline(); return;
    }
    if (tlDrag.kind === "bar") {
      const len = tlDrag.e - tlDrag.s;
      let s = tlDrag.s + dt;
      const sStart = snapT(s, L.id, e.altKey);
      if (sStart !== s) s = sStart; else { const sEnd = snapT(s + len, L.id, e.altKey); s = sEnd - len; }
      s = clamp(s, 0, Math.max(0, d - len));
      const moveBy = r3(s - tlDrag.s);
      L.start = r2(Math.max(0, s)); L.end = s + len >= d - 0.05 ? (tlDrag.e >= d - 0.05 ? null : r2(s + len)) : r2(s + len);
      L.kf = JSON.parse(JSON.stringify(tlDrag.kf)); shiftKeys(L, moveBy);
    } else if (tlDrag.kind === "start") L.start = r2(clamp(snapT(tlDrag.s + dt, L.id, e.altKey), 0, tlDrag.e - 0.1));
    else { const v = clamp(snapT(tlDrag.e + dt, L.id, e.altKey), L.start + 0.1, d); L.end = v >= d - 0.05 ? null : r2(v); }
    changed(false); renderTimeline();
  }
  body.addEventListener("pointermove", tlMove);
  const tlEnd = () => {
    if (!tlDrag) return;
    const dr = tlDrag; tlDrag = null; showSnap(null);
    if (dr.kind === "reorder") {
      body.querySelectorAll(".dropline").forEach(x => x.remove());
      if (dr.to != null) {
        const rowsIds = [...doc.layers].reverse().map(L => L.id), from = rowsIds.indexOf(dr.id);
        let to = dr.to; if (to > from) to--;
        if (to !== from) { rowsIds.splice(to, 0, rowsIds.splice(from, 1)[0]); doc.layers = rowsIds.reverse().map(id => layerById(id)); changed(); commit(); }
      }
      return;
    }
    if (dr.moved) { commit(); renderTimeline(); buildInspector(); }
    else if (dr.kind === "bar" && dr.chunkT != null) { pause(); T = dr.chunkT; seekVideo(); updateTimeline(true); refreshInspector(); }
  };
  body.addEventListener("pointerup", tlEnd);
  body.addEventListener("pointercancel", tlEnd);
  body.addEventListener("change", e => {
    const s = e.target.closest("[data-ease]"); if (!s) return;
    const L = layerById(s.dataset.id), q = L.kf[s.dataset.ease].find(x => Math.abs(x[0] - selKey.t) < 1e-3);
    if (q) { q[q.length - 1] = s.value; changed(); commit(); buildInspector(); }
  });
  body.addEventListener("contextmenu", e => {
    const row = e.target.closest("[data-row], [data-bar], [data-track]");
    if (!row) return;
    e.preventDefault();
    const id = row.dataset.row || row.dataset.bar || row.dataset.track;
    if (sel !== id) select(id);
    layerMenu(id, e.clientX, e.clientY);
  });
  cv.addEventListener("contextmenu", e => {
    const L = hitLayer(toDesign(e)); if (!L) return;
    e.preventDefault(); if (sel !== L.id) select(L.id); layerMenu(L.id, e.clientX, e.clientY);
  });
  // source strip (trim)
  const src = $("tl-src");
  let srcDrag = null;
  src.addEventListener("pointerdown", e => {
    const h = e.target.closest(".h");
    srcDrag = h ? { kind: "trim", i: +h.dataset.h } : { kind: "srcseek" };
    capture(src, e); srcMove(e);
  });
  function srcMove(e) {
    if (!srcDrag) return;
    const r = src.getBoundingClientRect(), s = clamp((e.clientX - r.left) / r.width * meta.duration, 0, meta.duration);
    pause();
    if (srcDrag.kind === "trim") { setTrim(srcDrag.i, s); T = srcDrag.i === 1 ? D() : 0; seekVideo(); srcDrag.moved = true; changed(false); drawRuler(); drawWaves(); }
    else { T = clamp((s - doc.trim[0]) / doc.speed, 0, D()); seekVideo(); updateTimeline(true); }
  }
  src.addEventListener("pointermove", srcMove);
  const srcEnd = () => { if (srcDrag && srcDrag.moved) { commit(); renderTimeline(); buildInspector(); } srcDrag = null; };
  src.addEventListener("pointerup", srcEnd);
  src.addEventListener("pointercancel", srcEnd);
  new ResizeObserver(() => { if (doc) renderTimeline(); }).observe(body);

  // ================= context menu =================
  const ctxEl = $("ctx");
  function closeCtx() { ctxEl.hidden = true; }
  function layerMenu(id, x, y) {
    const L = layerById(id); if (!L) return;
    const items = [
      ["Rename", "", () => { const nm = body.querySelector(`[data-name="${id}"]`); if (nm) nm.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); }],
      ["Duplicate", "Ctrl+D", () => duplicate(id)],
      ["Split at playhead", "Ctrl+Shift+D", () => split(id)],
      null,
      ["Bring to front", "", () => moveZ(id, "top")],
      ["Bring forward", "Ctrl+]", () => moveZ(id, 1)],
      ["Send backward", "Ctrl+[", () => moveZ(id, -1)],
      ["Send to back", "", () => moveZ(id, "bottom")],
      null,
      [propsFor(L).length ? (expanded.has(id) ? "Hide keyframes" : "Show keyframes") : null, "P", () => { expanded.has(id) ? expanded.delete(id) : expanded.add(id); renderTimeline(); }],
      ["Start at playhead", "[", () => { moveLayerTime(L, T - L.start); }],
      ["Trim start to playhead", "Alt+[", () => { if (T < layerEnd(L) - 0.1) { L.start = r2(T); changed(); commit(); } }],
      ["Trim end to playhead", "Alt+]", () => { if (T > L.start + 0.1) { L.end = T >= D() - 0.05 ? null : r2(T); changed(); commit(); } }],
      null,
      [L.hidden ? "Show" : "Hide", "", () => { L.hidden = !L.hidden; changed(); commit(); }],
      [L.locked ? "Unlock" : "Lock", "", () => { L.locked = !L.locked; buildInspector(); changed(); commit(); }],
      ["Delete", "Del", () => removeLayer(id), "danger"],
    ];
    ctxEl.innerHTML = "";
    for (const it of items) {
      if (!it) { ctxEl.appendChild(el("hr")); continue; }
      if (!it[0]) continue;
      const b = el("button", it[3] || "", `<span>${esc(it[0])}</span><kbd>${esc(it[1])}</kbd>`); b.type = "button";
      b.onclick = () => { closeCtx(); it[2](); };
      ctxEl.appendChild(b);
    }
    ctxEl.hidden = false;
    const r = ctxEl.getBoundingClientRect();
    ctxEl.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`; ctxEl.style.top = `${Math.min(y, innerHeight - r.height - 8)}px`;
  }
  document.addEventListener("pointerdown", e => { if (!ctxEl.hidden && !e.target.closest("#ctx")) closeCtx(); });
  function moveLayerTime(L, dt) {
    const len = layerEnd(L) - L.start, ns = clamp(L.start + dt, 0, Math.max(0, D() - 0.1));
    const moveBy = ns - L.start;
    L.start = r2(ns); if (L.end != null) L.end = r2(Math.min(D(), ns + len));
    shiftKeys(L, moveBy); changed(); commit();
  }

  // ================= playback =================
  let raf = 0, ac = null, gain = null;
  function ensureAudio() {
    if (ac || !window.AudioContext) return;
    try { ac = new AudioContext(); gain = ac.createGain(); ac.createMediaElementSource(vid).connect(gain); gain.connect(ac.destination); }
    catch (_) { ac = null; gain = null; }
  }
  function applyVolume() {
    const v = userMuted ? 0 : doc.volume * fadeAt(T);
    if (gain) { gain.gain.value = v; vid.volume = 1; vid.muted = false; }
    else { vid.volume = clamp(v, 0, 1); vid.muted = v <= 0; }
  }
  function seekVideo() {
    if (!doc) return;
    const s = doc.trim[0] + T * doc.speed;
    if (Math.abs(vid.currentTime - s) > 0.01) vid.currentTime = s; else draw();
  }
  function play() {
    if (!doc || playing) return;
    ensureAudio(); if (ac && ac.state === "suspended") ac.resume();
    if (T >= D() - 0.05) T = 0;
    vid.playbackRate = doc.speed;
    vid.currentTime = doc.trim[0] + T * doc.speed;
    applyVolume();
    vid.play().then(() => { playing = true; paintPlay(); loop(); }).catch(() => {});
  }
  function pause() { if (!playing) return; playing = false; vid.pause(); cancelAnimationFrame(raf); paintPlay(); refreshInspector(); updateTimeline(true); }
  function loop() {
    if (!playing) return;
    const s = vid.currentTime;
    if (s >= doc.trim[1] - 0.01 || vid.ended) {
      if (!looping) { T = D(); pause(); draw(); return; }
      vid.currentTime = doc.trim[0]; T = 0; if (vid.ended) vid.play();
    } else T = clamp((s - doc.trim[0]) / doc.speed, 0, D());
    if (vid.currentTime !== lastDrawn) {      // only when the video has moved on to a new frame
      lastDrawn = vid.currentTime;
      applyVolume(); draw(); updateTimeline(); tickInspector();
    }
    raf = requestAnimationFrame(loop);
  }
  let lastTick = 0, lastDrawn = -1;
  function tickInspector() { const now = performance.now(); if (now - lastTick > 250) { lastTick = now; refreshInspector(); } }
  const paintPlay = () => {
    $("play").innerHTML = playing ? ICON.pause : ICON.play; $("mute").innerHTML = userMuted ? ICON.muted : ICON.sound;
    $("loop").classList.toggle("on", looping);
  };
  const step = dt => { pause(); T = clamp(T + dt, 0, D()); seekVideo(); updateTimeline(true); refreshInspector(); };
  $("play").onclick = () => playing ? pause() : play();
  $("to-start").onclick = () => { pause(); T = 0; seekVideo(); updateTimeline(true); };
  $("step-back").onclick = () => step(-1 / (meta ? meta.fps : 30));
  $("step-fwd").onclick = () => step(1 / (meta ? meta.fps : 30));
  $("loop").onclick = () => { looping = !looping; paintPlay(); };
  $("mute").onclick = () => { userMuted = !userMuted; applyVolume(); paintPlay(); };
  vid.addEventListener("seeked", () => { draw(); if (cropPaint) cropPaint(); if (reactPaint) reactPaint(); });
  vid.addEventListener("loadeddata", () => { draw(); if (cropPaint) cropPaint(); });
  vid.addEventListener("loadedmetadata", () => {
    seekVideo();
    // Phones only load frames after a gesture; a silent play/pause (allowed) brings the first frame in.
    setTimeout(() => {
      if (vid.readyState >= 2 || playing) return;
      vid.muted = true;
      vid.play().then(() => { vid.pause(); vid.muted = false; seekVideo(); }).catch(() => { vid.muted = false; });
    }, 600);
  });

  // ================= keyboard =================
  let nudgeT = 0;
  document.addEventListener("keydown", e => {
    if (!doc) return;
    const typing = e.target.closest("input, textarea, select, [contenteditable]") && e.target.type !== "range";
    const modal = ["react", "done", "capm", "keys", "welcome"].some(id => !$(id).hidden);
    if (modal) { if (e.key === "Escape") closeModals(); return; }
    if (e.key === "Escape") closeCtx();
    const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
    if (mod && key === "z" && !typing) { e.preventDefault(); e.shiftKey ? $("redo").click() : $("undo").click(); return; }
    if (mod && key === "y" && !typing) { e.preventDefault(); $("redo").click(); return; }
    if (typing) return;
    const L = selL();
    if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "?") $("keys").hidden = false;
    else if (e.key === "Escape") { if (selKey) { selKey = null; buildInspector(); renderTimeline(); } else select(null); }
    else if (e.key === "Home") { e.preventDefault(); pause(); T = 0; seekVideo(); updateTimeline(true); }
    else if (e.key === "End") { e.preventDefault(); pause(); T = D(); seekVideo(); updateTimeline(true); }
    else if ((e.key === "Delete" || e.key === "Backspace") && selKey && L) {
      e.preventDefault(); const i = keyIndex(L, selKey.prop, selKey.t); if (i >= 0) removeKey(L, selKey.prop, i); selKey = null; buildInspector(); changed(); commit();
    }
    else if ((e.key === "Delete" || e.key === "Backspace") && L) { e.preventDefault(); removeLayer(L.id); }
    else if (mod && e.shiftKey && key === "d") { e.preventDefault(); split(); }
    else if (mod && key === "d" && L) { e.preventDefault(); duplicate(L.id); }
    else if (mod && e.key === "]" && L) { e.preventDefault(); moveZ(L.id, 1); }
    else if (mod && e.key === "[" && L) { e.preventDefault(); moveZ(L.id, -1); }
    else if (e.altKey && e.key === "[" && L) { e.preventDefault(); if (T < layerEnd(L) - 0.1) { L.start = r2(T); changed(); commit(); } }
    else if (e.altKey && e.key === "]" && L) { e.preventDefault(); if (T > L.start + 0.1) { L.end = T >= D() - 0.05 ? null : r2(T); changed(); commit(); } }
    else if (e.key === "[" && L) moveLayerTime(L, T - L.start);
    else if (e.key === "]" && L) moveLayerTime(L, T - layerEnd(L));
    else if (mod && e.key.startsWith("Arrow") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault(); step((e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : 1 / meta.fps));
    }
    else if (L && !L.locked && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const s = e.shiftKey ? 10 : 1, [px, py] = valueAt(L, "pos", T);
      const dx = e.key === "ArrowLeft" ? -s : e.key === "ArrowRight" ? s : 0, dy = e.key === "ArrowUp" ? -s : e.key === "ArrowDown" ? s : 0;
      setProp(L, "pos", [px + dx, py + dy]);
      changed(false); clearTimeout(nudgeT); nudgeT = setTimeout(commit, 400);
    }
    else if (!L && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); step((e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : 1 / meta.fps)); }
    else if (key === "j") jumpKey(-1);
    else if (key === "k") jumpKey(1);
    else if (key === "m") $("mute").click();
    else if (key === "t" && !mod) addKind("text");
    else if (["p", "s", "r", "o"].includes(key) && L && propsFor(L).length && !mod) { if (expanded.has(L.id)) expanded.delete(L.id); else expanded.add(L.id); renderTimeline(); }
  });

  // ================= reaction layout =================
  let reactOpts = { camTop: true, split: 0.42, game: "fill", corner: "auto", pip: 0.32 };
  let reactDet = null, reactPaint = null;
  Object.assign(reactOpts, load("rc_react_opts", {}));
  const vertical = () => Hd() > Wd() * 1.05, square = () => Math.abs(Hd() - Wd()) <= Wd() * 0.05;
  function cropInside(rect, A, face) {
    const [x, y, w, h] = rect, sw = meta.w, sh = meta.h;
    let cw = w, ch = h;
    if ((w * sw) / (h * sh) > A) cw = (h * sh * A) / sw; else ch = (w * sw / A) / sh;
    const fcx = face ? face[0] + face[2] / 2 : x + w / 2, fcy = face ? face[1] + face[3] * 0.9 : y + h / 2;
    return [clamp(fcx - cw / 2, x, x + w - cw), clamp(fcy - ch / 2, y, y + h - ch), cw, ch];
  }
  function overlap(a, b) {
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]), h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }
  function cropAvoiding(rect, A, avoid) {
    const base = cropInside(rect, A, null), [x, y, w, h] = rect;
    let best = base, bestO = avoid ? overlap(base, avoid) : 0, bestD = 0;
    if (!avoid || bestO === 0) return base;
    const horiz = base[2] < w - 1e-4;
    for (let i = 0; i <= 40; i++) {
      const c = [...base];
      if (horiz) c[0] = x + (w - c[2]) * i / 40; else c[1] = y + (h - c[3]) * i / 40;
      const o = overlap(c, avoid), dd = Math.abs(horiz ? c[0] - base[0] : c[1] - base[1]);
      if (o < bestO - 1e-6 || (Math.abs(o - bestO) < 1e-6 && dd < bestD)) { best = c; bestO = o; bestD = dd; }
    }
    return best;
  }
  function applyReaction(o, det) {
    const W = Wd(), H = Hd(), cam = det.cam, game = det.game || [0, 0, 1, 1];
    const backs = doc.layers.filter(L => L.backdrop), others = doc.layers.filter(L => L.type !== "video"), vids = [];
    const base = () => ({ ...baseLayer(), flip: false, blur: 0, bright: 1 });
    if (vertical() || square()) {
      const ch = Math.round(H * o.split), gh = H - ch;
      const camBox = o.camTop ? [0, 0, W, ch] : [0, gh, W, ch], region = o.camTop ? [0, ch, W, gh] : [0, 0, W, gh];
      let g;
      if (o.game === "fit") {
        const a = (game[2] * meta.w) / (game[3] * meta.h);
        let w = W, h = W / a; if (h > region[3]) { h = region[3]; w = h * a; }
        g = { crop: game, x: (W - w) / 2, y: region[1] + (region[3] - h) / 2, w, h };
      } else g = { crop: cropAvoiding(game, region[2] / region[3], cam), x: region[0], y: region[1], w: region[2], h: region[3] };
      vids.push({ ...base(), id: "game", type: "video", name: "Gameplay", ...g });
      if (cam) vids.push({ ...base(), id: "cam", type: "video", name: "Reaction cam", crop: cropInside(cam, camBox[2] / camBox[3], det.face),
                           x: camBox[0], y: camBox[1], w: camBox[2], h: camBox[3] });
    } else {
      vids.push({ ...base(), id: "game", type: "video", name: "Gameplay", crop: cropInside(game, W / H, null), x: 0, y: 0, w: W, h: H });
      if (cam) {
        const a = (cam[2] * meta.w) / (cam[3] * meta.h), w = W * o.pip, h = w / a, m = W * 0.025;
        let x, y;
        if (o.corner === "auto") {
          const g = vids[0], ccx = g.x + ((cam[0] + cam[2] / 2 - g.crop[0]) / g.crop[2]) * g.w, ccy = g.y + ((cam[1] + cam[3] / 2 - g.crop[1]) / g.crop[3]) * g.h;
          x = clamp(ccx - w / 2, 0, W - w); y = clamp(ccy - h / 2, 0, H - h);
        } else { x = o.corner.includes("l") ? m : W - w - m; y = o.corner.includes("t") ? m : H - h - m; }
        vids.push({ ...base(), id: "cam", type: "video", name: "Reaction cam", crop: cam, w, h, x, y });
      }
    }
    for (const L of vids) { for (const key of ["x", "y", "w", "h"]) L[key] = r2(L[key]); L.crop = L.crop.map(c => Math.round(c * 1e5) / 1e5); }
    doc.layers = [...backs, ...vids, ...others];
    if (sel && !layerById(sel)) sel = null;
  }
  function reactOptions() {
    const box = $("r-opts"); box.innerHTML = "";
    const segs = [];
    const mkSeg = (opts, get, set) => {
      const w = el("div", "seg2");
      for (const [v, label] of opts) { const b = el("button", "", esc(label)); b.type = "button"; b.dataset.v = v; b.onclick = () => { set(v); segs.forEach(s => s._paint()); }; w.appendChild(b); }
      w._paint = () => w.querySelectorAll("button").forEach(b => b.classList.toggle("on", String(get()) === b.dataset.v));
      w._paint(); segs.push(w); return w;
    };
    const rng = (label, min, max, key) => {
      const s = el("label", "ctl"), out = el("output"), inp = el("input");
      Object.assign(inp, { type: "range", min, max, step: 0.01, value: reactOpts[key] });
      out.textContent = `${Math.round(reactOpts[key] * 100)}%`;
      inp.oninput = () => { reactOpts[key] = +inp.value; out.textContent = `${Math.round(reactOpts[key] * 100)}%`; };
      s.append(el("span", "", label), out, inp); return s;
    };
    const parts = [];
    if (vertical() || square()) {
      parts.push(["Layout", mkSeg([["true", "Cam on top"], ["false", "Cam below"]], () => String(reactOpts.camTop), v => reactOpts.camTop = v === "true")]);
      parts.push(["", rng("Cam height", 0.2, 0.6, "split")]);
      parts.push(["Gameplay", mkSeg([["fill", "Fill"], ["fit", "Whole frame"]], () => reactOpts.game, v => reactOpts.game = v)]);
      parts.push(["", el("div", "note", "Fill crops the gameplay to fill its part (the cam is kept out of it where possible). Whole frame shows all of it with the blurred background around it.")]);
    } else {
      parts.push(["Cam corner", mkSeg([["auto", "Auto"], ["tl", "↖"], ["tr", "↗"], ["bl", "↙"], ["br", "↘"]], () => reactOpts.corner, v => reactOpts.corner = v)]);
      parts.push(["", rng("Cam size", 0.15, 0.5, "pip")]);
      parts.push(["", el("div", "note", `${doc.format} is landscape, so the gameplay fills the frame and the cam sits on top of the streamer's own cam. Pick 9:16 at the top for a vertical short.`)]);
    }
    for (const [label, node] of parts) {
      const w = el("div", ""); if (label) { const n = el("div", "note", esc(label)); n.style.marginBottom = "6px"; w.appendChild(n); }
      w.appendChild(node); box.appendChild(w);
    }
  }
  async function openReaction(again = false) {
    if (!doc) return;
    pause();
    $("react").hidden = false;
    reactOptions();
    $("r-apply").disabled = true;
    $("r-status").textContent = "Looking for the streamer's cam and the gameplay… (about 10 seconds)";
    const canvas = $("r-cv");
    reactPaint = null;
    const paintBoxes = () => {
      if (!reactDet) return;
      const rects = [{ get: () => reactDet.game, set: r => reactDet.game = r, color: "#ffffff", label: "Gameplay" }];
      if (reactDet.cam) rects.push({ get: () => reactDet.cam, set: r => reactDet.cam = r, color: "#0865ff", label: "Cam" });
      reactPaint = rectEditor(canvas, rects, () => {}, null);
    };
    rectEditor(canvas, [], () => {}, null);
    try {
      let det = !again && P.detect && P.detect.game ? P.detect : null;
      if (!det) {
        const r = await fetch(`/api/editor/${PID}/detect${again ? "?again=1" : ""}`, { method: "POST" });
        det = await r.json();
        if (!r.ok) throw new Error(det.error || "Detection failed.");
        P.detect = det;
      }
      reactDet = JSON.parse(JSON.stringify(det));
      if (!reactDet.game) reactDet.game = [0, 0, 1, 1];
      if (!reactDet.cam) { reactDet.cam = [0.7, 0.05, 0.28, 0.28]; $("r-status").textContent = "Couldn't find a face, so the cam box is a guess: drag it onto the streamer's cam."; }
      else $("r-status").textContent = det.cam_confidence >= 0.5 ? "Found the streamer's cam (orange) and the gameplay (white). Adjust the boxes if needed, then apply."
        : "Found the streamer's face, but not a clear cam border: check the orange box and adjust it if needed.";
      paintBoxes(); $("r-apply").disabled = false;
    } catch (err) {
      $("r-status").textContent = `${err.message} Drag the boxes onto the cam and the gameplay yourself.`;
      reactDet = { cam: [0.7, 0.05, 0.28, 0.28], game: [0, 0, 1, 1] }; paintBoxes(); $("r-apply").disabled = false;
    }
  }
  $("react-open").onclick = () => openReaction(false);
  $("r-again").onclick = () => openReaction(true);
  $("r-apply").onclick = () => {
    store("rc_react_opts", reactOpts);
    P.detect = { ...(P.detect || {}), cam: reactDet.cam, game: reactDet.game, face: reactDet.face };
    applyReaction(reactOpts, reactDet);
    closeModals(); buildInspector(); changed(); commit();
    toast("Reaction layout applied. Click a view to fine-tune its crop.");
  };

  // ================= Valorant POV layout =================
  // The player's view fills the frame, centred on the crosshair and zoomed in a
  // little; the kill feed (top-right corner of the game) is cut out as its own
  // video layer and placed back at the top, so every kill stays readable.
  function applyPOV(zoom = 1.18) {
    if (!doc) return;
    pause();
    const W = Wd(), H = Hd(), u = Math.min(W, H), KF = C.KILLFEED;
    const backs = doc.layers.filter(L => L.backdrop), others = doc.layers.filter(L => L.type !== "video");
    const base = () => ({ ...baseLayer(), flip: false, blur: 0, bright: 1 });
    let cw = (W / H) / (meta.w / meta.h) / zoom, ch = 1 / zoom;
    if (cw > 1 / zoom) { ch = ch / (cw * zoom); cw = 1 / zoom; }     // wide outputs: keep the frame's shape
    const game = { ...base(), id: "pov", type: "video", name: "Player POV", crop: [(1 - cw) / 2, (1 - ch) / 2, cw, ch], x: 0, y: 0, w: W, h: H };
    const kw = vertical() ? W * 0.56 : square() ? W * 0.45 : W * 0.3;
    const kh = kw * (KF[3] * meta.h) / (KF[2] * meta.w);
    const feed = { ...base(), id: "killfeed", type: "video", name: "Kill feed", crop: [...KF], w: kw, h: kh,
                   x: W - kw - u * 0.035, y: vertical() ? H * 0.11 : u * 0.035 };
    for (const L of [game, feed]) { for (const key of ["x", "y", "w", "h"]) L[key] = r2(L[key]); L.crop = L.crop.map(c => Math.round(c * 1e5) / 1e5); }
    doc.layers = [...backs, game, feed, ...others];
    sel = null; select(feed.id); buildInspector(); changed(); commit();
    toast(vertical() ? "POV layout applied: player view centred on the crosshair, kill feed at the top. Drag the kill feed to move it."
                     : "POV layout applied. Pick 9:16 at the top for a vertical reel.");
  }
  $("pov-apply").onclick = () => applyPOV();
  document.addEventListener("themechange", () => { if (doc) { renderTimeline(); draw(); } });

  // ================= auto captions =================
  let capOpts = Object.assign({ style: "bold", per: 3, quality: "best", language: "", vocab: "" }, load("rc_cap_opts", {}));
  let capTimer = null;
  (() => {
    const sl = $("cap-lang");
    for (const [v, n] of LANGS) { const o = el("option", "", esc(n)); o.value = v; sl.appendChild(o); }
    const cards = styleCards(() => capOpts.style, v => { capOpts.style = v; capOpts.per = CAP_STYLES[v].per; paintCapForm(); });
    ups = ups.filter(u => u !== ups[ups.length - 1]);
    $("cap-styles").appendChild(cards); $("cap-styles")._cards = cards;
    $("cap-words").onclick = e => { const b = e.target.closest("button"); if (b) { capOpts.per = +b.dataset.v; paintCapForm(); } };
    $("cap-q").onclick = e => { const b = e.target.closest("button"); if (b) { capOpts.quality = b.dataset.v; paintCapForm(); } };
  })();
  function paintCapForm() {
    $("cap-styles").querySelectorAll(".style-card").forEach(x => x.classList.toggle("on", x.dataset.v === capOpts.style));
    $("cap-words").querySelectorAll("button").forEach(b => b.classList.toggle("on", +b.dataset.v === capOpts.per));
    $("cap-q").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === capOpts.quality));
    $("cap-lang").value = capOpts.language; $("cap-vocab").value = capOpts.vocab;
  }
  function openCaptions() {
    if (!doc) return;
    pause();
    $("capm").hidden = false; $("cap-err").hidden = true;
    paintCapForm();
    const a = P.asr || {};
    $("cap-reuse").hidden = !(a.state === "done" && Math.abs((a.start || 0) - doc.trim[0]) < 0.05 && Math.abs((a.end || 0) - doc.trim[1]) < 0.05);
    if (!P.asr_available) { $("cap-err").hidden = false; $("cap-err").textContent = "Speech to text isn't installed on the server (pip install faster-whisper)."; $("cap-go").disabled = true; }
    else if (!meta.audio) { $("cap-err").hidden = false; $("cap-err").textContent = "This video has no sound."; $("cap-go").disabled = true; }
    else $("cap-go").disabled = a.state === "running";
    if (a.state === "running") { showCapProgress(a); pollAsr(); } else { $("cap-prog").hidden = true; $("cap-form").hidden = false; }
  }
  $("cap-open").onclick = openCaptions;
  function readCapForm() { capOpts.language = $("cap-lang").value; capOpts.vocab = $("cap-vocab").value.trim(); store("rc_cap_opts", capOpts); }
  $("cap-go").onclick = async () => {
    readCapForm();
    $("cap-err").hidden = true; $("cap-go").disabled = true;
    await save();
    const r = await fetch(`/api/editor/${PID}/transcribe`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality: capOpts.quality, language: capOpts.language, vocab: capOpts.vocab }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { $("cap-err").hidden = false; $("cap-err").textContent = j.error || "Couldn't start."; $("cap-go").disabled = false; return; }
    P.asr = { state: "running", stage: "Starting" }; showCapProgress(P.asr); pollAsr();
  };
  $("cap-reuse").onclick = () => { readCapForm(); finishCaptions(); };
  function showCapProgress(a) {
    $("cap-form").hidden = true; $("cap-prog").hidden = false;
    $("cap-stage").innerHTML = `<span class="spin"></span>${esc(a.stage || "Working")}`;
    $("cap-pc").textContent = a.progress != null ? `${Math.round(a.progress * 100)}%` : "";
    $("cap-bar").style.width = `${a.progress != null ? a.progress * 100 : 6}%`;
    $("cap-note").hidden = !/Download/.test(a.stage || "");
  }
  async function pollAsr() {
    clearTimeout(capTimer);
    const j = await fetch(`/api/editor/${PID}`).then(r => r.json()).catch(() => null);
    if (j) {
      P.asr = j.asr || {};
      if (P.asr.state === "running") { if (!$("capm").hidden) showCapProgress(P.asr); capTimer = setTimeout(pollAsr, 1000); return; }
      if (P.asr.state === "failed") {
        if (!$("capm").hidden) { $("cap-prog").hidden = true; $("cap-form").hidden = false; $("cap-err").hidden = false; $("cap-err").textContent = `Transcription failed: ${P.asr.error || "unknown error"}`; $("cap-go").disabled = false; }
        else toast("Transcription failed.");
        return;
      }
      if (P.asr.state === "done") finishCaptions();
    } else capTimer = setTimeout(pollAsr, 2000);
  }
  async function finishCaptions() {
    const tr = await fetch(`/api/editor/${PID}/transcript`).then(r => r.ok ? r.json() : null).catch(() => null);
    if (!tr) { toast("Couldn't load the transcript."); return; }
    const words = tr.words || [];
    closeModals();
    if (!words.length) return toast("No speech found in this part of the video.");
    const W = Wd(), H = Hd(), st = CAP_STYLES[capOpts.style] || CAP_STYLES.bold;
    let L = doc.layers.find(x => x.type === "captions");
    const fresh = !L;
    if (!L) {
      L = { ...baseLayer(), id: "captions", type: "captions", name: "Captions", x: r2(W * 0.06), w: r2(W * 0.88), h: 100, y: 0, chunks: [] };
    }
    L.chunks = [{ words: words.map(w => ({ ...w })) }];
    applyStyle(L, capOpts.style);
    rechunk(L, capOpts.per || st.per);
    if (fresh) { L.h = r2(L.size * C.LINE_H * 2.6); L.y = r2(H * (H > W ? 0.66 : 0.8) - L.h / 2); doc.layers.push(L); }
    asrApplied = true; if (P.asr) P.asr.applied = true;
    select(L.id); changed(); commit();
    toast(`Added ${L.chunks.length} captions (${words.length} words, ${tr.info && tr.info.language ? tr.info.language.toUpperCase() : ""}). Edit any word in the panel on the right.`);
  }

  // ================= export =================
  let expSize = "1080", expTimer = null;
  function paintExport() {
    const r = P.render || {};
    $("exp-size").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === expSize));
    const [W, H] = WH(), f = expSize === "1080" ? 1 : 720 / 1080;
    $("exp-info").textContent = `${doc.format} · ${Math.round(W * f / 2) * 2}×${Math.round(H * f / 2) * 2} · ${tfmt(D())} · H.264 + AAC`;
    const busy = r.state === "queued" || r.state === "rendering";
    $("exp-prog").hidden = !busy;
    if (busy) {
      const pc = Math.round((r.progress || 0) * 100), enc = r.stage === "Encoding";
      const left = enc && r.eta != null && pc > 3 ? ` · ${r.eta < 60 ? `${Math.max(1, r.eta)} s` : `${Math.round(r.eta / 60)} min`} left` : "";
      $("exp-txt").textContent = r.state === "queued" ? "Waiting for a free encoder…" : enc ? `Exporting ${pc}%${left}` : `${r.stage || "Preparing"}…`;
      $("exp-bar").style.width = `${enc ? (r.progress || 0) * 100 : 4}%`;
      document.title = `${r.state === "queued" ? "Waiting" : enc ? `${pc}%` : "Preparing"} · ${$("name").value} — Clipforge`;
    } else document.title = `${$("name").value} — Clipforge editor`;
    $("exp-open").textContent = busy ? "Exporting…" : "Export";
    $("exp-go").disabled = busy;
    $("exp-last").hidden = r.state !== "done";
  }
  $("exp-open").onclick = e => { e.stopPropagation(); $("exp-pop").hidden = !$("exp-pop").hidden; paintExport(); };
  document.addEventListener("click", e => { if (!e.target.closest(".exp-wrap")) $("exp-pop").hidden = true; });
  $("exp-size").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { expSize = b.dataset.v; doc.size = expSize; paintExport(); changed(false); } });
  $("exp-go").onclick = async () => {
    pause(); doc.size = expSize;
    const r = await fetch(`/api/editor/${PID}/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ doc }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return r.status === 402 || r.status === 503 ? null : toast(j.error || "Couldn't start the export.");  // those two are shown by recaply.js
    if (j.watermark) toast("Exporting with a Clipforge watermark (free plan). Upgrade to remove it.");
    dirty = false; paintSaved("Saved", "ok");
    P.render = { state: "queued", progress: 0 }; $("exp-pop").hidden = true; paintExport(); pollExport();
  };
  $("exp-last").onclick = () => { $("exp-pop").hidden = true; showDone(); };
  async function pollExport() {
    clearTimeout(expTimer);
    const j = await fetch(`/api/editor/${PID}`).then(r => r.json()).catch(() => null);
    if (j) {
      const before = (P.render || {}).state; P.render = j.render; paintExport();
      if (j.render.state === "done" && before !== "done") showDone();
      if (j.render.state === "failed") toast(`Export failed: ${j.render.error || "unknown error"}`);
    }
    if (["queued", "rendering"].includes((P.render || {}).state)) expTimer = setTimeout(pollExport, 1000);
  }
  function showDone() {
    const r = P.render;
    $("d-player").src = `/editor/${PID}/export.mp4?v=${encodeURIComponent(r.file || "")}`;
    $("d-dl").href = `/editor/${PID}/export.mp4?dl=1&v=${encodeURIComponent(r.file || "")}`;
    $("d-info").textContent = `${r.format || ""} · ${r.size_mb} MB${r.seconds != null ? ` · made in ${r.seconds}s` : ""}`;
    $("done").hidden = false;
  }

  // ================= modals, toasts, resizer =================
  function closeModals() {
    $("react").hidden = true; reactPaint = null; $("capm").hidden = true; $("keys").hidden = true; $("welcome").hidden = true; clearTimeout(capTimer);
    const p = $("d-player"); p.pause(); p.removeAttribute("src"); p.load(); $("done").hidden = true;
  }
  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closeModals));
  $("keys-open").onclick = () => { $("keys").hidden = false; };
  let toastT = 0;
  function toast(msg, action) {
    document.querySelectorAll(".toast").forEach(t => t.remove());
    const t = el("div", "toast", `<span>${esc(msg)}</span>`);
    if (action) { const b = el("button", "", esc(action.label)); b.onclick = () => { t.remove(); action.fn(); }; t.appendChild(b); }
    document.body.appendChild(t);
    clearTimeout(toastT); toastT = setTimeout(() => t.remove(), action ? 6000 : 4200);
  }
  (() => {
    const rz = $("resizer"), root = document.documentElement;
    const saved = load("rc_tl_h", 0); if (saved) root.style.setProperty("--tlh", `${saved}px`);
    let y0 = 0, h0 = 0, on = false;
    rz.addEventListener("pointerdown", e => { on = true; y0 = e.clientY; h0 = $("tl").getBoundingClientRect().height; capture(rz, e); });
    rz.addEventListener("pointermove", e => {
      if (!on) return;
      const h = clamp(h0 + (y0 - e.clientY), 150, innerHeight * 0.7);
      root.style.setProperty("--tlh", `${Math.round(h)}px`);
    });
    const end = () => { if (on) { on = false; store("rc_tl_h", Math.round($("tl").getBoundingClientRect().height)); } };
    rz.addEventListener("pointerup", end); rz.addEventListener("pointercancel", end);
  })();

  // ================= loading =================
  async function boot() {
    const j = await fetch(`/api/editor/${PID}`).then(r => r.json()).catch(() => null);
    if (!j) { setTimeout(boot, 3000); return; }
    P = j;
    if (j.state !== "ready") {
      $("cover").hidden = false;
      $("cover-box").innerHTML = j.state === "failed"
        ? `<b>Import failed</b><span>${esc(j.error || "Something went wrong.")}</span><a class="btn ghost sm" href="/editor">Back to projects</a>`
        : `<b><span class="spin"></span>${esc(j.stage || "Importing")}</b><span>${esc(j.name)}</span>
           <div class="bar"><div style="width:${j.progress != null ? j.progress * 100 : 8}%"></div></div>
           <span style="font-size:14px;color:var(--faint)">You can leave this page; the import keeps going.</span>`;
      if (j.state !== "failed") setTimeout(boot, 1500);
      return;
    }
    $("cover").hidden = true;
    meta = j.meta; doc = j.doc; expSize = doc.size || "1080";
    $("name").value = j.name; document.title = `${j.name} — Clipforge editor`;
    if (document.fonts) { try { await document.fonts.load(`40px "RecaplyFont"`); } catch (_) {} }
    for (const L of doc.layers) {
      L.kf = L.kf || {}; L.anim_in = L.anim_in || { type: "none", dur: 0.5 }; L.anim_out = L.anim_out || { type: "none", dur: 0.5 };
      if (typeof L.start !== "number") L.start = 0;
      if (L.end === undefined) L.end = null;
      if (typeof L.opacity !== "number") L.opacity = 1;
      L.hidden = !!L.hidden; L.locked = !!L.locked;
      if (L.type === "video") { if (typeof L.blur !== "number") L.blur = 0; if (typeof L.bright !== "number") L.bright = 1; }
      if (L.type === "text") layoutText(L);
      if (L.type === "image") loadImage(L.asset);
      if (L.type === "captions") relayoutCaptions(L);
    }
    vid.src = `/editor/${PID}/source.mp4`;
    hist = [JSON.stringify(doc)]; hi = 0; paintUndo();
    paintFormat(); paintPlay(); fit(); buildInspector(); renderTimeline(); paintHint();
    paintSaved("Saved", "ok");
    fetch(`/api/editor/${PID}/waveform`).then(r => r.json()).then(pk => { peaks = pk || []; drawWaves(); }).catch(() => {});
    if (["queued", "rendering"].includes((P.render || {}).state)) pollExport();
    if (!load("rc_ed_welcome", false)) { $("welcome").hidden = false; store("rc_ed_welcome", true); }
    if ((P.asr || {}).state === "running") pollAsr();
    else if ((P.asr || {}).state === "done" && !P.asr.applied)
      toast(`Your captions are ready (${P.asr.words} words).`, { label: "Add captions", fn: () => finishCaptions() });
    if (j.want_reaction) {
      vid.addEventListener("loadeddata", () => {
        if (j.detect && j.detect.cam) {
          applyReaction(reactOpts, j.detect); buildInspector(); changed(); commit();
          toast("Reaction layout applied: the streamer's cam on top, the gameplay below. Use Reaction to adjust.");
        } else openReaction(false);
      }, { once: true });
    }
  }
  // small hook for automated tests and the browser console
  window.RecaplyEditor = { doc: () => doc, project: () => P, select, changed, commit, draw, pose: (L, t) => pose(L, t),
                           captionState: (L, t) => captionState(L, t),
                           seek: t => new Promise(res => { pause(); T = t; updateTimeline(true); refreshInspector(); vid.addEventListener("seeked", () => res(), { once: true }); seekVideo(); setTimeout(res, 1500); }) };
  boot();
})();
