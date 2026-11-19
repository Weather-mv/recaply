"""
Resolve any input into a `Source`: a YouTube / Twitch / Kick link (VOD or
live), an uploaded file, or a local path.

Nothing is downloaded here except the page's metadata. The `key` is what the
stage cache is keyed on: the platform's video id for links, a hash of size +
sampled bytes for files (so a renamed copy still hits the cache).
"""

from __future__ import annotations

import copy
import hashlib
import os
from dataclasses import dataclass, field

from common import allowed_url
from recaply.cache import safe_name

MEDIA_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".flv",
              ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac")


class InputError(ValueError):
    """The input isn't something Clipforge can read (shown to the user as is)."""


@dataclass
class Source:
    kind: str                          # "url" | "file"
    key: str                           # cache key
    title: str
    duration: float
    location: str                      # the URL or the absolute file path
    extractor: str = ""
    video_id: str = ""
    webpage_url: str = ""
    is_live: bool = False
    has_video: bool = True
    meta: dict = field(default_factory=dict)      # title/description/tags/categories/game for detection
    raw: dict | None = field(default=None, repr=False)   # yt-dlp's unprocessed info (reused for clips)

    def public(self) -> dict:
        """JSON-safe summary for job records."""
        return {"kind": self.kind, "key": self.key, "title": self.title, "duration": self.duration,
                "location": self.location if self.kind == "url" else os.path.basename(self.location),
                "extractor": self.extractor, "video_id": self.video_id,
                "webpage_url": self.webpage_url, "is_live": self.is_live}


def looks_like_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://"))


def resolve(text: str, allow_local: bool = True) -> Source:
    """Turn user input (a link or a file path) into a Source."""
    text = (text or "").strip().strip('"')
    if not text:
        raise InputError("Paste a link or choose a file.")
    if looks_like_url(text):
        return resolve_url(text)
    if not allow_local:
        raise InputError("Only links and uploads are accepted here.")
    return resolve_file(text)


# ---------- links ----------

def resolve_url(url: str) -> Source:
    if len(url) > 2000 or any(c in url for c in "\r\n\t\0 "):
        raise InputError("That link doesn't look right.")
    if not allowed_url(url):
        raise InputError("Only YouTube, Twitch and Kick links are supported.")
    import yt_dlp
    from pipeline import base_opts
    with yt_dlp.YoutubeDL(base_opts(skip_download=True)) as ydl:
        raw = ydl.extract_info(url, download=False, process=False)
        for _ in range(3):      # a channel's /live link points at the video that's live now
            if raw.get("_type") not in ("url", "url_transparent") or not raw.get("url"):
                break
            raw = ydl.extract_info(raw["url"], download=False, process=False)
    if raw.get("_type") == "playlist":
        raise InputError("That's a playlist or channel; paste a single video's link.")
    extractor = (raw.get("extractor_key") or raw.get("extractor") or "").lower()
    vid = str(raw.get("id") or "")
    live = raw.get("live_status") in ("is_live", "post_live") or bool(raw.get("is_live"))
    meta = {
        "title": raw.get("title") or "",
        "description": raw.get("description") or "",
        "tags": raw.get("tags") or [],
        "categories": raw.get("categories") or [],
        "game": raw.get("game") or raw.get("category") or "",
        "extractor": extractor,
        "channel": raw.get("channel") or raw.get("uploader") or "",
    }
    return Source(
        kind="url", key=safe_name(f"{extractor or 'web'}-{vid or hashlib.sha1(url.encode()).hexdigest()[:12]}"),
        title=raw.get("title") or "video", duration=float(raw.get("duration") or 0),
        location=url, extractor=extractor, video_id=vid,
        webpage_url=raw.get("webpage_url") or url, is_live=live, meta=meta, raw=raw)


def processed_info(src: Source, fmt: str) -> dict:
    """yt-dlp's info for `src` with format `fmt` chosen (stream URLs, headers)."""
    import yt_dlp
    from pipeline import base_opts
    with yt_dlp.YoutubeDL(base_opts(skip_download=True, format=fmt)) as ydl:
        return ydl.process_ie_result(copy.deepcopy(src.raw), download=False)


def stream_inputs(src: Source, fmt: str) -> list[tuple[str, dict]]:
    """[(url, headers)] for the formats `fmt` picks: one entry (muxed) or two (video, audio)."""
    info = processed_info(src, fmt)
    fmts = info.get("requested_formats") or [info]
    return [(f["url"], f.get("http_headers") or info.get("http_headers") or {}) for f in fmts]


# ---------- files ----------

def file_key(path: str) -> str:
    """Content key of a file: size + 3 MiB sampled from its start, middle and end."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        for pos in (0, max(0, size // 2 - (1 << 19)), max(0, size - (1 << 20))):
            f.seek(pos)
            h.update(f.read(1 << 20))
    return "file-" + h.hexdigest()[:20]


def resolve_file(path: str) -> Source:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise InputError("File not found.")
    if not path.lower().endswith(MEDIA_EXTS):
        raise InputError("That isn't a video or audio file Clipforge can read.")
    from pipeline import probe_media
    try:
        video, audio, duration = probe_media(path)
    except Exception as e:  # noqa: BLE001
        raise InputError("Couldn't read that file (is it a complete video?).") from e
    if audio is None:
        raise InputError("That file has no sound track; Clipforge needs audio to find moments.")
    title = os.path.splitext(os.path.basename(path))[0]
    return Source(kind="file", key=file_key(path), title=title, duration=float(duration or 0),
                  location=path, has_video=video is not None,
                  meta={"title": title.replace("_", " "), "extractor": "file"})
