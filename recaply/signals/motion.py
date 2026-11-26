"""
Motion / scene-change intensity: how much the picture changes each second.

ffmpeg decodes the video small (160 px wide) and its `scene` score (the mean
frame-to-frame difference, 0..1) is read for the sampled frames, in parallel
5-minute chunks. Streams usually have a keyframe every 2-3 s; then only the
keyframes are decoded (about 12x faster than decoding everything, with the
score covering the change since the last keyframe). Videos with sparse
keyframes are sampled at 2 frames per second instead. Local files are read in place;
for links the smallest stream of 360p or less is read, never the whole
video at full quality. A fast fight, a car chase or a camera whip scores
high; a static facecam or a menu scores low.

The per-second score compares the motion with the local baseline (a stream
that is always busy isn't "high motion"), plus a floor for hard scene cuts.
`raw()` (the mean / max scene score per second) is cached once and also
used by content-type detection.
"""

from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from recaply.signals.base import Signal, SignalContext, register, rolling_median, smooth

SAMPLE_FPS = 2
WIDTH = 160
CHUNK_SEC = 300
BASELINE_SEC = 180
RATIO_FROM, RATIO_FULL = 1.5, 5.5        # x the baseline motion mapped to 0..1
KEYFRAME_MAX_GAP = 3.5                   # keyframe-only sampling when keyframes come at least this often (s)
HOLD_SEC = 6                             # a keyframe's score is held over the seconds until the next one
CUT_LEVEL = 0.35                         # a scene score above this is a hard cut
CUT_SCORE = 0.5
MIN_BASELINE = 0.003
PROXY_FORMAT = "bv*[height<=360]/b[height<=360]/wv*/w"
PROXY_SORT = ["+res", "vcodec:h264"]
VERSION = 2


def proxy_inputs(src) -> list[tuple[str, dict]]:
    """[(location, headers)] of a small video-only stream (or the file itself)."""
    if src.kind == "file":
        return [(src.location, {})]
    import copy

    import yt_dlp
    from pipeline import base_opts
    with yt_dlp.YoutubeDL(base_opts(skip_download=True, format=PROXY_FORMAT, format_sort=PROXY_SORT)) as ydl:
        info = ydl.process_ie_result(copy.deepcopy(src.raw), download=False)
    f = (info.get("requested_formats") or [info])[0]
    return [(f["url"], f.get("http_headers") or info.get("http_headers") or {})]


def parse_scene_scores(text: str) -> list[tuple[float, float]]:
    """[(time, score)] from ffmpeg's `metadata=print` output."""
    out, t = [], None
    for line in text.splitlines():
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:", 1)[1].split()[0])
            except (ValueError, IndexError):
                t = None
        elif line.startswith("lavfi.scene_score=") and t is not None:
            try:
                out.append((t, float(line.split("=", 1)[1])))
            except ValueError:
                pass
            t = None
    return out


def scores_to_seconds(samples: list[tuple[float, float]], seconds: int, hold: int = 0) -> np.ndarray:
    """
    (seconds, 2) array [mean, max] of the scene scores falling in each second.
    With `hold`, a second without a sample repeats the last mean for up to
    `hold` seconds (keyframe-only sampling has gaps); a hard cut isn't repeated.
    """
    out = np.zeros((seconds, 2), np.float32)
    count = np.zeros(seconds, np.int32)
    for t, s in samples:
        i = int(t)
        if 0 <= i < seconds:
            out[i, 0] += s
            out[i, 1] = max(out[i, 1], s)
            count[i] += 1
    nz = count > 0
    out[nz, 0] /= count[nz]
    if hold:
        last, age = 0.0, hold + 1
        for i in range(seconds):
            if nz[i]:
                last, age = out[i, 0], 0
            elif age < hold:
                out[i, 0] = last
                age += 1
            else:
                age = hold + 1
    return out


