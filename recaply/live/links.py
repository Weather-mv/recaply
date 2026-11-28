"""
Which links Live mode should watch.

A channel link (twitch.tv/name, kick.com/name, youtube.com/@name/live) is always watched: the channel may go
live at any time. A link to one YouTube video (youtube.com/live/ID, watch?v=ID, youtu.be/ID) is only a live
stream while that broadcast is on air (or scheduled): once it has ended it is an ordinary video, and recording
it live would wait forever.

Only youtube.com/live/<id> links are checked with YouTube when deciding what to watch (that is the shape
YouTube gives broadcasts); watch?v= and youtu.be links are clipped as videos without a lookup. Answers
are cached per video ID: an ended broadcast stays ended, so that is kept for a day, while "live" and
"upcoming" are re-checked after five minutes.
"""
from __future__ import annotations

import re
import threading
import time

_CHANNEL = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)/live/?(\?.*)?$|"
    r"twitch\.tv/[A-Za-z0-9_]{3,25}/?(\?.*)?$|kick\.com/[A-Za-z0-9_-]{3,25}/?(\?.*)?$)", re.I)
_VIDEO = re.compile(r"^https?://(www\.|m\.)?(youtube\.com/(live/|shorts/|watch\?)|youtu\.be/)", re.I)
_BROADCAST = re.compile(r"^https?://(www\.|m\.)?youtube\.com/live/", re.I)
_ID = re.compile(r"(?:youtube\.com/(?:live/|shorts/)|youtu\.be/|[?&]v=)([\w-]{6,})", re.I)

TTL = {"live": 300.0, "upcoming": 300.0, "ended": 86400.0, "video": 86400.0}
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()

ENDED_NOTE = ("This stream has ended and is now a regular video. Paste the link on the Projects page "
              "to clip it as a video.")


def is_channel_link(url: str) -> bool:
    return bool(_CHANNEL.match(url or ""))


def is_video_link(url: str) -> bool:
    """A link to one YouTube video or broadcast (not a channel)."""
    return bool(_VIDEO.match(url or ""))


def video_id(url: str) -> str | None:
    m = _ID.search(url or "")
    return m.group(1) if m else None


def video_status(url: str) -> str | None:
    """'live', 'upcoming', 'ended' or 'video' for a single YouTube video link; None when unknown.
    Cached per video ID (see TTL); an unknown answer is not cached."""
    vid = video_id(url) or url
    with _cache_lock:
        hit = _cache.get(vid)
    if hit and time.monotonic() < hit[1]:
        return hit[0]
    status = _lookup(url)
    if status is not None:
        with _cache_lock:
            _cache[vid] = (status, time.monotonic() + TTL[status])
    return status


def forget(url: str | None = None) -> None:
    """Drop the cached answer for `url` (or all of them)."""
    with _cache_lock:
        if url is None:
            _cache.clear()
        else:
            _cache.pop(video_id(url) or url, None)


def _lookup(url: str) -> str | None:
    try:
        from recaply.ingest.sources import resolve
        raw = resolve(url).raw or {}
    except Exception:  # noqa: BLE001 — unknown: let the caller decide
        return None
    status = raw.get("live_status")
    if status == "is_live" or (status is None and raw.get("is_live")):
        return "live"
    if status == "is_upcoming":
        return "upcoming"
    if status in ("was_live", "post_live"):
        return "ended"
    return "video"


def should_watch(url: str) -> bool:
    """True when Live mode should record this link."""
    if is_channel_link(url):
        return True
    if _BROADCAST.match(url or ""):
        return video_status(url) in ("live", "upcoming")
    return False                     # watch?v= / youtu.be / shorts: clipped as a video, no lookup


def ended(url: str) -> bool:
    """True only when `url` is one YouTube video that is known not to be live any more (or never was)."""
    return is_video_link(url) and video_status(url) in ("ended", "video")
