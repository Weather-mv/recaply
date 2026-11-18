"""
reels.py — turn a 16:9 clip into a 9:16 vertical reel (1080x1920 or 720x1280).

Every reel is a blurred copy of the clip filling the whole frame (blur and
opacity adjustable), with the clip itself scaled and placed freely on top,
an optional text caption, a trimmed time range and adjustable volume.

Layouts (how the clip sits on the background):
  blur   "Fit": the whole frame, full width at 100% size.
  crop   "Fill": full height at 100% zoom (the sides spill out of the frame).
  split  the whole frame in a top section, and a close-up of the action in
         the section below it (the close-up stays inside its section).
  pov    "POV": the player's view, full height and zoomed in a little,
         centred on the crosshair, with the kill feed traced in on top.

Layout options: `zoom` (0.25-3), `ox` / `oy` (-1..1, how far the video is
moved, as a share of its own width / height; 0 = centred), for split `top`
(the top section's share of the height), and for every layout the kill
feed: `kf` (1 = on), `kfs` (its size) and `kfy` (0 = high, 1 = low).

The kill feed sits in the top-right corner of a Valorant screen (KILLFEED),
which Fill and POV crop away. When it's on, that corner is cut out of the
source and placed back inside the reel, so every kill stays readable.

Style (shared by every layout): `blur` (0-100), `bgop` (background opacity
0-1: how visible the blurred copy is over black), `tsize` (caption size in
px at 1080 wide), `ty` (caption position, 0 = top, 1 = bottom), `vol`
(0-1, 0 mutes).

Geometry, blur size and caption wrapping are worked out here and mirrored
exactly by the page's live preview. Output is H.264 + AAC in an MP4 with
faststart, re-encoded on the GPU when there is one.
"""

import os
import shutil
import subprocess
import tempfile

from pipeline import ENCODER_ARGS, h264_encoder, probe_media

W, H = 1080, 1920                 # geometry frame (the 1080 output)
SW, SH = 1920, 1080               # geometry source
LAYOUTS = ("blur", "crop", "split", "pov")
SIZES = {"1080": (1080, 1920), "720": (720, 1280)}
DEFAULTS = {
    "blur": {"zoom": 1.0, "ox": 0.0, "oy": 0.0, "kf": 0.0, "kfs": 1.0, "kfy": 0.25},
    "crop": {"zoom": 1.0, "ox": 0.0, "oy": 0.0, "kf": 1.0, "kfs": 1.0, "kfy": 0.25},
    # The close-up is centred a little above the middle of the screen (the
    # HUD's ability bar sits at the bottom).
    "split": {"zoom": 1.0, "ox": 0.0, "oy": 0.03, "top": 608 / 1920, "kf": 0.0, "kfs": 1.0, "kfy": 0.25},
    "pov": {"zoom": 1.18, "ox": 0.0, "oy": 0.0, "kf": 1.0, "kfs": 1.0, "kfy": 0.22},
}
RANGES = {"zoom": (0.25, 3.0), "ox": (-1.0, 1.0), "oy": (-1.0, 1.0), "top": (0.25, 0.6),
          "kf": (0.0, 1.0), "kfs": (0.5, 1.6), "kfy": (0.0, 1.0)}