def keyframe_gap(inputs: list[tuple[str, dict]], probe_sec: int = 40) -> float:
    """Median seconds between keyframes in the first `probe_sec` of the video (inf when unknown)."""
    loc, headers = inputs[0]
    cmd = ["ffprobe", "-v", "error"]
    if loc.startswith("http") and headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += ["-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
            "-of", "csv=p=0", "-read_intervals", f"%+{probe_sec}", loc]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        t = sorted(float(x.strip(",")) for x in out.split() if x.strip(",").replace(".", "", 1).isdigit())
    except (OSError, ValueError, subprocess.SubprocessError):
        return float("inf")
    return float(np.median(np.diff(t))) if len(t) >= 3 else float("inf")


def _chunk(inputs: list[tuple[str, dict]], start: int, want: int, keyframes: bool = False) -> np.ndarray:
    loc, headers = inputs[0]
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if loc.startswith("http"):
        if headers:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        from pipeline import http_input_args
        cmd += http_input_args()
    if keyframes:
        cmd += ["-skip_frame", "nokey"]
    sample = "" if keyframes else f"fps={SAMPLE_FPS},"
    cmd += ["-ss", str(start), "-t", str(want), "-i", loc, "-an", "-sn",
            "-vf", f"{sample}scale={WIDTH}:-2,select='gte(scene,0)',"
                   "metadata=mode=print:key=lavfi.scene_score:file=-",
            "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError(f"Reading the video for motion failed: {r.stderr.strip()[-200:]}")
    return scores_to_seconds(parse_scene_scores(r.stdout), want, hold=HOLD_SEC if keyframes else 0)


def measure(inputs: list[tuple[str, dict]], duration: float, workers: int = 8,
            progress: Callable[[float], None] | None = None) -> np.ndarray:
    """Raw motion per second, shape (seconds, 2): [mean scene score, max scene score]."""
    total = int(np.ceil(duration))
    if total <= 0:
        return np.zeros((0, 2), np.float32)
    starts = list(range(0, total, CHUNK_SEC))
    parts: list = [None] * len(starts)
    lock = threading.Lock()
    done = [0]
    errors: list[str] = []
    keyframes = keyframe_gap(inputs) <= KEYFRAME_MAX_GAP

    def job(i):
        want = min(CHUNK_SEC, total - starts[i])
        try:
            parts[i] = _chunk(inputs, starts[i], want, keyframes)
        except Exception as e:  # noqa: BLE001 — a chunk that can't be read counts as "no motion info"
            errors.append(str(e))
            parts[i] = np.zeros((want, 2), np.float32)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0] / len(starts))

    with ThreadPoolExecutor(max(1, min(workers, len(starts)))) as ex:
        list(ex.map(job, range(len(starts))))
    if len(errors) == len(starts):
        raise RuntimeError(errors[0])
    return np.concatenate(parts)


def score(raw: np.ndarray) -> np.ndarray:
    """0..1 per second from raw motion: relative to the local baseline, plus hard cuts."""
    if len(raw) == 0:
        return np.zeros(0, np.float32)
    mean = smooth(raw[:, 0], 2)
    base = np.maximum(rolling_median(mean, BASELINE_SEC), MIN_BASELINE)
    ratio = np.clip((mean / base - RATIO_FROM) / (RATIO_FULL - RATIO_FROM), 0.0, 1.0)
    cuts = np.where(raw[:, 1] >= CUT_LEVEL, CUT_SCORE, 0.0)
    return smooth(np.maximum(ratio, cuts), 2).clip(0, 1).astype(np.float32)


def raw(ctx: SignalContext) -> np.ndarray:
    """The cached raw motion for a SignalContext (computed on first use)."""
    from recaply.settings import settings
    n = ctx.seconds
    arr = ctx.cache.array("motion-raw", {"v": VERSION, "fps": SAMPLE_FPS, "kf": KEYFRAME_MAX_GAP}, lambda: measure(
        proxy_inputs(ctx.source), ctx.duration, workers=settings.workers, progress=ctx.progress))
    if len(arr) < n:
        arr = np.concatenate([arr, np.zeros((n - len(arr), 2), np.float32)])
    return arr[:n]


@register
class Motion(Signal):
    name = "motion"
    version = 2

    def available(self, ctx: SignalContext) -> bool:
        from recaply.settings import settings
        if not ctx.source.has_video:
            return False
        return ctx.source.kind == "file" or settings.motion_for_links

    def compute(self, ctx: SignalContext) -> np.ndarray:
        return score(raw(ctx))
