"""
editor.py — the video editor: project documents, rendering and the
reaction (watch party) layout detector.

A project is one source video plus a document describing the edit:

  format    output shape: 9:16, 16:9, 1:1 or 4:5 (canvas in "design" pixels,
            1080 on the short side; `size` 720 scales the result down)
  trim      [start, end] seconds of the source, `speed` 0.25-4
  color     brightness / contrast / saturation (CSS filter semantics)
  bg        what shows where no layer covers the canvas: a blurred copy of
            the source (blur, opacity over black) or a solid colour
  volume, fade_in, fade_out (video and audio)
  layers    bottom to top. Each has a box (x, y, w, h in canvas pixels),
            opacity, an optional time range (output seconds) and a type:
              video  a crop of the source (fractions of the frame), flippable
              text   wrapped text with outline and an optional background
              shape  rect / ellipse (fill + stroke), line / arrow (stroke)
              image  an uploaded picture (logo, sticker)
              captions  word-timed caption chunks (from speech to text) shown
                     a few words at a time, the spoken word highlighted
            Layers can be animated: keyframes (pos, sc = scale, rot, op =
            opacity; time in output seconds, with an easing per key) and
            in / out presets (fade, slide, pop, zoom). Text, shapes and
            images can be rotated.

The page draws the same document on a canvas for the live preview, with the
same geometry, the same font file and the same wrapping, so the export looks
like the preview. Video layers become ffmpeg crop/scale/overlay chains; text,
shapes and images are drawn with Pillow (2x supersampled) into transparent
PNGs overlaid for their time range. Animated layers and captions become image
tracks (ffmpeg's concat demuxer): a picture per distinct state, 30 per second
while something moves and one per word change for captions. Moving video
layers are placed by overlay x / y commands sent with sendcmd.
"""

import functools
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pipeline import ENCODER_ARGS, h264_encoder
import reels
from reels import font_file

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
SIZES = ("1080", "720")                 # short side of the output
MAX_LAYERS = 60
TEXT_MAX = 500
LINE_H = 1.2                            # text line height, x font size
BASELINE = 0.93                         # first baseline below the line top, x font size
TEXT_PAD = 0.25                         # text box padding, x font size
SS = 2                                  # supersampling for shapes and text
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LAYER_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
ASSET_NAME = re.compile(r"^[0-9a-f]{12}\.png$")
FONT_NAME = re.compile(r"^[0-9a-f]{12}\.(ttf|otf)$")    # a custom font in the project's assets
EMOJI_FONTS = ("C:/Windows/Fonts/seguiemj.ttf", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
               "/System/Library/Fonts/Apple Color Emoji.ttc")
SHAPES = ("rect", "ellipse", "line", "arrow")
ALIGNS = ("left", "center", "right")
LAYER_TYPES = ("video", "text", "shape", "image", "captions")
EASES = ("linear", "ease", "in", "out", "hold")
ANIMS = ("none", "fade", "slide_up", "slide_down", "slide_left", "slide_right", "pop", "zoom")
KF_PROPS = {"pos": 2, "sc": 1, "rot": 1, "op": 1}       # keyframed property -> number of values
KF_RANGE = {"sc": (0.01, 20.0), "rot": (-3600.0, 3600.0), "op": (0.0, 1.0)}
FACE_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "face_detection_yunet_2023mar.onnx")


# ---------- probing ----------

