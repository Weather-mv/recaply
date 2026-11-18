"""
live.py — YouTube live streams, readable from the very start while they're
still going.

yt-dlp can't download a time range of a live stream, but YouTube's
"from start" formats (live_from_start) are a numbered series of fixed-length
pieces (usually 5 seconds): piece N is fetched with `&sq=N` and covers
[N * seg, (N + 1) * seg) seconds of the broadcast, and a HEAD request's
X-Head-Seqnum header says how far the stream has got. Joined end to end, the
pieces form a normal fragmented MP4. So any time range — the 360p picture for
the round scan, 720p for the kill feed names, 1080p for clips — is a set of
parallel piece downloads, exactly like a VOD.

The same works for a stream that has just ended and is still being
processed into a VOD ("post_live").
"""

import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import yt_dlp

from pipeline import base_opts, convert_to_mp4

LIVE_STATES = ("is_live", "post_live")
URL_MAX_AGE = 4 * 3600      # stream URLs expire after ~6h; refresh well before


class NotReadableLive(Exception):
    """A live stream that can't be read from the start (e.g. Twitch, DVR off)."""


def _height_pick(fmts, limit):
    video = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    avc = [f for f in video if str(f.get("vcodec", "")).startswith("avc1")] or video
    fit = [f for f in avc if f["height"] <= limit] or avc
    return max(fit, key=lambda f: (f["height"], f.get("fps") or 0)) if fit else None


class LiveStream:
    """One live (or just-ended) YouTube stream, fetchable by time range."""

    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.refresh()

    # ---------- stream info ----------

    def refresh(self):
        """(Re-)extract the stream: title, state and the from-start format URLs."""
        with yt_dlp.YoutubeDL(base_opts(skip_download=True, live_from_start=True)) as ydl:
            info = ydl.extract_info(self.url, download=False)
        fmts = [f for f in info.get("formats") or [] if f.get("is_from_start") and f.get("url")]
        with self.lock:
            self.title = info.get("title") or "Live stream"
            self.id = info.get("id")
            self.webpage_url = info.get("webpage_url") or self.url
            self.live_status = info.get("live_status")
            self.started_at = info.get("release_timestamp")
            self.fetched_at = time.time()
            if not fmts:
                if self.live_status in LIVE_STATES and "twitch" in (info.get("extractor") or "").lower():
                    raise NotReadableLive(
                        "Twitch live channels can't be read from the start. Use the VOD Twitch "
                        "records while it streams (twitch.tv/videos/…) instead.")
                if self.live_status in LIVE_STATES:
                    raise NotReadableLive(
                        "This live stream can't be read from the start (the channel may have "
                        "turned off rewinding). Paste the link again once the stream has "
                        "ended and YouTube has turned it into a video.")
                return
            self.seg = float(fmts[0].get("target_duration") or 5)
            audio = [f for f in fmts if f.get("vcodec") in (None, "none")]
            audio.sort(key=lambda f: (str(f.get("acodec", "")).startswith("mp4a.40.2"), f.get("abr") or 0))
            self.formats = {
                "scan": _height_pick(fmts, 360),
                "names": _height_pick(fmts, 720),
                "1080": _height_pick(fmts, 1080),
                "720": _height_pick(fmts, 720),
                "best": _height_pick(fmts, 99999),
                "audio": audio[-1] if audio else None,
            }

    @property
    def readable(self):
        """Still readable piece by piece (live or just ended, not yet a VOD)."""
        return self.live_status in LIVE_STATES and bool(getattr(self, "formats", None))

    def _fmt(self, kind):
        if time.time() - self.fetched_at > URL_MAX_AGE:
            self.refresh()
        with self.lock:
            f = self.formats.get(kind)
        if not f:
            raise RuntimeError(f"No {kind} stream in this live broadcast.")
        return f

    def head(self):
        """
        Number of complete pieces available so far, or None if the stream
        can't be reached (ended, or the URL expired: call refresh()).
        """
        f = self._fmt("scan")
        req = urllib.request.Request(f["url"], method="HEAD", headers=f.get("http_headers") or {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                seq = r.headers.get("X-Head-Seqnum")
        except (urllib.error.URLError, OSError):
            return None
        # The head piece itself may still be growing; stop just before it.
        return int(seq) if seq is not None else None

    # ---------- fetching ----------

    def _get(self, kind, sq):
        for attempt in range(6):
            f = self._fmt(kind)
            req = urllib.request.Request(f"{f['url']}&sq={sq}", headers=f.get("http_headers") or {})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                if data:
                    return data
            except urllib.error.HTTPError as e:
                if e.code in (403, 410) and attempt < 5:
                    self.refresh()           # URL expired
                    continue
                if e.code == 404 and attempt >= 2:
                    raise
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(1 + attempt)
        raise RuntimeError(f"Couldn't fetch piece {sq} of the live stream.")

    def fetch(self, kind, first, last, path, conns=8, progress=None):
        """Pieces [first, last) of one format, joined into `path`."""
        seqs = list(range(first, last))
        done = [0]
        lock = threading.Lock()
        results = {}

        def one(sq):
            results[sq] = self._get(kind, sq)
            with lock:
                done[0] += 1
                if progress:
                    progress(done[0] / len(seqs))

        with ThreadPoolExecutor(conns) as ex:
            list(ex.map(one, seqs))
        with open(path, "wb") as fh:
            for sq in seqs:
                fh.write(results[sq])
        return path

    def fetch_av(self, video_kind, first, last, out_path, progress=None, conns=8):
        """
        Video + audio for pieces [first, last) merged into one file whose
        clock starts at 0 (the pieces carry the broadcast's own clock).
        """
        base = f"{out_path}.{uuid.uuid4().hex[:6]}"
        v, a = base + ".v.mp4", base + ".a.m4a"
        try:
            self.fetch(video_kind, first, last, v, conns=conns,
                       progress=(lambda f: progress(0.8 * f)) if progress else None)
            self.fetch("audio", first, last, a, conns=conns,
                       progress=(lambda f: progress(0.8 + 0.2 * f)) if progress else None)
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", v, "-i", a,
                 "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy",
                 "-avoid_negative_ts", "make_zero", "-f", "mp4", out_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"Joining the live pieces failed: {r.stderr[-300:]}")
        finally:
            for p in (v, a):
                if os.path.exists(p):
                    os.remove(p)
        return out_path

    def pieces(self, start, end):
        """Piece range [first, last) covering [start, end] seconds."""
        return int(max(0, start) // self.seg), int(np.ceil(end / self.seg))

    # ---------- clips ----------

    def clip(self, start, end, out_path, quality="1080", on_convert=None):
        """Cut [start, end] seconds of the broadcast into an MP4 at `out_path`."""
        first, last = self.pieces(start, end)
        tmp = out_path + ".src.mp4"
        cut = out_path + ".cut.mkv"
        try:
            self.fetch_av(quality if quality in ("1080", "720", "best") else "1080", first, last, tmp)
            off = start - first * self.seg
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{off:.2f}", "-i", tmp,
                 "-t", f"{end - start:.2f}", "-map", "0", "-c", "copy", cut],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"Cutting the clip failed: {r.stderr[-300:]}")
            return convert_to_mp4(cut, out_path, progress=on_convert)
        finally:
            for p in (tmp, cut):
                if os.path.exists(p):
                    os.remove(p)


