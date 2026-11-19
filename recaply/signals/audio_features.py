"""
Per-second audio features, measured once for the whole file and cached.

One parallel decode (5-minute chunks, 16 kHz mono) gives, for every second:

    db      loudness (dB, mean frame RMS)
    hf      share of the energy above 1.5 kHz (shouts are bright, mumbles are dull)
    mod     share of the envelope's variation in the 3.5-7.5 Hz band: the rhythm of
            laughter ("ha ha ha") and of fast syllables
    depth   how deep that envelope swings (std / mean over 2 s): laughter and
            shouts breathe in and out, steady music and fans don't
    speech  1.0 when the second looks like speech (voice-band energy, a
            swinging envelope, well above the noise floor), else 0.0

These are plain signal-processing features, not a trained classifier, so
the shout / laughter scores built on them are a cheap hint that the fusion
weighs together with chat, motion and the transcript.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from recaply.ingest.audio import decode_pcm, media_duration

RATE = 16000
HOP = 160                     # 10 ms
WIN = 400                     # 25 ms
NFFT = 512
CHUNK_SEC = 300
FRAMES_PER_SEC = RATE // HOP  # 100
COLS = ("db", "hf", "mod", "depth", "speech")
VERSION = 1

_FREQ = np.fft.rfftfreq(NFFT, 1.0 / RATE)
_TOTAL = (_FREQ >= 80) & (_FREQ <= 6000)
_HIGH = (_FREQ >= 1500) & (_FREQ <= 6000)
_VOICE = (_FREQ >= 300) & (_FREQ <= 3400)
_HANN = np.hanning(WIN).astype(np.float32)


def frame_energies(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per 10 ms frame: (total, high-band, voice-band) power of a float32 signal in [-1, 1]."""
    if len(x) < WIN:
        z = np.zeros(0, np.float32)
        return z, z, z
    frames = np.lib.stride_tricks.sliding_window_view(x, WIN)[::HOP] * _HANN
    spec = np.abs(np.fft.rfft(frames, NFFT, axis=1)).astype(np.float32) ** 2
    return spec[:, _TOTAL].sum(1), spec[:, _HIGH].sum(1), spec[:, _VOICE].sum(1)


def seconds_from_frames(total: np.ndarray, high: np.ndarray, voice: np.ndarray, first: int, count: int) -> np.ndarray:
    """
    Features for `count` seconds, the first one starting at frame `first`
    (frames before 0 / after the end are treated as silence). Modulation is
    measured over a 2 s window centred on each second.
    """
    out = np.zeros((count, len(COLS)), np.float32)
    n = len(total)
    env = np.sqrt(total + 1e-12)
    pad = FRAMES_PER_SEC // 2
    env_p = np.concatenate([np.full(pad, env[:1].mean() if n else 0.0, np.float32), env,
                            np.full(2 * FRAMES_PER_SEC, env[-1:].mean() if n else 0.0, np.float32)])
    mod_bins = (np.fft.rfftfreq(2 * FRAMES_PER_SEC, 1.0 / FRAMES_PER_SEC) >= 3.5) & \
               (np.fft.rfftfreq(2 * FRAMES_PER_SEC, 1.0 / FRAMES_PER_SEC) <= 7.5)
    all_bins = np.fft.rfftfreq(2 * FRAMES_PER_SEC, 1.0 / FRAMES_PER_SEC) >= 0.5
    for i in range(count):
        a, b = first + i * FRAMES_PER_SEC, first + (i + 1) * FRAMES_PER_SEC
        if a >= n:
            out[i, 0] = -90.0
            continue
        t, h, v = total[a:b].sum() + 1e-12, high[a:b].sum(), voice[a:b].sum()
        out[i, 0] = 10.0 * np.log10(total[a:b].mean() + 1e-12)
        out[i, 1] = h / t
        w = env_p[a:a + 2 * FRAMES_PER_SEC]                        # centred: starts pad frames before a
        if len(w) == 2 * FRAMES_PER_SEC and w.mean() > 0:
            d = w - w.mean()
            p = np.abs(np.fft.rfft(d * np.hanning(len(d)))) ** 2
            out[i, 2] = p[mod_bins].sum() / (p[all_bins].sum() + 1e-12)
            out[i, 3] = float(w.std() / (w.mean() + 1e-12))
        out[i, 4] = v / t
    return out


def _speech_flags(feat: np.ndarray) -> np.ndarray:
    """Fill the `speech` column: voice-band share, syllable rhythm and level above the noise floor."""
    db = feat[:, 0]
    if len(db) == 0:
        return feat
    floor = np.percentile(db, 10)
    voiced = feat[:, 4] >= 0.3                                     # `speech` col holds the voice-band share until now
    dynamic = feat[:, 3] >= 0.4                                    # speech swings between words; hum and drones don't
    feat[:, 4] = (voiced & dynamic & (db > floor + 10.0)).astype(np.float32)
    return feat


def extract(path: str, duration: float, workers: int = 8,
            progress: Callable[[float], None] | None = None) -> np.ndarray:
    """Feature matrix, shape (seconds, len(COLS)), for the whole file."""
    total_sec = int(np.ceil(duration)) if duration > 0 else int(np.ceil(media_duration(path)))
    if total_sec <= 0:
        return np.zeros((0, len(COLS)), np.float32)
    starts = list(range(0, total_sec, CHUNK_SEC))
    parts: list = [None] * len(starts)
    lock = threading.Lock()
    done = [0]

    def job(i):
        s0 = starts[i]
        want = min(CHUNK_SEC, total_sec - s0)
        lead = 1 if s0 > 0 else 0                                   # one extra second before the chunk, for the window
        pcm = decode_pcm(path, s0 - lead, want + lead + 2, RATE) / 32768.0
        tot, high, voice = frame_energies(pcm.astype(np.float32))
        feat = seconds_from_frames(tot, high, voice, lead * FRAMES_PER_SEC, want)
        parts[i] = feat
        with lock:
            done[0] += 1
            if progress:
                progress(done[0] / len(starts))

    with ThreadPoolExecutor(max(1, min(workers, len(starts)))) as ex:
        list(ex.map(job, range(len(starts))))
    feat = np.concatenate(parts)
    return _speech_flags(feat)


def get(ctx) -> np.ndarray:
    """The cached feature matrix for a SignalContext (computed on first use, shared by all audio signals)."""
    from recaply.settings import settings
    n = ctx.seconds
    arr = ctx.cache.array("audiofeat", {"v": VERSION}, lambda: extract(
        ctx.audio_path, ctx.duration, workers=settings.workers, progress=ctx.progress))
    if len(arr) < n:
        arr = np.concatenate([arr, np.tile(np.array([[-90, 0, 0, 0, 0]], np.float32), (n - len(arr), 1))])
    return arr[:n]
