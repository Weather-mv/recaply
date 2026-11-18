"""
common.py — helpers and in-memory state shared by the recap, reel and editor
routes: formatting, readable errors, link checks, the MP4 fixer, and the work
slots that keep a busy server queueing instead of thrashing.
"""

import os
import re
import threading
from urllib.parse import urlparse

from config import ALLOWED_HOSTS, env_int
from pipeline import MP4_AUDIO_CODECS, MP4_VIDEO_CODECS, convert_to_mp4, cookie_copy, probe_media
from slots import PrioritySlots

JOB_ID = re.compile(r"^[0-9a-f]{10}$")

# In-memory recap store (also saved to clips/<job>/job.json).
JOBS = {}
JOBS_LOCK = threading.Lock()

MAX_ANALYSES = max(1, env_int("RECAPLY_MAX_ANALYSES", 2))     # analyses running at once; the rest wait
ANALYSIS_SLOTS = PrioritySlots(MAX_ANALYSES)
# Reel renders and editor exports (each a full video encode).
RENDER_SLOTS = PrioritySlots(max(1, env_int("RECAPLY_REEL_WORKERS", 2)))


def fmt_hms(seconds):
    h, rest = divmod(int(round(seconds)), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_dur(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


YT_ID = re.compile(r"(?:youtu\.be/|[?&]v=|/(?:live|shorts|embed)/)([\w-]{11})")


def thumbnail_url(url):
    """YouTube thumbnail for a VOD link (Twitch VOD thumbnails need an API key)."""
    m = YT_ID.search(url or "")
    return f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else ""


def allowed_url(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    return "*" in ALLOWED_HOSTS or any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


BOT_CHECK_HELP = (
    "YouTube is asking this server to confirm it's not a bot. Export your YouTube "
    "cookies (the \"Get cookies.txt LOCALLY\" browser extension, on youtube.com while "
    "logged in) and save the file as cookies.txt in the Recaply folder (or point "
    "RECAPLY_COOKIES at it), then try again. No restart needed.")


def friendly_error(e):
    """A readable message for errors users can fix themselves."""
    msg = str(e)
    if "confirm you" in msg and "not a bot" in msg:
        try:
            with open(cookie_copy(), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except (OSError, TypeError, ValueError):
            return BOT_CHECK_HELP
        if not re.search(r"\t(SID|__Secure-1PSID|LOGIN_INFO)\t", text):
            return ("YouTube is asking this server to confirm it's not a bot, and your cookies.txt "
                    "isn't logged in (it has no YouTube login cookies). Export it again while "
                    "logged in to youtube.com, replace cookies.txt, then try again. No restart needed.")
        return ("YouTube is asking this server to confirm it's not a bot, and it didn't accept "
                "your cookies.txt: YouTube may have signed that session out. Export fresh cookies "
                "(best from a private window you then close), replace cookies.txt, and try again.")
    return msg


# Clips saved before the MP4 converter existed may be AV1/VP9 + Opus inside
# an .mp4, which many players can't open. Convert those the first time
# they're served. Checked files are remembered by (path, mtime).
_MP4_OK = set()
_MP4_LOCKS = {}
_MP4_LOCKS_GUARD = threading.Lock()


def ensure_mp4(path):
    """True when the file had to be converted."""
    if not path.lower().endswith(".mp4") or not os.path.exists(path):
        return False
    with _MP4_LOCKS_GUARD:
        lock = _MP4_LOCKS.setdefault(path, threading.Lock())
    with lock:
        key = (path, os.path.getmtime(path))
        if key in _MP4_OK:
            return False
        try:
            video, audio, _ = probe_media(path)
        except Exception:  # noqa: BLE001 — unreadable; serve as-is
            return False
        converted = False
        if video not in MP4_VIDEO_CODECS or (audio and audio not in MP4_AUDIO_CODECS):
            src = path + ".orig"
            os.replace(path, src)
            try:
                convert_to_mp4(src, path)
                os.remove(src)
                converted = True
            except Exception:  # noqa: BLE001 — keep the original
                os.replace(src, path)
                return False
        _MP4_OK.add((path, os.path.getmtime(path)))
        return converted


class Offline(str):
    """Why a clip couldn't be charged when the online service is unreachable (not a plan limit)."""


def charge_failed(info):
    """The response for a clip that couldn't be charged: 503 when offline, 402 (offer an upgrade) otherwise."""
    from flask import jsonify
    if isinstance(info, Offline):
        return jsonify({"error": str(info)}), 503
    return jsonify({"error": info, "upgrade": True}), 402
