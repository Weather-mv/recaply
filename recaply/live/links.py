"""
Which links Live mode should watch.

A channel link (twitch.tv/name, kick.com/name, youtube.com/@name/live) is always watched: the channel may go
live at any time. A link to one YouTube video (youtube.com/live/ID, watch?v=ID, youtu.be/ID) is only a live
stream while that broadcast is on air (or scheduled): once it has ended it is an ordinary video, and recording
it live would wait forever.
"""
from __future__ import annotations

import re

_CHANNEL = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)/live/?(\?.*)?$|"
    r"twitch\.tv/[A-Za-z0-9_]{3,25}/?(\?.*)?$|kick\.com/[A-Za-z0-9_-]{3,25}/?(\?.*)?$)", re.I)
_VIDEO = re.compile(r"^https?://(www\.|m\.)?(youtube\.com/(live/|shorts/|watch\?)|youtu\.be/)", re.I)

ENDED_NOTE = ("This stream has ended and is now a regular video. Paste the link on the Projects page "
              "to clip it as a video.")


def is_channel_link(url: str) -> bool:
    return bool(_CHANNEL.match(url or ""))


def is_video_link(url: str) -> bool:
    """A link to one YouTube video or broadcast (not a channel)."""
    return bool(_VIDEO.match(url or ""))


def video_status(url: str) -> str | None:
    """'live', 'upcoming', 'ended' or 'video' for a single YouTube video link; None when unknown."""
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
    if is_video_link(url):
        return video_status(url) in ("live", "upcoming")
    return False


def ended(url: str) -> bool:
    """True only when `url` is one YouTube video that is known not to be live any more (or never was)."""
    return is_video_link(url) and video_status(url) in ("ended", "video")