# The kill feed on a 1920x1080 Valorant screen (in-game and VCT broadcast):
# up to six rows, right-aligned below the top edge.
KILLFEED = (1180, 84, 740, 240)
KF_WIDTH = 600                    # its width in the reel at size 100%
WATERMARK = "Made with Recaply"
SPLIT_BASE = 900                  # source rows that fill the close-up at 100%
STYLE_DEFAULTS = {"blur": 50, "bgop": 0.82, "tsize": 72, "ty": 0.1, "vol": 1.0}
STYLE_RANGES = {"blur": (0, 100), "bgop": (0.0, 1.0), "tsize": (36, 150), "ty": (0.0, 1.0), "vol": (0.0, 1.0)}
TEXT_MAX = 80                     # caption characters
TEXT_LINES = 3
FONTS = (                         # bold sans for captions, first one found wins
    os.environ.get("RECAPLY_FONT", ""),
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _clamp(raw, key, default, lo, hi):
    try:
        v = float(raw.get(key, default))
    except (TypeError, ValueError, AttributeError):
        v = default
    if v != v:                    # NaN
        v = default
    return round(min(hi, max(lo, v)), 4)


def clean_opts(layout, raw):
    """Layout options from untrusted input, clamped to their ranges."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: _clamp(raw, k, d, *RANGES[k]) for k, d in DEFAULTS[layout].items()}


def clean_style(raw):
    """Style options from untrusted input, clamped to their ranges."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: _clamp(raw, k, d, *STYLE_RANGES[k]) for k, d in STYLE_DEFAULTS.items()}


def clean_text(text):
    return " ".join(str(text or "").split())[:TEXT_MAX]


def font_file():
    return next((f for f in FONTS if f and os.path.exists(f)), None)


# ---------- geometry ----------

def _placed(o, base_w, region):
    """The whole source scaled to `base_w * zoom` wide, centred in `region`, then moved."""
    rx, ry, rw, rh = region
    dw = base_w * o["zoom"]
    dh = dw * SH / SW
    dx = rx + (rw - dw) / 2 + o["ox"] * dw
    dy = ry + (rh - dh) / 2 + o["oy"] * dh
    return dx, dy, dw, dh


def _visible(placed, region):
    """Clip a placed source rect to `region`: (sx, sy, sw, sh, vx, vy, vw, vh) or None."""
    dx, dy, dw, dh = placed
    rx, ry, rw, rh = region
    vx0, vy0 = max(dx, rx), max(dy, ry)
    vx1, vy1 = min(dx + dw, rx + rw), min(dy + dh, ry + rh)
    if vx1 - vx0 < 2 or vy1 - vy0 < 2:
        return None
    k = SW / dw                   # source pixels per frame pixel
    return ((vx0 - dx) * k, (vy0 - dy) * k, (vx1 - vx0) * k, (vy1 - vy0) * k,
            vx0, vy0, vx1 - vx0, vy1 - vy0)


def killfeed_box(o):
    """Where the kill feed goes: the source corner into the top right of the reel."""
    sx, sy, sw, sh = KILLFEED
    vw = KF_WIDTH * o.get("kfs", 1.0)
    vh = vw * sh / sw
    vx = W - vw - 36
    vy = 60 + (H * 0.5 - 60 - vh) * o.get("kfy", 0.25)
    return (sx, sy, sw, sh, vx, vy, vw, vh)


def boxes(layout, o):
    """
    Pieces drawn over the background: [(sx, sy, sw, sh, vx, vy, vw, vh)],
    a source rectangle (1920x1080 space) into a frame rectangle (1080x1920).
    """
    frame = (0, 0, W, H)
    if layout == "blur":
        pieces = [_visible(_placed(o, W, frame), frame)]
    elif layout in ("crop", "pov"):
        pieces = [_visible(_placed(o, H * SW / SH, frame), frame)]      # full height at 100%
    else:
        th = H * o["top"]
        top, bottom = (0, 0, W, th), (0, th, W, H - th)
        cover_w = max(W, th * SW / SH)
        top_piece = _visible(_placed({"zoom": 1, "ox": 0, "oy": 0}, cover_w, top), top)
        base_w = (H - th) / SPLIT_BASE * SW
        pieces = [top_piece, _visible(_placed(o, base_w, bottom), bottom)]
    if o.get("kf", 0) >= 0.5:
        pieces.append(killfeed_box(o))
    return [p for p in pieces if p]


def blur_size(blur):
    """Width of the shrunken background (0 = no blur, full size)."""
    if blur <= 0:
        return W
    return max(12, int(round(W / (1 + 0.3 * blur) / 2)) * 2)


def wrap(text, size):
    """Caption lines: greedy word wrap by an average glyph width (the page does the same)."""
    per_line = max(6, int(1000 // (size * 0.56)))
    lines, cur = [], ""
    for word in text.split():
        while len(word) > per_line:                  # a very long word
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:per_line])
            word = word[per_line:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= per_line:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:TEXT_LINES]


def text_layout(text, st):
    """[(line, y_top)] for the caption, in the 1080x1920 frame."""
    lines = wrap(text, st["tsize"]) if text else []
    if not lines:
        return []
    lh = st["tsize"] * 1.18
    block = lh * len(lines)
    top = 60 + (H - 120 - block) * st["ty"]
    return [(line, top + i * lh) for i, line in enumerate(lines)]


# ---------- ffmpeg ----------

def _even(v):
    return max(2, int(round(v / 2)) * 2)


def _piece(sx, sy, sw, sh, vx, vy, vw, vh):
    """ffmpeg crop+scale for one piece, and where to overlay it."""
    sw, sh = min(SW, _even(sw)), min(SH, _even(sh))
    sx = min(SW - sw, max(0, int(round(sx))))
    sy = min(SH - sh, max(0, int(round(sy))))
    vw, vh = _even(vw), _even(vh)
    vx = min(W - vw, max(0, int(round(vx))))
    vy = min(H - vh, max(0, int(round(vy))))
    return f"crop={sw}:{sh}:{sx}:{sy},scale={vw}:{vh}:flags=lanczos", vx, vy


def prepare_watermark(work):
    """Put the watermark's font and text in `work` (ffmpeg runs there). False without a font."""
    font = font_file()
    if not font:
        return False
    shutil.copyfile(font, os.path.join(work, "wm.ttf"))
    with open(os.path.join(work, "wm.txt"), "w", encoding="utf-8") as f:
        f.write(WATERMARK)
    return True


def watermark_filter(w, h):
    """drawtext for the free plan's watermark on a w x h output (see prepare_watermark)."""
    size = max(14, int(round(min(w, h) * 0.034)))
    m = int(round(size * 0.9))
    # Portrait: centred above the platform's own buttons; landscape: bottom right.
    pos = f"x=(w-text_w)/2:y=h-text_h-{int(h * 0.13)}" if h > w else f"x=w-text_w-{m}:y=h-text_h-{m}"
    return (f"drawtext=fontfile=wm.ttf:textfile=wm.txt:expansion=none:fontsize={size}:fontcolor=white@0.85"
            f":borderw={max(1, size // 12)}:bordercolor=black@0.5:{pos}")


def _filter(layout, o, st, size="1080", lines=(), font="font.ttf", watermark=False):
    """
    The filter graph. `lines` are [(textfile, y)] caption lines; text and
    font files are referenced by name, so ffmpeg must run in their folder.
    """
    ow, oh = SIZES.get(size, SIZES["1080"])
    # Normalise any source to 1920x1080 so the geometry holds.
    norm = "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1,setsar=1"
    parts = boxes(layout, o)
    n = len(parts)
    # Background: shrinking and scaling back up blurs for almost nothing (a
    # real blur at full size is slow); opacity darkens it towards black.
    bw = blur_size(st["blur"])
    bh = _even(bw * H / W)
    op = st["bgop"]
    dim = f",colorchannelmixer=rr={op}:gg={op}:bb={op}" if op < 0.999 else ""
    soft = ",boxblur=2:1" if bw < W else ""
    up = f",scale={W}:{H}:flags=bilinear" if bw < W else ""
    chains = [f"{norm},split={n + 1}[bgsrc]" + "".join(f"[p{i}]" for i in range(n)),
              f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}{dim}{soft}{up}[l0]"]
    for i, p in enumerate(parts):
        f, x, y = _piece(*p)
        chains.append(f"[p{i}]{f}[f{i}]")
        chains.append(f"[l{i}][f{i}]overlay={x}:{y}[l{i + 1}]")
    size_px = int(round(st["tsize"]))
    border = max(2, int(round(size_px * 0.07)))
    text = "".join(
        f",drawtext=fontfile={font}:textfile={tf}:expansion=none:fontsize={size_px}:fontcolor=white"
        f":borderw={border}:bordercolor=black@0.6:x=(w-text_w)/2:y={int(round(ty))}"
        for tf, ty in lines)
    tail = f",scale={ow}:{oh}" if (ow, oh) != (W, H) else ""
    if watermark:
        tail += "," + watermark_filter(ow, oh)
    chains.append(f"[l{n}]null{text}{tail},setsar=1,format=yuv420p[v]")
    return ";".join(chains)


def make_reel(src, dst, layout="blur", opts=None, style=None, start=0.0, end=None, text="",
              size="1080", progress=None, watermark=False):
    """Render `src` as a 9:16 reel at `dst` (free plan: with a watermark). Returns the encoder used."""
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown layout {layout!r}")
    o, st, text = clean_opts(layout, opts), clean_style(style), clean_text(text)
    _, audio, duration = probe_media(src)
    start = min(max(0.0, float(start or 0)), max(0.0, duration - 1))
    end = duration if not end else min(duration, max(start + 1, float(end)))
    length = end - start
    work = tempfile.mkdtemp(prefix="recaply_reel_")
    try:
        lines = []
        font = font_file()
        if text and font:
            shutil.copyfile(font, os.path.join(work, "font.ttf"))
            for i, (line, y) in enumerate(text_layout(text, st)):
                with open(os.path.join(work, f"t{i}.txt"), "w", encoding="utf-8") as f:
                    f.write(line)
                lines.append((f"t{i}.txt", y))
        watermark = watermark and prepare_watermark(work)
        graph = _filter(layout, o, st, size, lines, watermark=watermark)
        encoders = [h264_encoder()]
        if encoders[0] != "libx264":
            encoders.append("libx264")   # fall back to the CPU if the GPU chokes
        tmp = os.path.abspath(dst) + ".part.mp4"
        err = ""
        for enc in encoders:
            cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
            if start > 0:
                cmd += ["-ss", f"{start:.3f}"]
            cmd += ["-t", f"{length:.3f}", "-i", os.path.abspath(src),
                    "-filter_complex", graph, "-map", "[v]"]
            if audio and st["vol"] > 0:
                cmd += ["-map", "0:a:0"]
                if st["vol"] < 0.999:
                    cmd += ["-af", f"volume={st['vol']}", "-c:a", "aac", "-b:a", "160k"]
                else:
                    cmd += ["-c:a", "copy"] if audio == "aac" else ["-c:a", "aac", "-b:a", "160k"]
            cmd += ["-c:v", enc, *ENCODER_ARGS[enc],
                    "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=work)
            for line in proc.stdout:
                if progress and line.startswith("out_time_us="):
                    try:
                        progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / length))
                    except ValueError:
                        pass
            err = proc.stderr.read()
            if proc.wait() == 0 and os.path.exists(tmp):
                os.replace(tmp, dst)
                return enc
            if os.path.exists(tmp):
                os.remove(tmp)
        raise RuntimeError(f"Making the reel failed: {err.strip()[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def frame_jpg(src, dst, at=0.4):
    """A 640x360 still from `at` (fraction) of the clip, for layout previews."""
    _, _, duration = probe_media(src)
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{duration * at:.2f}", "-i", src,
                    "-frames:v", "1", "-vf", "scale=640:360", "-q:v", "4", dst],
                   capture_output=True, timeout=60)
    return os.path.exists(dst)
