"""
Audio-only ingest: the one thing every analysis needs.

For links, only the audio track is fetched (never the video): plain-HTTP
streams (YouTube) with parallel byte ranges, which beats YouTube's throttling
of long reads; segmented streams (Twitch / Kick HLS) with yt-dlp's parallel
fragment download. The file is cached per source, so a second run with other
settings doesn't fetch it again. Local files are read in place.

Loudness is measured with parallel ffmpeg decodes of 5-minute chunks at
8 kHz mono, so memory stays bounded for multi-hour inputs.
"""

from __future__ import annotations

import glob
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from recaply.cache import StageCache
from recaply.ingest.sources import Source, processed_info
from recaply.log import get_logger

log = get_logger("ingest.audio")

AUDIO_FORMAT = "ba[acodec^=mp4a]/ba/b[height<=360]/wa/b"
RMS_RATE = 8000          # Hz for loudness analysis
CHUNK_SEC = 300


def ensure_audio(src: Source, cache: StageCache, progress: Callable[[float], None] | None = None) -> str:
    """Path of a local file holding the source's audio (downloaded once per source)."""
    if src.kind == "file":
        return src.location
    done = [p for p in glob.glob(os.path.join(glob.escape(cache.dir), "audio.*"))
            if not p.endswith((".part", ".ytdl", ".tmp"))]
    if done:
        return done[0]
    info = processed_info(src, AUDIO_FORMAT)
    fmt = (info.get("requested_formats") or [info])[0]
    url, headers = fmt["url"], fmt.get("http_headers") or info.get("http_headers") or {}
    ext = fmt.get("ext") or "m4a"
    out = os.path.join(cache.dir, f"audio.{ext}")
    proto = (fmt.get("protocol") or "").lower()
    if proto in ("https", "http") and ".m3u8" not in url:
        from pipeline import fast_fetch
        tmp = out + ".part"
        fast_fetch(url, headers, tmp, progress=progress)
        os.replace(tmp, out)
    else:
        _ytdlp_download(src, cache.dir, progress)
        found = [p for p in glob.glob(os.path.join(glob.escape(cache.dir), "audio.*"))
                 if not p.endswith((".part", ".ytdl", ".tmp"))]
        if not found:
            raise RuntimeError("The audio download produced no file.")
        out = found[0]
    log.info("audio fetched", source=src.key, mb=round(os.path.getsize(out) / 1e6, 1))
    return out


def _ytdlp_download(src: Source, folder: str, progress):
    import copy

    import yt_dlp
    from pipeline import base_opts

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))
            elif d.get("fragment_count"):
                progress(min(1.0, (d.get("fragment_index") or 0) / d["fragment_count"]))

    opts = base_opts(**{
        "format": AUDIO_FORMAT, "outtmpl": os.path.join(folder, "audio.%(ext)s"),
        "concurrent_fragment_downloads": 8, "fragment_retries": 20, "retries": 10,
        "skip_unavailable_fragments": True, "progress_hooks": [hook],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.process_ie_result(copy.deepcopy(src.raw), download=True)


def media_duration(path: str) -> float:
    from pipeline import probe_media
    return float(probe_media(path)[2] or 0)


def decode_pcm(path: str, start: float, dur: float, rate: int) -> np.ndarray:
    """Mono int16 samples of [start, start+dur) as float32."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)


def loudness(path: str, duration: float, workers: int = 8,
             progress: Callable[[float], None] | None = None) -> np.ndarray:
    """Per-second RMS loudness of the whole file (float32, one value per second)."""
    total = int(np.ceil(duration)) if duration > 0 else int(np.ceil(media_duration(path)))
    if total <= 0:
        return np.zeros(0, np.float32)
    starts = list(range(0, total, CHUNK_SEC))
    parts: list = [None] * len(starts)
    lock = threading.Lock()
    done = [0]

    def job(i):
        want = min(CHUNK_SEC, total - starts[i])
        s = decode_pcm(path, starts[i], want, RMS_RATE)
        n = len(s) // RMS_RATE
        rms = np.sqrt(np.mean(s[: n * RMS_RATE].reshape(n, RMS_RATE) ** 2, axis=1) + 1e-9) if n else np.zeros(0)
        parts[i] = np.concatenate([rms, np.zeros(max(0, want - len(rms)))])[:want].astype(np.float32)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0] / len(starts))

    with ThreadPoolExecutor(max(1, min(workers, len(starts)))) as ex:
        list(ex.map(job, range(len(starts))))
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)
