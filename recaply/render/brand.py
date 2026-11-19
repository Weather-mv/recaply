"""
Brand kit, saved per account ("workspace"): logo / watermark, colours,
caption style, font, intro and outro clips. Applied to every short when
"Use brand kit" is on.

Stored in `<data dir>/_brand/<account>/`: brand.json plus the files
(logo.png, font.ttf, intro.mp4, outro.mp4).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, fields

from recaply.settings import settings

POSITIONS = ("tl", "tr", "bl", "br")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT_CHOICES = {                      # bold fonts offered when found on this PC
    "default": None,
    "montserrat": "C:/Windows/Fonts/Montserrat-Black.ttf",
    "impact": "C:/Windows/Fonts/impact.ttf",
    "segoe": "C:/Windows/Fonts/seguibl.ttf",
    "bahnschrift": "C:/Windows/Fonts/bahnschrift.ttf",
    "verdana": "C:/Windows/Fonts/verdanab.ttf",
    "dejavu": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}
MEDIA_FILES = {"logo": "logo.png", "font": "font.ttf", "intro": "intro.mp4", "outro": "outro.mp4"}


@dataclass
class BrandKit:
    logo: bool = False                   # logo.png present
    logo_pos: str = "tr"
    logo_size: float = 0.16              # share of the canvas width
    logo_opacity: float = 0.9
    accent: str = "#0865ff"              # hook box colour
    text_color: str = "#ffffff"
    highlight_color: str = ""            # "" = the caption style's own
    keyword_color: str = "#ff9f1c"
    caption_style: str = "bold"
    font: str = "default"                # a FONT_CHOICES key or "custom" (font.ttf)
    intro: bool = False
    outro: bool = False

    def clean(self) -> "BrandKit":
        from recaply.render.subtitles import PRESETS
        k = BrandKit(**{f.name: getattr(self, f.name) for f in fields(self)})
        k.logo_pos = k.logo_pos if k.logo_pos in POSITIONS else "tr"
        k.logo_size = min(0.4, max(0.05, float(k.logo_size or 0.16)))
        k.logo_opacity = min(1.0, max(0.1, float(k.logo_opacity or 0.9)))
        for c in ("accent", "text_color", "keyword_color"):
            if not HEX.match(str(getattr(k, c) or "")):
                setattr(k, c, getattr(BrandKit(), c))
        k.highlight_color = k.highlight_color if HEX.match(str(k.highlight_color or "")) else ""
        k.caption_style = k.caption_style if k.caption_style in PRESETS else "bold"
        if k.font not in FONT_CHOICES and k.font != "custom":
            k.font = "default"
        return k

    def colors(self) -> dict:
        return {"text": self.text_color, "highlight": self.highlight_color, "keyword": self.keyword_color}


def brand_dir(owner) -> str:
    key = re.sub(r"[^0-9A-Za-z_-]", "", str(owner if owner is not None else "local"))[:40] or "local"
    d = os.path.join(settings.data_dir, "_brand", key)
    os.makedirs(d, exist_ok=True)
    return d


def load(owner) -> BrandKit:
    path = os.path.join(brand_dir(owner), "brand.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    known = {f.name for f in fields(BrandKit)}
    kit = BrandKit(**{k: v for k, v in raw.items() if k in known}).clean()
    d = brand_dir(owner)
    for key, name in MEDIA_FILES.items():               # the flags follow the files actually there
        if key != "font":
            setattr(kit, key, os.path.exists(os.path.join(d, name)))
    if kit.font == "custom" and not os.path.exists(os.path.join(d, MEDIA_FILES["font"])):
        kit.font = "default"
    return kit


def save(owner, kit: BrandKit) -> BrandKit:
    kit = kit.clean()
    path = os.path.join(brand_dir(owner), "brand.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(asdict(kit), f, indent=1)
    os.replace(path + ".tmp", path)
    return load(owner)


def available_fonts() -> list[dict]:
    return [{"key": k, "label": k.capitalize() if k != "default" else "Default (Arial Bold)"}
            for k, p in FONT_CHOICES.items() if p is None or os.path.exists(p)]


def font_path(owner, kit: BrandKit) -> str | None:
    if kit.font == "custom":
        p = os.path.join(brand_dir(owner), MEDIA_FILES["font"])
        return p if os.path.exists(p) else None
    p = FONT_CHOICES.get(kit.font)
    return p if p and os.path.exists(p) else None


def save_logo(owner, data_path: str) -> None:
    """Store an uploaded image as logo.png (max 1024 px, transparency kept)."""
    from PIL import Image
    with Image.open(data_path) as im:
        im = im.convert("RGBA")
        im.thumbnail((1024, 1024))
        im.save(os.path.join(brand_dir(owner), MEDIA_FILES["logo"]), "PNG")


def save_font(owner, data_path: str) -> None:
    from PIL import ImageFont
    ImageFont.truetype(data_path, 40)               # raises if it isn't a usable font
    shutil.copyfile(data_path, os.path.join(brand_dir(owner), MEDIA_FILES["font"]))


def save_clip(owner, which: str, data_path: str) -> None:
    """Store an intro/outro video (converted to H.264/AAC MP4, max 15 s)."""
    from pipeline import convert_to_mp4, probe_media
    if which not in ("intro", "outro"):
        raise ValueError(which)
    if probe_media(data_path)[2] > 15.5:
        raise ValueError("Intros and outros can be up to 15 seconds.")
    dst = os.path.join(brand_dir(owner), MEDIA_FILES[which])
    convert_to_mp4(data_path, dst)


def remove(owner, which: str) -> None:
    if which in MEDIA_FILES:
        p = os.path.join(brand_dir(owner), MEDIA_FILES[which])
        if os.path.exists(p):
            os.remove(p)


def stage_assets(owner, kit: BrandKit, assets_dir: str) -> dict:
    """Copy the logo / font into an editor assets folder; returns {"logo": name, "font": name} as present."""
    os.makedirs(assets_dir, exist_ok=True)
    out = {}
    d = brand_dir(owner)
    if kit.logo and os.path.exists(os.path.join(d, MEDIA_FILES["logo"])):
        name = uuid.uuid4().hex[:12] + ".png"
        shutil.copyfile(os.path.join(d, MEDIA_FILES["logo"]), os.path.join(assets_dir, name))
        out["logo"] = name
    fp = font_path(owner, kit)
    if fp:
        name = uuid.uuid4().hex[:12] + os.path.splitext(fp)[1].lower()
        shutil.copyfile(fp, os.path.join(assets_dir, name))
        out["font"] = name
    return out


def logo_layer(kit: BrandKit, asset: str, assets_dir: str, W: int, H: int) -> dict:
    from PIL import Image
    with Image.open(os.path.join(assets_dir, asset)) as im:
        aw, ah = im.size
    w = W * kit.logo_size
    h = w * ah / max(1, aw)
    m = W * 0.04
    top = H * 0.05
    x = m if kit.logo_pos in ("tl", "bl") else W - w - m
    y = top if kit.logo_pos in ("tl", "tr") else H - h - H * 0.2      # above the platform UI at the bottom
    return {"id": "logo", "type": "image", "name": "Logo", "asset": asset, "hidden": False, "locked": False,
            "opacity": kit.logo_opacity, "x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2),
            "rot": 0, "start": 0, "end": None, "kf": {},
            "anim_in": {"type": "none", "dur": 0.5}, "anim_out": {"type": "none", "dur": 0.5}}


def add_intro_outro(owner, kit: BrandKit, main: str, dst: str, W: int, H: int, fps: float) -> bool:
    """Join intro + main + outro into dst (each fitted to W x H). False when there's nothing to add."""
    d = brand_dir(owner)
    parts = []
    if kit.intro and os.path.exists(os.path.join(d, MEDIA_FILES["intro"])):
        parts.append(os.path.join(d, MEDIA_FILES["intro"]))
    parts.append(main)
    if kit.outro and os.path.exists(os.path.join(d, MEDIA_FILES["outro"])):
        parts.append(os.path.join(d, MEDIA_FILES["outro"]))
    if len(parts) == 1:
        return False
    from pipeline import ENCODER_ARGS, h264_encoder
    from recaply.ingest.audio import media_duration
    chains, labels = [], []
    for i, p in enumerate(parts):
        chains.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                      f"setsar=1,fps={fps:.3f},format=yuv420p[v{i}]")
        has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                    "stream=index", "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
        if has_audio:
            chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={media_duration(p):.3f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(parts)}:v=1:a=1[v][a]"
    enc = h264_encoder()
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
    for p in parts:
        cmd += ["-i", p]
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", enc, *ENCODER_ARGS[enc],
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Adding the intro/outro failed: {r.stderr.strip()[-300:]}")
    return True