class LiveFrameSource:
    """killer_id frame source reading 720p frames from a live stream."""

    FPS = 2

    def __init__(self, stream, tmp_dir):
        self.stream = stream
        self.tmp_dir = tmp_dir

    def frames(self, a, b):
        first, last = self.stream.pieces(a, b)
        base = os.path.join(self.tmp_dir, f"names_{uuid.uuid4().hex[:8]}")
        raw_path, path = base + ".raw.mp4", base + ".mp4"
        try:
            self.stream.fetch("names", first, last, raw_path, conns=4)
            # Rebase the clock to 0 so the seek below is relative to `first`.
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", raw_path, "-map", "0:v:0",
                            "-c", "copy", "-avoid_negative_ts", "make_zero", path],
                           capture_output=True, timeout=120)
            off = a - first * self.stream.seg
            cmd = ["ffmpeg", "-v", "error", "-nostdin", "-skip_loop_filter", "all",
                   "-ss", f"{off:.2f}", "-t", f"{b - a + 0.01:.2f}", "-i", path, "-an",
                   "-vf", f"fps={self.FPS},scale=1280:720", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
            raw = subprocess.run(cmd, capture_output=True, timeout=180).stdout
        finally:
            for p in (raw_path, path):
                if os.path.exists(p):
                    os.remove(p)
        size = 1280 * 720 * 3
        n = len(raw) // size
        frames = np.frombuffer(raw[: n * size], np.uint8).reshape(n, 720, 1280, 3)
        return [(a + i / self.FPS, f) for i, f in enumerate(frames)]