def probe(path):
    """{w, h, fps, duration, audio, video} of a video file (w/h after rotation)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate:stream_tags=rotate"
         ":stream_side_data=rotation:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True, timeout=60).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("width")), None)
    if v is None:
        raise ValueError("This file has no video.")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((v.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = 30.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, d = (v.get(key) or "0/0").split("/")
            if float(d) and 1 <= float(n) / float(d) <= 240:
                fps = float(n) / float(d)
                break
        except (ValueError, ZeroDivisionError):
            pass
    return {"w": w, "h": h, "fps": round(min(fps, 60.0), 3),
            "duration": round(float(info.get("format", {}).get("duration") or 0), 3),
            "audio": a.get("codec_name") if a else None, "video": v.get("codec_name")}


# ---------- documents ----------

def _num(raw, key, default, lo, hi):
    try:
        v = float(raw.get(key, default))
    except (TypeError, ValueError, AttributeError):
        v = default
    if v != v or v in (float("inf"), float("-inf")):
        v = default
    return round(min(hi, max(lo, v)), 4)


def _color(raw, key, default):
    v = raw.get(key) if isinstance(raw, dict) else None
    return v.lower() if isinstance(v, str) and HEX.match(v) else default


def canvas_size(fmt):
    return FORMATS.get(fmt, FORMATS["9:16"])


def fit_video(meta, W, H, mode="fit"):
    """A video layer showing the whole source: fit (all of it) or fill (cover the canvas)."""
    sw, sh = meta["w"], meta["h"]
    if mode == "fill":
        # crop the source to the canvas shape, centred
        if sw / sh > W / H:
            cw = (sh * W / H) / sw
            crop = [(1 - cw) / 2, 0, cw, 1]
        else:
            ch = (sw * H / W) / sh
            crop = [0, (1 - ch) / 2, 1, ch]
        return {"id": "video", "type": "video", "name": "Video", "crop": [round(c, 5) for c in crop],
                "flip": False, "opacity": 1, "x": 0, "y": 0, "w": W, "h": H}
    w, h = W, W * sh / sw
    if h > H:
        w, h = H * sw / sh, H
    return {"id": "video", "type": "video", "name": "Video", "crop": [0, 0, 1, 1], "flip": False, "opacity": 1,
            "x": round((W - w) / 2, 2), "y": round((H - h) / 2, 2), "w": round(w, 2), "h": round(h, 2)}


def backdrop_layer(meta, W, H, blur=50, bright=0.82):
    """The blurred copy of the video that fills the frame: a normal video layer (bottom of the stack)."""
    L = fit_video(meta, W, H, "fill")
    L.update(id="bg", name="Background", blur=blur, bright=bright, backdrop=True, hidden=False, locked=False,
             start=0, end=None, kf={}, anim_in={"type": "none", "dur": 0.5}, anim_out={"type": "none", "dur": 0.5})
    return L


def default_doc(meta, fmt="9:16"):
    W, H = canvas_size(fmt)
    return {
        "format": fmt, "size": "1080", "trim": [0, meta["duration"]], "speed": 1,
        "volume": 1, "fade_in": 0, "fade_out": 0,
        "color": {"brightness": 1, "contrast": 1, "saturation": 1},
        "bg": {"type": "color", "color": "#000000", "blur": 50, "opacity": 0.82},
        "layers": [backdrop_layer(meta, W, H), fit_video(meta, W, H)],
    }


def migrate_doc(doc, meta):
    """Older documents had the blurred background as a setting; it is now a layer."""
    bg = doc.get("bg") if isinstance(doc.get("bg"), dict) else {}
    if bg.get("type") != "blur":
        return doc
    W, H = canvas_size(doc.get("format"))
    try:
        blur, bright = float(bg.get("blur", 50)), float(bg.get("opacity", 0.82))
    except (TypeError, ValueError):
        blur, bright = 50.0, 0.82
    layers = doc.get("layers") if isinstance(doc.get("layers"), list) else []
    ids = {L.get("id") for L in layers if isinstance(L, dict)}
    B = backdrop_layer(meta, W, H, blur, bright)
    while B["id"] in ids:
        B["id"] += "x"
    return dict(doc, layers=[B, *layers], bg={"type": "color", "color": "#000000", "blur": blur, "opacity": bright})


def _clean_kf(raw, t, big):
    """Keyframes {prop: [[time, value(s)..., ease]]}, sorted, one per time."""
    out = {}
    if not isinstance(raw, dict):
        return out
    allowed = () if t == "captions" else ("pos", "op") if t == "video" else tuple(KF_PROPS)
    for prop in allowed:
        keys = raw.get(prop)
        if not isinstance(keys, list):
            continue
        n = KF_PROPS[prop]
        clean = []
        for k in keys[:300]:
            if not isinstance(k, list) or len(k) < 1 + n:
                continue
            try:
                tt = min(36000.0, max(0.0, float(k[0])))
                vals = [float(v) for v in k[1:1 + n]]
            except (TypeError, ValueError):
                continue
            if any(v != v or abs(v) == float("inf") for v in vals):
                continue
            if prop == "pos":
                vals = [min(big, max(-big, v)) for v in vals]
            else:
                lo, hi = KF_RANGE[prop]
                vals = [min(hi, max(lo, vals[0]))]
            ease = k[1 + n] if len(k) > 1 + n and k[1 + n] in EASES else "ease"
            clean.append([round(tt, 3), *[round(v, 4) for v in vals], ease])
        clean.sort(key=lambda k: k[0])
        dedup = []
        for k in clean:
            if dedup and abs(dedup[-1][0] - k[0]) < 1e-3:
                dedup[-1] = k
            else:
                dedup.append(k)
        if dedup:
            out[prop] = dedup
    return out


def _clean_chunks(raw):
    chunks, total = [], 0
    for c in (raw if isinstance(raw, list) else [])[:4000]:
        if not isinstance(c, dict):
            continue
        words = []
        for w in (c.get("words") if isinstance(c.get("words"), list) else [])[:40]:
            if not isinstance(w, dict):
                continue
            text = " ".join(str(w.get("w") or "").split())[:40]
            try:
                s, e = float(w.get("s")), float(w.get("e"))
            except (TypeError, ValueError):
                continue
            if not text or s != s or e != e:
                continue
            s = max(0.0, min(36000.0, s))
            word = {"w": text, "s": round(s, 3), "e": round(min(36000.0, max(e, s + 0.02)), 3)}
            if w.get("k"):
                word["k"] = 1                   # keyword: drawn in the layer's keyword colour
            try:
                word["p"] = round(min(1.0, max(0.0, float(w.get("p", 1)))), 3)
            except (TypeError, ValueError):
                pass
            words.append(word)
        if not words:
            continue
        total += len(words)
        if total > 10000:
            break
        ch = {"words": words}
        emo = c.get("emoji")
        if isinstance(emo, str) and 0 < len(emo) <= 8 and not any(x.isalnum() or x in "<>&\"'" for x in emo):
            ch["emoji"] = emo
        lines = c.get("lines")
        if (isinstance(lines, list) and 0 < len(lines) <= 10 and all(isinstance(n, int) and n > 0 for n in lines)
                and sum(lines) == len(words)):
            ch["lines"] = lines
        chunks.append(ch)
    chunks.sort(key=lambda c: c["words"][0]["s"])
    return chunks


def clean_layer(raw, W, H, assets_dir, used_ids):
    if not isinstance(raw, dict) or raw.get("type") not in LAYER_TYPES:
        return None
    t = raw["type"]
    lid = raw.get("id") if isinstance(raw.get("id"), str) and LAYER_ID.match(raw["id"]) else None
    if not lid or lid in used_ids:
        lid = f"l{len(used_ids) + 1}"
        while lid in used_ids:
            lid += "x"
    used_ids.add(lid)
    big = 8 * max(W, H)
    L = {"id": lid, "type": t, "name": str(raw.get("name") or "")[:40],
         "hidden": bool(raw.get("hidden")), "locked": bool(raw.get("locked")),
         "opacity": _num(raw, "opacity", 1, 0, 1),
         "x": _num(raw, "x", 0, -big, big), "y": _num(raw, "y", 0, -big, big)}
    lo = -big if t == "shape" and raw.get("kind") in ("line", "arrow") else 1
    L["w"] = _num(raw, "w", 200, lo, big)
    L["h"] = _num(raw, "h", 200, lo, big)
    L["start"] = _num(raw, "start", 0, 0, 36000)
    end = raw.get("end")
    L["end"] = None if end in (None, "") else _num(raw, "end", 0, L["start"] + 0.05, 36000)
    if t in ("text", "shape", "image"):
        L["rot"] = _num(raw, "rot", 0, -3600, 3600)
    L["kf"] = _clean_kf(raw.get("kf"), t, big)
    for key in ("anim_in", "anim_out"):
        a = raw.get(key) if isinstance(raw.get(key), dict) else {}
        typ = a.get("type") if a.get("type") in ANIMS else "none"
        if t in ("video", "captions") and typ in ("pop", "zoom"):
            typ = "fade"
        L[key] = {"type": typ, "dur": _num(a, "dur", 0.5, 0.05, 5)}
    if t == "video":
        c = raw.get("crop") if isinstance(raw.get("crop"), list) and len(raw["crop"]) == 4 else [0, 0, 1, 1]
        try:
            cx, cy, cw, ch = (min(1.0, max(0.0, float(v))) for v in c)
        except (TypeError, ValueError):
            cx, cy, cw, ch = 0, 0, 1, 1
        cw, ch = max(0.01, min(cw, 1 - cx)), max(0.01, min(ch, 1 - cy))
        cx, cy = min(cx, 1 - cw), min(cy, 1 - ch)
        L["crop"] = [round(v, 5) for v in (cx, cy, cw, ch)]
        L["flip"] = bool(raw.get("flip"))
        L["blur"] = _num(raw, "blur", 0, 0, 100)
        L["bright"] = _num(raw, "bright", 1, 0, 2)
        L["backdrop"] = bool(raw.get("backdrop"))
    elif t == "text":
        L["text"] = str(raw.get("text") or "").replace("\r", "")[:TEXT_MAX]
        L["size"] = int(round(_num(raw, "size", 72, 8, 400)))
        L["color"] = _color(raw, "color", "#ffffff")
        L["stroke"] = _color(raw, "stroke", "#000000")
        L["stroke_w"] = _num(raw, "stroke_w", 0, 0, 40)
        L["bg"] = _color(raw, "bg", "#000000")
        L["bg_op"] = _num(raw, "bg_op", 0, 0, 1)
        L["align"] = raw.get("align") if raw.get("align") in ALIGNS else "center"
        lines = raw.get("lines")
        if isinstance(lines, list) and len(lines) <= 60 and all(isinstance(s, str) for s in lines):
            L["lines"] = [s[:TEXT_MAX] for s in lines]
    elif t == "shape":
        L["kind"] = raw.get("kind") if raw.get("kind") in SHAPES else "rect"
        L["fill"] = _color(raw, "fill", "#f66f14")
        L["fill_op"] = _num(raw, "fill_op", 1, 0, 1)
        L["stroke"] = _color(raw, "stroke", "#ffffff")
        L["stroke_op"] = _num(raw, "stroke_op", 1, 0, 1)
        L["stroke_w"] = _num(raw, "stroke_w", 0, 0, 200)
        L["radius"] = _num(raw, "radius", 0, 0, 4000)
    elif t == "captions":
        L["size"] = int(round(_num(raw, "size", 76, 12, 300)))
        L["color"] = _color(raw, "color", "#ffffff")
        L["hi"] = _color(raw, "hi", "#ffd21f")
        L["hi_bg"] = _color(raw, "hi_bg", "#f66f14")
        L["hi_mode"] = raw.get("hi_mode") if raw.get("hi_mode") in ("color", "box", "none") else "color"
        L["kw"] = _color(raw, "kw", L["hi"])
        L["stroke"] = _color(raw, "stroke", "#000000")
        L["stroke_w"] = _num(raw, "stroke_w", 6, 0, 40)
        L["bg"] = _color(raw, "bg", "#000000")
        L["bg_op"] = _num(raw, "bg_op", 0, 0, 1)
        L["upper"] = bool(raw.get("upper"))
        L["pop"] = bool(raw.get("pop", True))
        L["per"] = int(_num(raw, "per", 3, 1, 8))
        if isinstance(raw.get("preset"), str) and re.match(r"^[a-z]{1,12}$", raw["preset"]):
            L["preset"] = raw["preset"]
        L["chunks"] = _clean_chunks(raw.get("chunks"))
    if t in ("text", "captions"):
        f = raw.get("font")
        if isinstance(f, str) and FONT_NAME.match(f) and os.path.exists(os.path.join(assets_dir, f)):
            L["font"] = f
    if t in ("image",):
        name = raw.get("asset")
        if not (isinstance(name, str) and ASSET_NAME.match(name)
                and os.path.exists(os.path.join(assets_dir, name))):
            return None
        L["asset"] = name
    return L


def clean_doc(raw, meta, assets_dir):
    """A document from untrusted input: known keys only, numbers clamped."""
    raw = migrate_doc(raw if isinstance(raw, dict) else {}, meta)
    base = default_doc(meta)
    fmt = raw.get("format") if raw.get("format") in FORMATS else base["format"]
    W, H = canvas_size(fmt)
    dur = meta["duration"]
    trim = raw.get("trim") if isinstance(raw.get("trim"), list) and len(raw["trim"]) == 2 else [0, dur]
    try:
        t0 = min(max(0.0, float(trim[0])), max(0.0, dur - 0.2))
        t1 = min(dur, max(t0 + 0.2, float(trim[1])))
    except (TypeError, ValueError):
        t0, t1 = 0.0, dur
    color = raw.get("color") if isinstance(raw.get("color"), dict) else {}
    bg = raw.get("bg") if isinstance(raw.get("bg"), dict) else {}
    used = set()
    layers = [L for L in (clean_layer(x, W, H, assets_dir, used)
                          for x in (raw.get("layers") if isinstance(raw.get("layers"), list) else [])[:MAX_LAYERS])
              if L]
    return {
        "format": fmt, "size": raw.get("size") if raw.get("size") in SIZES else "1080",
        "trim": [round(t0, 3), round(t1, 3)], "speed": _num(raw, "speed", 1, 0.25, 4),
        "volume": _num(raw, "volume", 1, 0, 2),
        "fade_in": _num(raw, "fade_in", 0, 0, 5), "fade_out": _num(raw, "fade_out", 0, 0, 5),
        "color": {k: _num(color, k, 1, 0, 2) for k in ("brightness", "contrast", "saturation")},
        "bg": {"type": bg.get("type") if bg.get("type") in ("blur", "color") else "blur",
               "color": _color(bg, "color", "#000000"), "blur": _num(bg, "blur", 50, 0, 100),
               "opacity": _num(bg, "opacity", 0.82, 0, 1)},
        "layers": layers,
    }


def duration_of(doc):
    return max(0.1, (doc["trim"][1] - doc["trim"][0]) / doc["speed"])


def output_size(doc):
    W, H = canvas_size(doc["format"])
    k = 1.0 if doc["size"] == "1080" else 720 / 1080
    return _even(W * k), _even(H * k)


# ---------- animation (the page mirrors this exactly) ----------

def _ease(kind, u):
    if kind == "linear":
        return u
    if kind == "in":
        return u ** 3
    if kind == "out":
        return 1 - (1 - u) ** 3
    if kind == "hold":
        return 0.0
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2      # ease in-out


def _back_out(u):
    c1 = 1.70158
    return 1 + (c1 + 1) * (u - 1) ** 3 + c1 * (u - 1) ** 2


def kf_value(keys, t):
    """Value(s) of a keyframed property at time t (held before the first / after the last key)."""
    n = len(keys[0]) - 2
    if t <= keys[0][0]:
        return keys[0][1:1 + n]
    if t >= keys[-1][0]:
        return keys[-1][1:1 + n]
    for a, b in zip(keys, keys[1:]):
        if a[0] <= t < b[0]:
            e = _ease(a[-1], (t - a[0]) / (b[0] - a[0]))
            return [va + (vb - va) * e for va, vb in zip(a[1:1 + n], b[1:1 + n])]
    return keys[-1][1:1 + n]


def layer_end(L, D):
    return D if L["end"] is None else min(D, L["end"])


def animated(L):
    return bool(L.get("kf")) or any((L.get(k) or {}).get("type", "none") != "none" for k in ("anim_in", "anim_out"))


def pose(L, t, W, H, D):
    """A copy of the layer as it is at time t: keyframes and in/out animations applied."""
    kf = L.get("kf") or {}
    x, y = kf_value(kf["pos"], t) if "pos" in kf else (L["x"], L["y"])
    sc = kf_value(kf["sc"], t)[0] if "sc" in kf else 1.0
    rot = kf_value(kf["rot"], t)[0] if "rot" in kf else L.get("rot", 0)
    op = kf_value(kf["op"], t)[0] if "op" in kf else 1.0
    slide = 0.1 * min(W, H)
    for key, left in (("anim_in", t - L["start"]), ("anim_out", layer_end(L, D) - t)):
        a = L.get(key) or {}
        typ = a.get("type", "none")
        if typ == "none":
            continue
        u = left / a["dur"]
        if u < 0 or u >= 1:
            continue
        p = 1 - (1 - u) ** 3
        if typ == "fade":
            op *= p
        elif typ.startswith("slide"):
            op *= p
            dx, dy = {"slide_up": (0, 1), "slide_down": (0, -1), "slide_left": (1, 0), "slide_right": (-1, 0)}[typ]
            if key == "anim_out":
                dx, dy = -dx, -dy
            x += dx * (1 - p) * slide
            y += dy * (1 - p) * slide
        elif typ == "pop":
            sc *= 0.5 + 0.5 * _back_out(u)
            op *= min(1.0, u * 3)
        elif typ == "zoom":
            sc *= 1.3 - 0.3 * p
            op *= p
    P = dict(L)
    P.update(x=x, y=y, rot=rot, opacity=L["opacity"] * max(0.0, min(1.0, op)))
    if abs(sc - 1) > 1e-4 and L["type"] != "video":
        cx, cy = x + L["w"] / 2, y + L["h"] / 2
        P["w"], P["h"] = L["w"] * sc, L["h"] * sc
        P["x"], P["y"] = cx - P["w"] / 2, cy - P["h"] / 2
        for key in ("size", "stroke_w", "radius"):
            if key in P:
                P[key] = P[key] * sc
    return P


# ---------- text ----------

def _font(size):
    path = font_file()
    if not path:
        raise RuntimeError("No bold font found for text layers. Set RECAPLY_FONT to a .ttf file.")
    return _font_at(path, max(1, int(round(size))))


def _lfont(L, size):
    """The layer's own font (a custom font in the project's assets) or the default bold font."""
    path = L.get("_font_path")
    if path and os.path.exists(path):
        return _font_at(path, max(1, int(round(size))))
    return _font(size)


def _emoji_font(size):
    path = next((f for f in EMOJI_FONTS if os.path.exists(f)), None)
    return _font_at(path, max(1, int(round(size)))) if path else None


def bind_fonts(doc, assets_dir):
    """A copy of the document whose layers know their custom font files (for drawing only)."""
    doc = json.loads(json.dumps(doc))
    for L in doc.get("layers", []):
        if L.get("font"):
            L["_font_path"] = os.path.join(assets_dir, L["font"])
    return doc


@functools.lru_cache(maxsize=256)
def _font_at(path, size):
    # Loading a .ttf parses the whole file: do it once per size, not once per frame.
    return ImageFont.truetype(path, size)


def wrap_text(text, width, measure):
    """Greedy word wrap into `width` (the page's wrap is the same algorithm)."""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            if word == "":
                continue
            while measure(word) > width and len(word) > 1:      # a very long word
                if cur:
                    lines.append(cur)
                    cur = ""
                n = len(word) - 1
                while n > 1 and measure(word[:n]) > width:
                    n -= 1
                lines.append(word[:n])
                word = word[n:]
            if not cur:
                cur = word
            elif measure(cur + " " + word) <= width:
                cur += " " + word
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def text_lines(L):
    """Lines of a text layer: the page's wrapping when it matches the text, else Pillow's."""
    squash = lambda s: "".join(s.split())                       # noqa: E731
    given = L.get("lines")
    if given is not None and squash("".join(given)) == squash(L["text"]):
        return given
    font = _lfont(L, L["size"])
    return wrap_text(L["text"], L["w"] - 2 * L["size"] * TEXT_PAD, font.getlength)


# ---------- captions ----------

CAP_POP = 0.2                           # seconds a chunk takes to pop in
CAP_POP_FRAMES = 5                      # pictures drawn for the pop (the page animates it smoothly too)
CAP_GAP = 0.6                           # a chunk stays up across pauses shorter than this
CAP_TAIL = 0.3                          # ...and this long after its last word otherwise


def caption_state(L, t, trim0, speed):
    """(chunk index, active word index, scale, opacity) at output time t, or None."""
    ch = L["chunks"]
    out = lambda s: (s - trim0) / speed                         # noqa: E731
    i = None
    for k, c in enumerate(ch):
        if out(c["words"][0]["s"]) <= t:
            i = k
        else:
            break
    if i is None:
        return None
    c = ch[i]
    cs = out(c["words"][0]["s"])
    last = out(c["words"][-1]["e"])
    nxt = out(ch[i + 1]["words"][0]["s"]) if i + 1 < len(ch) else float("inf")
    ce = nxt if nxt - last < CAP_GAP else min(nxt, last + CAP_TAIL)
    if t >= ce:
        return None
    j = 0
    for k, w in enumerate(c["words"]):
        if out(w["s"]) <= t:
            j = k
    sc, op = 1.0, 1.0
    if L["pop"]:
        u = (t - cs) / CAP_POP
        if u < 1:
            sc, op = 0.7 + 0.3 * _back_out(u), min(1.0, u * 2.5)
    return i, j, round(sc, 4), round(op, 4)


def caption_layout(L, chunk, measure):
    """[(word, x, baseline, width, line_top)], block top, block height (unscaled)."""
    size = L["size"]
    words = [w["w"].upper() if L["upper"] else w["w"] for w in chunk["words"]]
    widths = [measure(w) for w in words]
    space = measure(" ")
    lines = chunk.get("lines")
    if not lines:
        maxw, lines, cur, cw = L["w"] - 2 * size * 0.3, [], 0, 0.0
        for wd in widths:
            add = wd if cur == 0 else cw + space + wd
            if cur and add > maxw:
                lines.append(cur)
                cur, cw = 1, wd
            else:
                cur, cw = cur + 1, add
        if cur:
            lines.append(cur)
    lh = size * LINE_H
    block = len(lines) * lh
    top = L["y"] + (L["h"] - block) / 2
    out, i = [], 0
    for li, n in enumerate(lines):
        lw = sum(widths[i:i + n]) + space * (n - 1)
        x = L["x"] + (L["w"] - lw) / 2
        for k in range(n):
            out.append((words[i + k], x, top + li * lh + size * 0.97, widths[i + k], top + li * lh))
            x += widths[i + k] + space
        i += n
    return out, top, block


def _draw_caption(img, L, state, P, ss=SS):
    """One caption state (chunk i, word j, scale, opacity) into `img` via canvas->image mapping P."""
    i, j, sc, op = state
    size = L["size"]
    font0 = _lfont(L, size)
    items, top, block = caption_layout(L, L["chunks"][i], font0.getlength)
    cx, cy = L["x"] + L["w"] / 2, top + block / 2
    S = lambda x, y: P(cx + (x - cx) * sc, cy + (y - cy) * sc)  # noqa: E731
    d = ImageDraw.Draw(img)
    if L["bg_op"] > 0:
        pad = size * 0.3
        x0 = min(it[1] for it in items) - pad
        x1 = max(it[1] + it[3] for it in items) + pad
        a, b = S(x0, top - pad * 0.5), S(x1, top + block + pad * 0.5)
        d.rounded_rectangle([a[0], a[1], b[0] - 1, b[1] - 1], radius=int(round(size * 0.25 * sc * ss)),
                            fill=_rgba(L["bg"], L["bg_op"]))
    if L["hi_mode"] == "box":
        w, x, _, ww, lt = items[j][0], items[j][1], items[j][2], items[j][3], items[j][4]
        a, b = S(x - size * 0.12, lt + size * 0.1), S(x + ww + size * 0.12, lt + size * 1.16)
        d.rounded_rectangle([a[0], a[1], b[0] - 1, b[1] - 1], radius=int(round(size * 0.18 * sc * ss)),
                            fill=_rgba(L["hi_bg"], 1))
    font = _lfont(L, size * sc * ss)
    sw = int(round(L["stroke_w"] * sc * ss))
    stroke_mask = Image.new("L", img.size, 0) if sw > 0 else None
    masks = {}
    for k, (w, x, base, _, _) in enumerate(items):
        xy = S(x, base)
        if stroke_mask is not None:
            ImageDraw.Draw(stroke_mask).text(xy, w, font=font, fill=255, anchor="ls", stroke_width=sw, stroke_fill=255)
        kw = L["chunks"][i]["words"][k].get("k")
        col = L["hi"] if (k == j and L["hi_mode"] == "color") else (L.get("kw", L["hi"]) if kw else L["color"])
        m = masks.setdefault(col, Image.new("L", img.size, 0))
        ImageDraw.Draw(m).text(xy, w, font=font, fill=255, anchor="ls")
    if stroke_mask is not None:
        _paint(img, stroke_mask, _rgba(L["stroke"], 1))
    for col, m in masks.items():
        _paint(img, m, _rgba(col, 1))
    emo = L["chunks"][i].get("emoji")
    ef = _emoji_font(109 * ss * sc) if emo else None    # colour emoji fonts are drawn at fixed sizes
    if ef is not None:
        tile = Image.new("RGBA", (int(140 * ss * sc) * max(1, len(emo)), int(140 * ss * sc)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((0, 0), emo, font=ef, embedded_color=True)
        bb = tile.getbbox()
        if bb:
            tile = tile.crop(bb)
            target = size * 1.1 * sc * ss
            k = target / max(1, tile.height)
            tile = tile.resize((max(1, int(tile.width * k)), max(1, int(tile.height * k))), Image.LANCZOS)
            ex, ey = S(cx, top - size * 0.25)
            img.alpha_composite(tile, (int(ex - tile.width / 2), max(0, int(ey - tile.height))))
    o = L["opacity"] * op
    if o < 0.999:
        img.putalpha(img.getchannel("A").point(lambda v: int(round(v * o))))


# ---------- overlays (text, shapes, images) ----------

def _rgba(hexcol, op):
    return (int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16), int(round(255 * op)))


def _bbox(L):
    """Canvas-space bounding box of a layer's drawing, generously including strokes."""
    if L["type"] == "shape" and L["kind"] in ("line", "arrow"):
        x0, x1 = sorted((L["x"], L["x"] + L["w"]))
        y0, y1 = sorted((L["y"], L["y"] + L["h"]))
        m = L["stroke_w"] * 4 + 20
    else:
        x0, y0, x1, y1 = L["x"], L["y"], L["x"] + L["w"], L["y"] + L["h"]
        m = (L.get("stroke_w") or 0) + 4
    return x0 - m, y0 - m, x1 + m, y1 + m


def _arrow_head(x1, y1, x2, y2, sw):
    """(base point, triangle points) of an arrow's head at (x2, y2)."""
    dx, dy = x2 - x1, y2 - y1
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    head = min(max(sw * 3.5, 18.0), ln)
    half = head * 0.6
    bx, by = x2 - ux * head, y2 - uy * head
    return (bx, by), [(x2, y2), (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half)]


def _render_box(L, x0, y0, bw, bh, assets_dir):
    """The layer (unrotated) drawn into a bw x bh image whose top-left is canvas (x0, y0)."""
    if L["type"] == "image":
        src = Image.open(os.path.join(assets_dir, L["asset"])).convert("RGBA")
        w, h = max(1, int(round(L["w"]))), max(1, int(round(L["h"])))
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        img.paste(src.resize((w, h), Image.LANCZOS), (int(round(L["x"])) - x0, int(round(L["y"])) - y0))
    else:
        big = Image.new("RGBA", (bw * SS, bh * SS), (0, 0, 0, 0))
        P = lambda x, y: ((x - x0) * SS, (y - y0) * SS)          # noqa: E731
        if L["type"] == "shape":
            _draw_shape(big, L, P)
        else:
            _draw_text(big, L, P)
        img = big.resize((bw, bh), Image.LANCZOS)
    if L["opacity"] < 0.999:
        img.putalpha(img.getchannel("A").point(lambda v: int(round(v * L["opacity"]))))
    return img


def draw_layer(L, W, H, assets_dir):
    """(RGBA image, (x, y)) of one text / shape / image layer (posed), or None if it's off the canvas."""
    bx0, by0, bx1, by1 = _bbox(L)
    rot = L.get("rot", 0) or 0
    if abs(rot) % 360 < 0.01:
        x0, y0 = max(0, int(math.floor(bx0))), max(0, int(math.floor(by0)))
        x1, y1 = min(W, int(math.ceil(bx1))), min(H, int(math.ceil(by1)))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        return _render_box(L, x0, y0, x1 - x0, y1 - y0, assets_dir), (x0, y0)
    # rotated: draw the whole layer, turn it around its centre, then clip to the canvas
    x0, y0 = int(math.floor(bx0)), int(math.floor(by0))
    bw, bh = int(math.ceil(bx1)) - x0, int(math.ceil(by1)) - y0
    if bw < 1 or bh < 1 or bw * bh > 40_000_000:
        return None
    img = _render_box(L, x0, y0, bw, bh, assets_dir)
    cx, cy = x0 + bw / 2, y0 + bh / 2
    img = img.rotate(-rot, resample=Image.BICUBIC, expand=True)
    ox, oy = int(round(cx - img.width / 2)), int(round(cy - img.height / 2))
    cl, ct = max(0, -ox), max(0, -oy)
    cr, cb = min(img.width, W - ox), min(img.height, H - oy)
    if cr - cl < 1 or cb - ct < 1:
        return None
    return img.crop((cl, ct, cr, cb)), (ox + cl, oy + ct)


def _draw_shape(img, L, P):
    d = ImageDraw.Draw(img)
    sw = L["stroke_w"] * SS
    stroke = _rgba(L["stroke"], L["stroke_op"]) if L["stroke_w"] > 0 and L["stroke_op"] > 0 else None
    if L["kind"] in ("line", "arrow"):
        if not stroke:
            return
        x1, y1, x2, y2 = L["x"], L["y"], L["x"] + L["w"], L["y"] + L["h"]
        if L["kind"] == "arrow":
            (bx, by), tri = _arrow_head(x1, y1, x2, y2, L["stroke_w"])
            if math.hypot(bx - x1, by - y1) > 0.5:
                d.line([P(x1, y1), P(bx, by)], fill=stroke, width=max(1, int(round(sw))))
            d.polygon([P(*p) for p in tri], fill=stroke)
        else:
            d.line([P(x1, y1), P(x2, y2)], fill=stroke, width=max(1, int(round(sw))))
        return
    fill = _rgba(L["fill"], L["fill_op"]) if L["fill_op"] > 0 else None
    box = [*P(L["x"], L["y"]), *P(L["x"] + L["w"], L["y"] + L["h"])]
    box[2] -= 1
    box[3] -= 1
    width = max(1, int(round(sw))) if stroke else 0
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    if L["kind"] == "ellipse":
        d.ellipse(box, fill=fill, outline=stroke, width=width)
    else:
        r = min(L["radius"], L["w"] / 2, L["h"] / 2) * SS
        d.rounded_rectangle(box, radius=int(round(r)), fill=fill, outline=stroke, width=width)


def _paint(img, mask, rgba):
    solid = Image.new("RGBA", img.size, rgba[:3] + (0,))
    if rgba[3] < 255:
        mask = mask.point(lambda v: v * rgba[3] // 255)
    solid.putalpha(mask)
    img.alpha_composite(solid)


def _draw_text(img, L, P):
    size, pad, lh = L["size"], L["size"] * TEXT_PAD, L["size"] * LINE_H
    lines = text_lines(L)
    if L["bg_op"] > 0:
        d = ImageDraw.Draw(img)
        box = [*P(L["x"], L["y"]), *P(L["x"] + L["w"], L["y"] + L["h"])]
        d.rounded_rectangle([box[0], box[1], box[2] - 1, box[3] - 1], radius=int(round(size * 0.2 * SS)),
                            fill=_rgba(L["bg"], L["bg_op"]))
    font = _lfont(L, size * SS)
    anchor = {"left": "ls", "center": "ms", "right": "rs"}[L["align"]]
    ax = {"left": L["x"] + pad, "center": L["x"] + L["w"] / 2, "right": L["x"] + L["w"] - pad}[L["align"]]
    sw = int(round(L["stroke_w"] * SS))
    stroke_mask = Image.new("L", img.size, 0) if sw > 0 else None
    fill_mask = Image.new("L", img.size, 0)
    for i, line in enumerate(lines):
        if not line:
            continue
        xy = P(ax, L["y"] + pad + i * lh + size * BASELINE)
        if stroke_mask is not None:
            ImageDraw.Draw(stroke_mask).text(xy, line, font=font, fill=255, anchor=anchor,
                                             stroke_width=sw, stroke_fill=255)
        ImageDraw.Draw(fill_mask).text(xy, line, font=font, fill=255, anchor=anchor)
    if stroke_mask is not None:
        _paint(img, stroke_mask, _rgba(L["stroke"], 1))
    _paint(img, fill_mask, _rgba(L["color"], 1))


def overlay_groups(doc, assets_dir, work):
    """
    Still overlay layers: consecutive ones with the same time range drawn into
    one PNG: [(z, "png", png_name, x, y, start, end)]. Animated layers and
    captions get their own image track (see `_tracks`).
    """
    W, H = canvas_size(doc["format"])
    D = duration_of(doc)
    groups, cur, cur_key = [], None, None
    for z, L in enumerate(doc["layers"]):
        if L["hidden"] or L["type"] in ("video", "captions") or animated(L):
            cur, cur_key = None, None
            continue
        start, end = L["start"], layer_end(L, D)
        if start >= D or end - start < 0.02:
            continue
        drawn = draw_layer(L, W, H, assets_dir)
        if drawn is None:
            continue
        key = (round(start, 3), round(end, 3))
        if cur is None or key != cur_key:
            cur = {"z": z, "img": Image.new("RGBA", (W, H), (0, 0, 0, 0)), "start": start, "end": end}
            cur_key = key
            groups.append(cur)
        cur["img"].alpha_composite(drawn[0], drawn[1])
    out = []
    for i, g in enumerate(groups):
        bb = g["img"].getchannel("A").getbbox()
        if not bb:
            continue
        name = f"o{i}.png"
        g["img"].crop(bb).save(os.path.join(work, name))
        out.append((g["z"], "png", name, bb[0], bb[1], g["start"], g["end"]))
    return out


TRACK_FPS = 30                          # animation frames per second in image tracks
_POOL = None
_POOL_LOCK = threading.Lock()


def _pool():
    """A process pool for drawing track frames (Pillow text drawing holds the GIL, so threads don't help)."""
    global _POOL
    if os.environ.get("RECAPLY_FRAME_PROCS", "1") != "1":
        return None
    with _POOL_LOCK:
        if _POOL is None:
            import sys
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            main = sys.modules.get("__main__")
            old = getattr(main, "__file__", None)
            n = max(2, min(4, (os.cpu_count() or 4) - 1))
            try:
                # Workers start from a stub main script instead of re-running the app's.
                if main is not None:
                    main.__file__ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frameworker.py")
                _POOL = ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"))
                for f in [_POOL.submit(os.getpid) for _ in range(n * 2)]:      # start them all now
                    f.result(timeout=60)
            except Exception:  # noqa: BLE001 — fall back to threads
                _POOL = False
            finally:
                if main is not None and old is not None:
                    main.__file__ = old
        return _POOL or None


def _cap_render(ctx, st):
    """One caption frame (chunk, word, scale, opacity) as an RGBA image of the track region."""
    ss, rw, rh, rx0, ry0 = ctx["ss"], ctx["rw"], ctx["rh"], ctx["rx0"], ctx["ry0"]
    big = Image.new("RGBA", (rw * ss, rh * ss), (0, 0, 0, 0))
    _draw_caption(big, ctx["L"], st, lambda x, y: ((x - rx0) * ss, (y - ry0) * ss), ss)
    return big.resize((rw, rh), Image.LANCZOS) if ss > 1 else big


def _anim_render(ctx, key):
    """One frame of an animated layer: its pre-drawn picture scaled, turned and faded into place."""
    P, rw, rh, rx0, ry0 = ctx["posed"][key], ctx["rw"], ctx["rh"], ctx["rx0"], ctx["ry0"]
    img = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    s = ctx["scale_of"][key] / ctx["top"]
    im = ctx["base"]
    if abs(s - 1) > 1e-3:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.BICUBIC)
    rot = P.get("rot", 0) or 0
    if abs(rot) % 360 >= 0.01:
        im = im.rotate(-rot, resample=Image.BICUBIC, expand=True)
    if P["opacity"] < 0.999:
        op = P["opacity"]
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(round(v * op))))
    cx, cy = P["x"] + P["w"] / 2, P["y"] + P["h"] / 2
    x, y = int(round(cx - im.width / 2)) - rx0, int(round(cy - im.height / 2)) - ry0
    cl, ct = max(0, -x), max(0, -y)
    im = im.crop((cl, ct, min(im.width, rw - x), min(im.height, rh - y)))
    if im.width > 0 and im.height > 0:
        img.alpha_composite(im, (x + cl, y + ct))
    return img


