"""Export presets: output shape, resolution and the platform's length limit."""

from __future__ import annotations

EXPORTS: dict[str, dict] = {
    "shorts": {"label": "YouTube Shorts (9:16)", "format": "9:16", "size": "1080", "max_sec": 180},
    "tiktok": {"label": "TikTok (9:16)", "format": "9:16", "size": "1080", "max_sec": 600},
    "reels": {"label": "Instagram Reels (9:16)", "format": "9:16", "size": "1080", "max_sec": 180},
    "portrait": {"label": "Feed post (4:5)", "format": "4:5", "size": "1080", "max_sec": 600},
    "square": {"label": "Square (1:1)", "format": "1:1", "size": "1080", "max_sec": 600},
    "landscape": {"label": "Landscape (16:9)", "format": "16:9", "size": "1080", "max_sec": None},
    "draft": {"label": "Quick draft (9:16, 720p)", "format": "9:16", "size": "720", "max_sec": None},
}
DEFAULT_EXPORT = "shorts"


def export_preset(key: str | None) -> dict:
    return {"key": key if key in EXPORTS else DEFAULT_EXPORT, **EXPORTS.get(key or "", EXPORTS[DEFAULT_EXPORT])}


def ratio(fmt: str) -> float:
    a, b = fmt.split(":")
    return int(a) / int(b)


def length_note(preset: dict, seconds: float) -> str | None:
    """A warning when the clip is longer than the platform accepts."""
    if preset.get("max_sec") and seconds > preset["max_sec"]:
        return f"{preset['label']} takes up to {preset['max_sec']} s; this short is {seconds:.0f} s."
    return None
