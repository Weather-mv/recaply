"""
Thumbnails from the clip's own frames.

`frames()` pulls a few candidate stills out of the finished short. `render()`
turns one still into a thumbnail of the network's size. When the still's shape
doesn't match (a vertical short into a 16:9 YouTube thumbnail), the frame
is laid over a blurred, darker copy of itself. Bold title text, the Brand
Kit's accent colour, font and logo go on top. Everything is written to
`<job dir>/thumbs/` so the usual file route serves it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

from recaply.log import get_logger

log = get_logger("social.thumbnail")
SIZES = {"youtube": (1280, 720), "vertical": (1080, 1920), "linkedin": (1200, 627), "square": (1080, 1080)}
TEMPLATES = ("bold", "boxed", "clean")


def thumbs_dir(job_id: str) -> str:
    from recaply.jobs.runner import job_dir
    d = os.path.join(job_dir(job_id), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def _duration(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                             capture_output=True, timeout=30).stdout
        return float(json.loads(out)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return 0.0


def _grab(video: str, at: float, dst: str) -> bool:
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", video,
                        "-frames:v", "1", "-q:v", "2", dst], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.isfile(dst) and os.path.getsize(dst) > 0


def frames(job_id: str, cid: int, video: str, n: int = 4, hook_at: float | None = None) -> list[dict]:
    """Up to `n` candidate stills: [{"at": seconds, "name": "thumbs/frame_..jpg"}]. The hook frame comes first."""
    dur = _duration(video)
    if dur <= 0:
        return []
    times = [round(dur * f, 2) for f in (0.12, 0.35, 0.58, 0.8)][:n]
    if hook_at is not None and 0 <= hook_at < dur:
        times = [round(hook_at, 2)] + [t for t in times if abs(t - hook_at) > 1.0][: n - 1]
    out = []
    d = thumbs_dir(job_id)
    for t in times:
        name = f"frame_{cid:02d}_{int(t * 100):06d}.jpg"
        path = os.path.join(d, name)
        if os.path.isfile(path) or _grab(video, t, path):
            out.append({"at": t, "name": "thumbs/" + name})
    return out


def _hex(c: str, default=(8, 101, 255)) -> tuple[int, int, int]:
    c = (c or "").lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else default


def _cover(img, W, H):
    from PIL import Image
    s = max(W / img.width, H / img.height)
    im = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def _canvas(src, W, H):
    """The frame filling W x H: cropped when the shapes are close, otherwise over a blurred copy of itself."""
    from PIL import Image, ImageEnhance, ImageFilter
    if abs((src.width / src.height) - (W / H)) < 0.25:
        return _cover(src, W, H)
    bg = _cover(src, W, H).filter(ImageFilter.GaussianBlur(radius=max(W, H) // 40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    s = min(W / src.width, H / src.height)
    fg = src.resize((round(src.width * s), round(src.height * s)), Image.LANCZOS)
    if W > H:                                     # a vertical frame on a wide card sits to the right, text on the left
        bg.paste(fg, (W - fg.width - round(W * 0.06), (H - fg.height) // 2))
    else:
        bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


def render(job_id: str, cid: int, frame_name: str, text: str, size: str = "youtube", template: str = "bold",
           owner=None) -> str:
    """Draw the thumbnail; returns its name inside the job folder ("thumbs/thumb_....jpg")."""
    from PIL import Image, ImageDraw, ImageFont

    import editor
    from recaply.publish.queue import resolve_image
    from recaply.render import brand
    W, H = SIZES.get(size, SIZES["youtube"])
    template = template if template in TEMPLATES else "bold"
    text = " ".join((text or "").split())[:80]
    kit = brand.load(owner)
    src = Image.open(resolve_image(job_id, frame_name)).convert("RGB")
    img = _canvas(src, W, H).convert("RGBA")
    accent = _hex(kit.accent)
    text_rgb = _hex(kit.text_color, (255, 255, 255))
    if text and template != "clean":
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        wide = W > H and abs((src.width / src.height) - (W / H)) >= 0.25
        box_w = W * (0.5 if wide else 0.86)
        size_px = round(min(W, H) * (0.13 if W > H else 0.085))
        fpath = brand.font_path(owner, kit)
        fnt = ImageFont.truetype(fpath, size_px) if fpath else editor._font(size_px)
        lines = editor.wrap_text(text.upper(), box_w, fnt.getlength)
        while len(lines) > 3 and size_px > 24:                # shrink until it fits in three lines
            size_px = round(size_px * 0.88)
            fnt = ImageFont.truetype(fpath, size_px) if fpath else editor._font(size_px)
            lines = editor.wrap_text(text.upper(), box_w, fnt.getlength)
        lines = lines[:3]
        lh = size_px * 1.12
        block = lh * len(lines)
        x0 = W * 0.06
        top = (H - block) / 2 if wide else H * (0.62 if H > W else 0.58) - block / 2
        if not wide:                                          # a dark fade under the text keeps it readable
            fade = Image.new("L", (1, H))
            for yy in range(H):
                fade.putpixel((0, yy), int(190 * max(0.0, (yy / H - 0.35) / 0.65)))
            layer.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), fade.resize((W, H)))
        for i, line in enumerate(lines):
            lw = fnt.getlength(line)
            x = x0 if wide else (W - lw) / 2
            y = top + i * lh
            if template == "boxed":
                pad = size_px * 0.18
                d.rectangle([x - pad, y - pad * 0.4, x + lw + pad, y + size_px * 1.05 + pad * 0.4], fill=(*accent, 255))
                d.text((x, y), line, font=fnt, fill=(*text_rgb, 255))
            else:
                d.text((x, y), line, font=fnt, fill=(*text_rgb, 255), stroke_width=max(3, size_px // 12),
                       stroke_fill=(0, 0, 0, 255))
        if template == "bold":
            bx = x0 if wide else W * 0.38
            d.rectangle([bx, top - size_px * 0.42, bx + W * (0.16 if wide else 0.24), top - size_px * 0.42 + max(6, size_px // 9)],
                        fill=(*accent, 255))
        img = Image.alpha_composite(img, layer)
    logo = os.path.join(brand.brand_dir(owner), "logo.png")
    if kit.logo and os.path.isfile(logo):
        try:
            lg = Image.open(logo).convert("RGBA")
            lw = round(W * 0.12)
            lg = lg.resize((lw, max(1, round(lg.height * lw / lg.width))), Image.LANCZOS)
            img.alpha_composite(lg, (W - lw - round(W * 0.03), round(H * 0.03)))
        except OSError:
            pass
    key = hashlib.sha1(f"{frame_name}|{text}|{size}|{template}|{kit.accent}|{kit.font}".encode()).hexdigest()[:10]
    name = f"thumb_{cid:02d}_{size}_{key}.jpg"
    img.convert("RGB").save(os.path.join(thumbs_dir(job_id), name), "JPEG", quality=90)
    return "thumbs/" + name


def save_upload(job_id: str, cid: int, data: bytes) -> str:
    """The user's own image, checked and re-saved as a JPEG (so nothing but a picture lands in the folder)."""
    import io

    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 — anything Pillow can't open isn't a picture
        raise ValueError("That file isn't a picture Clipforge can read (use JPG or PNG).") from None
    im = im.convert("RGB")
    if max(im.size) > 2560:
        im.thumbnail((2560, 2560))
    name = f"upload_{cid:02d}_{hashlib.sha1(data).hexdigest()[:10]}.jpg"
    im.save(os.path.join(thumbs_dir(job_id), name), "JPEG", quality=92)
    return "thumbs/" + name