_RENDERERS = {"caption": _cap_render, "anim": _anim_render}


def _render_batch(kind, ctx, items, work):
    """Draw and save a batch of track frames: [(key, file name)] (runs in a worker process)."""
    fn = _RENDERERS[kind]
    for key, fname in items:
        fn(ctx, key).save(os.path.join(work, fname), compress_level=1)
    return items


def _write_track(work, name, segments, kind, ctx, region, D):
    """
    An image track for ffmpeg's concat demuxer: `segments` [(t, key)] sorted by
    time (key None = nothing shown). Each distinct key is drawn once by the
    `kind` renderer (in worker processes when there are many).
    """
    rw, rh = region[2], region[3]
    blank = f"{name}_blank.png"
    Image.new("RGBA", (rw, rh), (0, 0, 0, 0)).save(os.path.join(work, blank))
    merged = []
    for t, key in segments:
        t = max(0.0, min(D, t))
        if merged and merged[-1][1] == key:
            continue
        if merged and t - merged[-1][0] < 1e-4:
            merged[-1] = (merged[-1][0], key)
            continue
        merged.append((t, key))
    if not merged or merged[0][0] > 0:
        merged.insert(0, (0.0, None))
    distinct = list(dict.fromkeys(k for _, k in merged if k is not None))

    items = [(key, f"{name}_{i}.png") for i, key in enumerate(distinct)]
    files = None
    pool = _pool() if len(items) >= 24 else None
    if pool is not None:
        n = max(1, min(len(items) // 8, (os.cpu_count() or 4) * 2))
        try:
            futs = [pool.submit(_render_batch, kind, ctx, items[i::n], work) for i in range(n)]
            files = dict(pair for f in futs for pair in f.result())
        except Exception:  # noqa: BLE001 — a broken pool: draw here instead
            global _POOL
            _POOL = False
            files = None
    if files is None:
        with ThreadPoolExecutor(min(8, os.cpu_count() or 4)) as ex:
            files = dict(pair for batch in ex.map(lambda it: _render_batch(kind, ctx, [it], work), items) for pair in batch)
    lines = ["ffconcat version 1.0"]
    for n, (t, key) in enumerate(merged):
        nxt = merged[n + 1][0] if n + 1 < len(merged) else D + 1.0
        lines.append(f"file '{files[key] if key is not None else blank}'")
        lines.append(f"duration {max(0.001, nxt - t):.4f}")
    lines.append(f"file '{files[merged[-1][1]] if merged[-1][1] is not None else blank}'")
    with open(os.path.join(work, f"{name}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return f"{name}.txt"


def _tracks(doc, assets_dir, work):
    """Image tracks for animated overlay layers and captions: [(z, "track", list_file, x, y, 0, D)]."""
    W, H = canvas_size(doc["format"])
    D = duration_of(doc)
    t0, sp = doc["trim"][0], doc["speed"]
    out = []
    for z, L in enumerate(doc["layers"]):
        if L["hidden"] or L["type"] == "video":
            continue
        start, end = L["start"], layer_end(L, D)
        if start >= D or end - start < 0.02:
            continue
        name = f"t{z}"
        if L["type"] == "captions":
            if not L["chunks"]:
                continue
            m = (L["w"] * 0.4, L["h"] * 0.6)
            rx0, ry0 = max(0, int(L["x"] - m[0])), max(0, int(L["y"] - m[1]))
            rx1, ry1 = min(W, int(math.ceil(L["x"] + L["w"] + m[0]))), min(H, int(math.ceil(L["y"] + L["h"] + m[1])))
            if rx1 - rx0 < 2 or ry1 - ry0 < 2:
                continue
            times = {start, end}
            out_t = lambda s: (s - t0) / sp                     # noqa: E731
            for c in L["chunks"]:
                cs = out_t(c["words"][0]["s"])
                times.update(cs + CAP_POP * i / CAP_POP_FRAMES for i in range(CAP_POP_FRAMES + 1))
                times.update(out_t(w["s"]) for w in c["words"])
                times.update((out_t(c["words"][-1]["e"]) + CAP_TAIL, out_t(c["words"][-1]["e"]) + CAP_GAP))
            segs = []
            for t in sorted(x for x in times if 0 <= x <= D):
                st = caption_state(L, t, t0, sp) if start <= t < end else None
                segs.append((t, st))

            # text is anti-aliased by FreeType; supersample only for the rounded boxes
            ss = SS if (L["bg_op"] > 0 or L["hi_mode"] == "box") else 1

            ctx = {"L": L, "rx0": rx0, "ry0": ry0, "rw": rx1 - rx0, "rh": ry1 - ry0, "ss": ss}
            f = _write_track(work, name, segs, "caption", ctx, (rx0, ry0, rx1 - rx0, ry1 - ry0), D)
            out.append((z, "track", f, rx0, ry0, 0.0, D))
        elif animated(L):
            segs, posed, n = [], {}, int(math.ceil((end - start) * TRACK_FPS))
            for i in range(n + 1):
                t = min(end, start + i / TRACK_FPS)
                P = pose(L, t, W, H, D)
                key = tuple(round(P[k], 2) for k in ("x", "y", "w", "h", "rot", "opacity")) + (
                    round(P.get("size", 0), 2), round(P.get("stroke_w", 0), 2))
                posed.setdefault(key, P)
                segs.append((t, key if P["opacity"] > 0.002 else None))
            segs.append((end, None))
            # the frames only need to cover the area the layer moves through
            boxes = []
            for P in posed.values():
                bx0, by0, bx1, by1 = _bbox(P)
                if abs(P.get("rot", 0) or 0) % 360 >= 0.01:
                    cx, cy, r = (bx0 + bx1) / 2, (by0 + by1) / 2, math.hypot(bx1 - bx0, by1 - by0) / 2 + 2
                    bx0, by0, bx1, by1 = cx - r, cy - r, cx + r, cy + r
                boxes.append((bx0, by0, bx1, by1))
            rx0 = max(0, int(math.floor(min(b[0] for b in boxes))))
            ry0 = max(0, int(math.floor(min(b[1] for b in boxes))))
            rx1 = min(W, int(math.ceil(max(b[2] for b in boxes))))
            ry1 = min(H, int(math.ceil(max(b[3] for b in boxes))))
            if rx1 - rx0 < 2 or ry1 - ry0 < 2:
                continue

            # Draw the layer once (unturned, opaque, at its largest scale), then make
            # every frame by scaling / turning / fading that picture: much cheaper
            # than drawing text and shapes again for each frame.
            ln = lambda Q: math.hypot(Q["w"], Q["h"]) or 1.0                       # noqa: E731
            scale_of = {key: ln(P) / ln(L) for key, P in posed.items()}
            top = max(1.0, max(scale_of.values()))
            B = dict(L, rot=0, opacity=1.0, kf={})
            cx0, cy0 = L["x"] + L["w"] / 2, L["y"] + L["h"] / 2
            B.update(w=L["w"] * top, h=L["h"] * top, x=cx0 - L["w"] * top / 2, y=cy0 - L["h"] * top / 2)
            for k_ in ("size", "stroke_w", "radius"):
                if k_ in B:
                    B[k_] = B[k_] * top
            bx0, by0, bx1, by1 = _bbox(B)
            half_w, half_h = max(cx0 - bx0, bx1 - cx0), max(cy0 - by0, by1 - cy0)   # centred on the layer
            base_img = _render_box(B, int(math.floor(cx0 - half_w)), int(math.floor(cy0 - half_h)),
                                   max(1, int(math.ceil(2 * half_w))), max(1, int(math.ceil(2 * half_h))), assets_dir)

            ctx = {"posed": posed, "scale_of": scale_of, "top": top, "base": base_img,
                   "rx0": rx0, "ry0": ry0, "rw": rx1 - rx0, "rh": ry1 - ry0}
            f = _write_track(work, name, segs, "anim", ctx, (rx0, ry0, rx1 - rx0, ry1 - ry0), D)
            out.append((z, "track", f, rx0, ry0, 0.0, D))
    return out


# ---------- ffmpeg ----------

def _even(v):
    return max(2, int(round(v / 2)) * 2)


def blur_size(blur, W):
    """Width of the shrunken background copy (0 = no blur, full size); the page does the same."""
    if blur <= 0:
        return W
    return max(12, int(round(W / (1 + 0.3 * blur) / 2)) * 2)


def _scale_fx(L, vw, vh):
    """
    Scale a cropped video layer to vw x vh, with its blur and brightness. A
    blurred layer is shrunk straight to its small blur size, softened and
    scaled back up (cheap: it never goes through full size twice).
    """
    b = L.get("bright", 1)
    shade = f",lutyuv=y=clip((val-16)*{b}+16\\,0\\,255):u=(val-128)*{b}+128:v=(val-128)*{b}+128" if abs(b - 1) > 1e-3 else ""
    if L.get("blur", 0) > 0:
        k = blur_size(L["blur"], max(2, L["w"])) / max(2, L["w"])
        sw, sh = max(12, _even(vw * k)), max(12, _even(vh * k))
        if sw < vw:
            return f"scale={sw}:{sh}:flags=area{shade},boxblur=2:1,scale={vw}:{vh}:flags=bilinear"
    return f"scale={vw}:{vh}:flags=bicubic{shade}"


def _video_piece(L, meta, W, H):
    """ffmpeg filters for a still video layer's visible part and where it goes, or None."""
    x, y, w, h = L["x"], L["y"], L["w"], L["h"]
    vx0, vy0, vx1, vy1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if vx1 - vx0 < 2 or vy1 - vy0 < 2:
        return None
    u0, u1, v0, v1 = (vx0 - x) / w, (vx1 - x) / w, (vy0 - y) / h, (vy1 - y) / h
    if L["flip"]:
        u0, u1 = 1 - u1, 1 - u0
    cx, cy, cw, ch = L["crop"]
    SW, SH = meta["w"], meta["h"]
    sw = min(SW, _even((u1 - u0) * cw * SW))
    sh = min(SH, _even((v1 - v0) * ch * SH))
    sx = min(SW - sw, max(0, int(round((cx + u0 * cw) * SW))))
    sy = min(SH - sh, max(0, int(round((cy + v0 * ch) * SH))))
    vw, vh = min(W, _even(vx1 - vx0)), min(H, _even(vy1 - vy0))
    px = min(W - vw, max(0, int(round(vx0))))
    py = min(H - vh, max(0, int(round(vy0))))
    f = f"crop={sw}:{sh}:{sx}:{sy}{',hflip' if L['flip'] else ''}," + _scale_fx(L, vw, vh)
    if L["opacity"] < 0.999:
        f += f",format=yuva420p,lutyuv=a=val*{L['opacity']}"
    return f, px, py


def _video_piece_animated(L, meta, W, H, D, i, work):
    """
    A moving / fading video layer: the whole crop scaled to its box, placed by
    an overlay whose x / y (and an alpha lookup table) are driven by sendcmd.
    Returns (filters, x, y, overlay name).
    """
    cx, cy, cw, ch = L["crop"]
    SW, SH = meta["w"], meta["h"]
    sw, sh = min(SW, _even(cw * SW)), min(SH, _even(ch * SH))
    sx, sy = min(SW - sw, max(0, int(round(cx * SW)))), min(SH - sh, max(0, int(round(cy * SH))))
    vw, vh = _even(L["w"]), _even(L["h"])
    start, end = L["start"], layer_end(L, D)
    cmds, last = [], None
    n = int(math.ceil((end - start) * TRACK_FPS))
    first = pose(L, start, W, H, D)
    for k in range(n + 1):
        t = min(end, start + k / TRACK_FPS)
        P = pose(L, t, W, H, D)
        v = (int(round(P["x"])), int(round(P["y"])), round(P["opacity"], 3))
        if v != last:
            cmds.append(f"{t:.4f} overlay@vo{i} x {v[0]}, overlay@vo{i} y {v[1]}, lutyuv@va{i} a val*{v[2]};")
            last = v
    with open(os.path.join(work, f"v{i}.cmd"), "w", encoding="utf-8") as f:
        f.write("\n".join(cmds) + "\n")
    f = (f"sendcmd=f=v{i}.cmd,crop={sw}:{sh}:{sx}:{sy}{',hflip' if L['flip'] else ''},"
         f"{_scale_fx(L, vw, vh)},format=yuva420p,lutyuv@va{i}=a=val*{round(first['opacity'], 3)}")
    return f, int(round(first["x"])), int(round(first["y"])), f"overlay@vo{i}"


def _enable(start, end, D):
    if start <= 0.001 and end >= D - 0.001:
        return ""
    return f":enable='between(t,{start:.3f},{end:.3f})'"


def _atempo(speed):
    out = []
    while speed > 2.0:
        out.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        out.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1) > 1e-3:
        out.append(f"atempo={speed:.4f}")
    return out


def build_graph(doc, meta, overlays, work=".", watermark=False):
    """The filter graph: [v] (and [a] when there's sound). `overlays` are PNGs and image tracks."""
    W, H = canvas_size(doc["format"])
    D = duration_of(doc)
    sp = doc["speed"]
    c = doc["color"]
    head = f"[0:v]setpts=(PTS-STARTPTS)/{sp},setsar=1"
    if abs(sp - 1) > 1e-3:
        head += f",fps={meta['fps']}"          # drop / repeat frames before the heavy work
    if abs(c["brightness"] - 1) > 1e-3 or abs(c["saturation"] - 1) > 1e-3 or abs(c["contrast"] - 1) > 1e-3:
        head += ",format=gbrp"
        b, k = c["brightness"], c["contrast"]
        if abs(b - 1) > 1e-3 or abs(k - 1) > 1e-3:
            # brightness, then contrast (the CSS filter order), as one lookup table
            e = f"clip((val*{b}-128)*{k}+128\\,0\\,255)"
            head += f",lutrgb=r='{e}':g='{e}':b='{e}'"
        s = c["saturation"]
        if abs(s - 1) > 1e-3:
            # the CSS saturate() matrix
            m = [[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                 [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                 [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]]
            head += (",colorchannelmixer=" + ":".join(f"{a}{b_}={m[i][j]:.4f}" for i, a in enumerate("rgb")
                                                      for j, b_ in enumerate("rgb")))
        head += ",format=yuv420p"
    videos = [(z, L) for z, L in enumerate(doc["layers"]) if L["type"] == "video" and not L["hidden"]]
    pieces = []
    for z, L in videos:
        if L["start"] >= D:
            continue
        if animated(L):
            f, px, py, name = _video_piece_animated(L, meta, W, H, D, len(pieces), work)
            pieces.append((z, L, (f, px, py), name))
        else:
            p = _video_piece(L, meta, W, H)
            if p:
                pieces.append((z, L, p, "overlay"))
    n = len(pieces)
    # composite bottom to top: video pieces, PNGs and image tracks in layer order
    items = [(z, "v", i) for i, (z, _, _, _) in enumerate(pieces)] + [(o[0], "o", i) for i, o in enumerate(overlays)]
    items.sort(key=lambda t: (t[0], t[1] == "o"))
    # When the bottom layer is a still, opaque video that always covers the whole
    # frame (the usual blurred background), it *is* the base: no colour frame
    # to make and nothing to overlay it on.
    base = None
    if items and items[0][1] == "v":
        _, L0, (f0, px0, py0), oname0 = pieces[items[0][2]]
        if (oname0 == "overlay" and (px0, py0) == (0, 0) and f"scale={W}:{H}:" in f0.split(",")[-1] + ":"
                and L0["opacity"] >= 0.999 and L0["start"] <= 0.001 and layer_end(L0, D) >= D - 0.001):
            base = items.pop(0)[2]
    bg = doc["bg"]
    if base is not None:
        chains = [f"{head},split={n}" + "".join(f"[p{i}]" for i in range(n)) if n > 1 else f"{head}[p0]",
                  f"[p{base}]{pieces[base][2][0]},setsar=1[c0]"]
    elif bg["type"] == "color":
        chains = [f"{head},split={n + 1}[bgsrc]" + "".join(f"[p{i}]" for i in range(n))]
        r, g, b_ = (int(bg["color"][i:i + 2], 16) for i in (1, 3, 5))
        y = round(16 + 0.257 * r + 0.504 * g + 0.098 * b_)
        u = round(128 - 0.148 * r - 0.291 * g + 0.439 * b_)
        v = round(128 + 0.439 * r - 0.368 * g - 0.071 * b_)
        # a 2x2 frame set to the colour and blown up without interpolation: cheap
        chains.append(f"[bgsrc]scale=2:2,format=yuv420p,lutyuv=y={y}:u={u}:v={v},"
                      f"scale={W}:{H}:flags=neighbor,setsar=1[c0]")
    else:
        chains = [f"{head},split={n + 1}[bgsrc]" + "".join(f"[p{i}]" for i in range(n))]
        bw = blur_size(bg["blur"], W)
        bh = _even(bw * H / W)
        op = bg["opacity"]
        chain = f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}"
        if op < 0.999:
            chain += f",colorchannelmixer=rr={op}:gg={op}:bb={op}"
        if bw < W:
            chain += f",boxblur=2:1,scale={W}:{H}:flags=bilinear"
        chains.append(chain + ",setsar=1[c0]")
    cur = "c0"
    for k, (z, kind, i) in enumerate(items):
        nxt = f"c{k + 1}"
        if kind == "v":
            _, L, (f, px, py), oname = pieces[i]
            chains.append(f"[p{i}]{f}[f{i}]")
            chains.append(f"[{cur}][f{i}]{oname}=x={px}:y={py}{_enable(L['start'], layer_end(L, D), D)}[{nxt}]")
        else:
            _, _, _, ox, oy, start, end = overlays[i]
            chains.append(f"[{i + 1}:v]format=yuva420p[o{i}]")
            chains.append(f"[{cur}][o{i}]overlay={ox}:{oy}:format=yuv420{_enable(start, end, D)}[{nxt}]")
        cur = nxt
    tail = []
    if doc["fade_in"] > 0:
        tail.append(f"fade=t=in:st=0:d={doc['fade_in']}")
    if doc["fade_out"] > 0:
        tail.append(f"fade=t=out:st={max(0, D - doc['fade_out']):.3f}:d={doc['fade_out']}")
    ow, oh = output_size(doc)
    if (ow, oh) != (W, H):
        tail.append(f"scale={ow}:{oh}:flags=bicubic")
    if watermark:
        tail.append(reels.watermark_filter(ow, oh))
    # scale keeps the display shape by changing the pixel aspect; the output must have square pixels
    tail.append("setsar=1")
    tail.append("format=yuv420p")
    chains.append(f"[{cur}]" + ",".join(tail) + "[v]")
    audio = bool(meta.get("audio")) and doc["volume"] > 0
    if audio:
        a = ["asetpts=PTS-STARTPTS", *_atempo(sp)]
        if abs(doc["volume"] - 1) > 1e-3:
            a.append(f"volume={doc['volume']}")
        if doc["fade_in"] > 0:
            a.append(f"afade=t=in:st=0:d={doc['fade_in']}")
        if doc["fade_out"] > 0:
            a.append(f"afade=t=out:st={max(0, D - doc['fade_out']):.3f}:d={doc['fade_out']}")
        chains.append("[0:a]" + ",".join(a) + "[a]")
    return ";".join(chains), audio


def render(src, dst, doc, meta, assets_dir, progress=None, watermark=False):
    """Render the (cleaned) document to `dst` (free plan: with a watermark). Returns the encoder used."""
    D = duration_of(doc)
    t0, t1 = doc["trim"]
    work = tempfile.mkdtemp(prefix="recaply_edit_")

    def report(frac, stage):
        if progress:
            try:
                progress(frac, stage)
            except TypeError:               # an older one-argument callback
                progress(frac)
    try:
        heavy = any(L["type"] == "captions" or animated(L) for L in doc["layers"] if not L["hidden"])
        report(0.0, "Drawing captions and animations" if heavy else "Preparing")
        doc = bind_fonts(doc, assets_dir)        # custom fonts: drawing only, never saved
        overlays = overlay_groups(doc, assets_dir, work) + _tracks(doc, assets_dir, work)
        report(0.0, "Encoding")
        watermark = watermark and reels.prepare_watermark(work)
        graph, audio = build_graph(doc, meta, overlays, work, watermark)
        encoders = [h264_encoder()]
        if encoders[0] != "libx264":
            encoders.append("libx264")
        tmp = os.path.abspath(dst) + ".part.mp4"
        err = ""
        for enc in encoders:
            cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
            if t0 > 0:
                cmd += ["-ss", f"{t0:.3f}"]
            cmd += ["-t", f"{t1 - t0:.3f}", "-i", os.path.abspath(src)]
            for o in overlays:
                if o[1] == "track":
                    cmd += ["-f", "concat", "-safe", "0", "-i", o[2]]
                else:
                    cmd += ["-i", o[2]]      # one frame; overlay repeats it (eof_action=repeat)
            cmd += ["-filter_complex", graph, "-map", "[v]"]
            if audio:
                cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
            cmd += ["-c:v", enc, *ENCODER_ARGS[enc], "-t", f"{D:.3f}",
                    "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=work)
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    try:
                        report(min(1.0, int(line.split("=", 1)[1]) / 1e6 / D), "Encoding")
                    except ValueError:
                        pass
            err = proc.stderr.read()
            if proc.wait() == 0 and os.path.exists(tmp):
                os.replace(tmp, dst)
                return enc
            if os.path.exists(tmp):
                os.remove(tmp)
        raise RuntimeError(f"Export failed: {err.strip()[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def waveform(src, n=2400):
    """Loudness peaks of the audio in `n` buckets (0..1), for the timeline."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", src, "-vn", "-ac", "1", "-ar", "4000",
                          "-f", "s16le", "pipe:1"], capture_output=True, timeout=600).stdout
    a = np.abs(np.frombuffer(raw, np.int16).astype(np.float32)) / 32768.0
    if not len(a):
        return []
    peaks = [float(b.max()) if len(b) else 0.0 for b in np.array_split(a, n)]
    m = max(peaks) or 1.0
    return [round((p / m) ** 0.7, 3) for p in peaks]


def poster(src, dst, at):
    """A 480px-wide JPEG still from `at` seconds (project thumbnails)."""
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{max(0, at):.2f}", "-i", src,
                    "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", dst], capture_output=True, timeout=60)
    return os.path.exists(dst)


# ---------- reaction (watch party) layout detection ----------
#
# A watch party stream is the broadcast (usually full screen) with the
# streamer's webcam in a box somewhere on top. To find the cam:
#   1. Sample ~24 frames across the clip and find faces (YuNet). The
#      streamer's face stays in the same place; faces in the broadcast (player
#      cams, the desk) come and go. The face seen in the most frames wins.
#   2. The cam's border is a straight edge that stays put while the gameplay
#      around it keeps changing, so edges that persist across the samples are
#      border candidates (frame edges count too: cams often sit in a corner).
#   3. Every combination of candidate sides around the face is scored by how
#      much of each side is a persistent edge, how close the box is to a
#      webcam shape (16:9 or 4:3), and where the face sits in it (people frame
#      themselves with their face about a third of the way down). A missing
#      side can be inferred from the shape.
# The gameplay area is the largest 16:9 box drawn by long persistent lines
# (a scaled-down broadcast), else the whole frame minus black bars.

DETECT_W = 960


def _sample_frames(path, duration, n=24):
    ts = np.linspace(duration * 0.04, duration * 0.96, n) if duration > 2 else [0.0]

    def one(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale={DETECT_W}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                             capture_output=True, timeout=60).stdout
        h = len(raw) // (DETECT_W * 3)
        return np.frombuffer(raw[:h * DETECT_W * 3], np.uint8).reshape(h, DETECT_W, 3) if h else None

    with ThreadPoolExecutor(8) as ex:
        frames = [f for f in ex.map(one, ts) if f is not None]
    if not frames:
        raise RuntimeError("Couldn't read frames from the video.")
    h = min(f.shape[0] for f in frames)
    return [f[:h] for f in frames]


def _streamer_face(frames):
    import cv2
    H = frames[0].shape[0]
    det = cv2.FaceDetectorYN.create(FACE_MODEL, "", (DETECT_W, H), 0.75)
    faces = []
    for i, f in enumerate(frames):
        r = det.detect(f)[1]
        for row in (r if r is not None else []):
            if row[2] >= 10 and row[3] >= 10:
                faces.append((i, *map(float, row[:4])))
    best = None
    for (_, x, y, w, h) in faces:
        cx, cy = x + w / 2, y + h / 2
        m = [g for g in faces if abs(g[1] + g[3] / 2 - cx) < 0.6 * w and abs(g[2] + g[4] / 2 - cy) < 0.6 * h
             and 0.6 < g[3] / w < 1.6]
        nf = len({g[0] for g in m})
        if best is None or (nf, w) > (best[0], best[1]):
            best = (nf, w, m)
    if not best or best[0] < max(3, len(frames) * 0.25):
        return None, 0.0
    face = np.median(np.array([g[1:] for g in best[2]]), axis=0)
    return tuple(float(v) for v in face), best[0] / len(frames)


def _persistent_edges(frames):
    import cv2
    edges = np.array([cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (3, 3), 0), 40, 120) > 0
                      for f in frames])
    P = edges.mean(0)
    Pv = np.maximum(np.maximum(P, np.roll(P, 1, 1)), np.roll(P, -1, 1)) >= 0.6    # vertical lines
    Ph = np.maximum(np.maximum(P, np.roll(P, 1, 0)), np.roll(P, -1, 0)) >= 0.6    # horizontal lines
    return Pv.astype(np.float32), Ph.astype(np.float32)


def _candidates(profile, lo, hi, k=16):
    lo, hi = max(0, int(lo)), min(len(profile), int(hi))
    xs = [x for x in range(lo + 1, hi - 1)
          if profile[x] >= 0.12 and profile[x] >= profile[x - 1] and profile[x] >= profile[x + 1]]
    xs = sorted(xs, key=lambda x: -profile[x])[:k]
    if lo <= 1:
        xs.append(0)
    if hi >= len(profile) - 1:
        xs.append(len(profile) - 1)
    return xs


def _find_cam(face, Pv, Ph):
    H, W = Pv.shape
    cv = np.vstack([np.zeros((1, W), np.float32), np.cumsum(Pv, 0)])
    ch = np.hstack([np.zeros((H, 1), np.float32), np.cumsum(Ph, 1)])

    def vs(x, y0, y1):                  # share of a vertical side that is a persistent edge
        if x <= 1 or x >= W - 2:
            return 0.8
        y0, y1 = max(0, int(y0)), min(H, int(y1))
        return float(cv[y1, x] - cv[y0, x]) / max(1, y1 - y0)

    def hs(y, x0, x1):
        if y <= 1 or y >= H - 2:
            return 0.8
        x0, x1 = max(0, int(x0)), min(W, int(x1))
        return float(ch[y, x1] - ch[y, x0]) / max(1, x1 - x0)

    fx, fy, fw, fh = face
    cp = Pv[int(max(0, fy - 2 * fh)):int(min(H, fy + 3 * fh))].mean(0)
    rp = Ph[:, int(max(0, fx - 3 * fw)):int(min(W, fx + 4 * fw))].mean(1)
    Ls = _candidates(cp, fx - 7 * fw, fx - 0.3 * fw)
    Rs = _candidates(cp, fx + 1.3 * fw, fx + 8 * fw)
    Ts = _candidates(rp, fy - 4 * fh, fy - 0.2 * fh)
    Bs = _candidates(rp, fy + 1.4 * fh, fy + 6 * fh)
    best = None
    for left in Ls:
        for right in Rs:
            w = right - left
            if not 3 * fw <= w <= 11 * fw:
                continue
            for t0 in Ts + [None]:
                for b0 in Bs + [None]:
                    if t0 is not None and b0 is not None:
                        options = [(t0, b0)]
                    elif b0 is not None:
                        options = [(b0 - w / k, b0) for k in (16 / 9, 4 / 3)]
                    elif t0 is not None:
                        options = [(t0, t0 + w / k) for k in (16 / 9, 4 / 3)]
                    else:
                        options = []
                    for top, bottom in options:
                        top, bottom = int(round(top)), int(round(bottom))
                        h = bottom - top
                        if top < 0 or bottom > H - 1 or h < 1.6 * fh or not (top < fy and bottom > fy + fh):
                            continue
                        a = w / h
                        shape = max(math.exp(-(math.log(a / (16 / 9)) / 0.12) ** 2),
                                    0.85 * math.exp(-(math.log(a / (4 / 3)) / 0.12) ** 2))
                        sides = (vs(left, top, bottom) + vs(right, top, bottom)
                                 + hs(top, left, right) + hs(bottom, left, right)) / 4
                        pos = math.exp(-(((fy - top) / h - 0.37) / 0.14) ** 2)
                        score = 0.55 * sides + 0.3 * shape + 0.15 * pos
                        if best is None or score > best[0]:
                            best = (score, left, top, right + 1, bottom + 1)
    if best and best[0] >= 0.5:
        return best[1:], best[0]
    # No clear box: a 16:9 box around the face, sized like a typical webcam shot.
    w = min(W, 6 * fw)
    h = min(H, w * 9 / 16)
    left = min(max(0, fx + fw / 2 - w / 2), W - w)
    top = min(max(0, fy - 0.37 * h), H - h)
    return (left, top, left + w, top + h), 0.0


def _find_gameplay(frames, Pv, Ph, cam):
    H, W = Pv.shape
    gray = np.median(np.array([f.mean(2) for f in frames]), axis=0)
    rows = np.where(gray.mean(1) > 14)[0]
    cols = np.where(gray.mean(0) > 14)[0]
    full = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1) if len(rows) and len(cols) else (0, 0, W, H)
    colf, rowf = Pv.mean(0), Ph.mean(1)
    xs = sorted({0, W, *[x for x in range(2, W - 2) if colf[x] > 0.3 and colf[x] >= colf[x - 1] and colf[x] >= colf[x + 1]]})
    ys = sorted({0, H, *[y for y in range(2, H - 2) if rowf[y] > 0.3 and rowf[y] >= rowf[y - 1] and rowf[y] >= rowf[y + 1]]})
    best = None
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    w, h = x1 - x0, y1 - y0
                    if w * h < 0.2 * W * H or abs(w / h / (16 / 9) - 1) > 0.05:
                        continue
                    if (x0, y0, x1, y1) == (0, 0, W, H):
                        continue
                    if best is None or w * h > best[0]:
                        best = (w * h, (x0, y0, x1, y1))
    if best and best[0] < 0.9 * W * H:
        return best[1], True
    return full, False


def detect_reaction(path, meta):
    """
    Where the streamer's cam and the gameplay are, as fractions of the frame:
    {"cam": [x, y, w, h], "face": [...], "game": [...], "found": bool, ...}.
    """
    frames = _sample_frames(path, meta["duration"])
    H, W = frames[0].shape[:2]
    face, seen = _streamer_face(frames)
    Pv, Ph = _persistent_edges(frames)
    frac = lambda b: [round(b[0] / W, 4), round(b[1] / H, 4),                    # noqa: E731
                      round((b[2] - b[0]) / W, 4), round((b[3] - b[1]) / H, 4)]
    if face is None:
        cam, conf = None, 0.0
    else:
        cam, conf = _find_cam(face, Pv, Ph)
    game, boxed = _find_gameplay(frames, Pv, Ph, cam)
    return {
        "found": face is not None,
        "cam": frac(cam) if cam else None,
        "cam_confidence": round(conf, 2),
        "face": frac((face[0], face[1], face[0] + face[2], face[1] + face[3])) if face else None,
        "face_seen": round(seen, 2),
        "game": frac(game),
        "game_boxed": boxed,
    }
