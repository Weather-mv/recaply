"""
Cinematic teaser hook: a 3-second cold open placed in front of a short.

It shows the clip's most striking picture (sharp, contrasty, colourful, with something going on),
slowly pushed in, darkened, between letterbox bars, with the hook line over it. The last moments
glitch like a TV losing signal (RGB split, noise) and the first moments of the short glitch back in,
so the cut feels deliberate.

    t = pick_time(source)                         # the best moment (seconds into the clip)
    build(still_png, audio_src, t, body, dst, ...) # teaser + body in one ffmpeg pass

The still is made by the caller from the reframed video layers (no captions) so it has the same
9:16 crop as the short. Everything here is plain ffmpeg + OpenCV, no network.
"""

from __future__ import annotations

import os
import subprocess

TEASER_SEC = 3.0
GLITCH_OUT = 0.45          # the teaser's last seconds glitch...
GLITCH_IN = 0.3            # ...and the short's first seconds glitch back in
BAR_FRAC = 0.115           # letterbox bar height, as a share of the canvas height


def _norm(v: list[float]) -> list[float]:
    lo, hi = min(v), max(v)
    return [0.5] * len(v) if hi - lo < 1e-9 else [(x - lo) / (hi - lo) for x in v]


def pick_time(video: str, duration: float, step: float = 0.5, margin: float = 0.4) -> float:
    """
    The most striking moment of `video`: sampled every `step` s (skipping the first / last `margin` s),
    scored by sharpness (focus, not blur), contrast, colour and how much changed since the previous sample
    (something is happening). Returns seconds from the start; the middle of the clip when nothing can be read.
    """
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return max(0.0, duration / 2)
    times, sharp, contrast, color, change = [], [], [], [], []
    prev = None
    try:
        t = margin
        while t <= max(margin, duration - margin):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            k = 320.0 / max(h, w)
            small = cv2.resize(frame, (max(2, int(w * k)), max(2, int(h * k))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            times.append(t)
            sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            contrast.append(float(gray.std()))
            color.append(float(hsv[..., 1].mean()))
            change.append(float(np.abs(gray.astype("int16") - prev).mean()) if prev is not None else 0.0)
            prev = gray.astype("int16")
            t += step
    finally:
        cap.release()
    if not times:
        return max(0.0, duration / 2)
    score = [0.35 * a + 0.25 * b + 0.15 * c + 0.25 * d
             for a, b, c, d in zip(_norm(sharp), _norm(contrast), _norm(color), _norm(change))]
    return float(times[max(range(len(times)), key=score.__getitem__)])


def overlay_png(path: str, W: int, H: int, text: str, accent: str = "#0865ff", font: str | None = None,
                assets: str | None = None) -> str:
    """The teaser's graphics as one transparent picture: letterbox bars, an accent line and the hook text."""
    from PIL import Image, ImageDraw

    import editor
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bar = int(round(H * BAR_FRAC))
    d.rectangle([0, 0, W, bar], fill=(0, 0, 0, 255))
    d.rectangle([0, H - bar, W, H], fill=(0, 0, 0, 255))
    text = " ".join((text or "").split())[:90]
    if text:
        size = int(round(W * (0.095 if H > W else 0.07)))
        fnt = editor._font(size)
        lines = editor.wrap_text(text, W * 0.82, fnt.getlength)[:4]
        lh = size * 1.18
        block = lh * len(lines)
        top = H / 2 - block / 2 + H * 0.04
        for i, line in enumerate(lines):
            x = (W - fnt.getlength(line)) / 2
            y = top + i * lh
            d.text((x, y), line, font=fnt, fill=(255, 255, 255, 255), stroke_width=max(2, size // 14), stroke_fill=(0, 0, 0, 255))
        rgb = tuple(int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) if len(accent.lstrip("#")) == 6 else (8, 101, 255)
        d.rectangle([W * 0.34, top - size * 0.35, W * 0.66, top - size * 0.35 + max(4, size // 10)], fill=(*rgb, 255))
    img.save(path)
    return path


def build(still: str, overlay: str, audio_src: str, audio_at: float, body: str, dst: str, W: int, H: int,
          fps: float, has_audio: bool = True, body_has_audio: bool = True) -> None:
    """Write dst = [3 s teaser from `still`] + body (one ffmpeg pass, GPU encode when there is one)."""
    from pipeline import ENCODER_ARGS, h264_encoder
    from recaply.ingest.audio import media_duration
    T = TEASER_SEC
    g0 = T - GLITCH_OUT
    glitch_out = (f"rgbashift=rh=-14:bh=14:gv=6:enable='gte(t,{g0:.2f})',"
                  f"noise=alls=38:allf=t:enable='gte(t,{g0:.2f})',"
                  f"eq=brightness=0.12:contrast=1.3:enable='between(t,{T - 0.12:.2f},{T:.2f})'")
    glitch_in = (f"rgbashift=rh=14:bh=-14:gv=-6:enable='lte(t,{GLITCH_IN:.2f})',"
                 f"noise=alls=38:allf=t:enable='lte(t,{GLITCH_IN:.2f})'")
    v_teaser = (f"[0:v]scale={int(W * 1.5)}:{int(H * 1.5)}:force_original_aspect_ratio=increase,"
                f"zoompan=z='min(1.0+0.0016*on,1.16)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps:.3f},"
                f"eq=brightness=-0.2:contrast=1.1:saturation=0.9,vignette=PI/4,format=yuv420p[a0];"
                f"[a0][1:v]overlay=0:0:format=auto,{glitch_out},setsar=1,format=yuv420p[vt]")
    v_body = (f"[3:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
              f"fps={fps:.3f},format=yuv420p,{glitch_in}[vb]")
    a_teaser = (f"[2:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{T},asetpts=PTS-STARTPTS,"
                f"afade=t=in:d=0.25,afade=t=out:st={T - 0.5:.2f}:d=0.5,volume=0.85[at]") if has_audio else \
        f"anullsrc=r=48000:cl=stereo,atrim=duration={T}[at]"
    a_body = ("[3:a]aresample=48000,aformat=channel_layouts=stereo[ab]" if body_has_audio else
              f"anullsrc=r=48000:cl=stereo,atrim=duration={media_duration(body):.3f}[ab]")
    graph = ";".join([v_teaser, v_body, a_teaser, a_body, "[vt][at][vb][ab]concat=n=2:v=1:a=1[v][a]"])
    enc = h264_encoder()
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y",
           "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{T:.2f}", "-i", still,
           "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{T:.2f}", "-i", overlay,
           "-ss", f"{max(0.0, audio_at):.3f}", "-t", f"{T:.2f}", "-i", audio_src,
           "-i", body,
           "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", enc, *ENCODER_ARGS[enc],
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Adding the teaser failed: {r.stderr.strip()[-300:]}")
    if not os.path.exists(dst):
        raise RuntimeError("Adding the teaser failed: no file was written.")
