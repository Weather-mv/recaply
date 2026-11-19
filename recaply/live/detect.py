"""
Near-real-time moment detection for a growing stream.

`LiveFeatures` measures the same per-second audio features as the VOD
pipeline, but only for the seconds that appeared since the last tick.
`LiveScorer` turns the recent past into the three signals that make sense
live (loudness, shouts / laughter, chat) and fuses them with the profile's
weights. `SpikeDetector` watches the fused curve and reports each burst once.

Chat reacts a few seconds late, so the scorer only trusts seconds that are
at least `SETTLE` seconds old; a spike is therefore reported about
SETTLE + the burst's length after it happened, and the clip is cut a little
later still (`post` seconds after the peak).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from recaply.profiles.base import Profile
from recaply.score.fusion import fuse
from recaply.signals import audio_features as af
from recaply.signals import chat_rate, shout_laugh
from recaply.signals.audio_energy import BASELINE_SEC as ENERGY_BASE
from recaply.signals.audio_energy import FULL_SCALE_DB
from recaply.signals.base import rolling_median, smooth

SETTLE = chat_rate.LAG + 3          # seconds before a second's score is final
WINDOW = 1800                       # the scorer looks at the last 30 minutes
LIVE_SIGNALS = ("audio_energy", "shout_laugh", "chat_rate")
CHAT_MIN_RECENT = 10                # chat counts only once this many messages came in the last 5 minutes


class LiveFeatures:
    """The per-second audio feature matrix of the stream so far, grown tick by tick."""

    def __init__(self, decode: Callable[[float, float], np.ndarray]):
        self.decode = decode                    # (start, dur) -> int16-scale float32 samples at 16 kHz
        self.raw = np.zeros((0, len(af.COLS)), np.float32)

    @property
    def seconds(self) -> int:
        return len(self.raw)

    def update(self, live_len: float) -> int:
        """Measure the new whole seconds (the very last one is still being written). Returns how many."""
        target = int(live_len) - 1
        s0 = self.seconds
        if target <= s0:
            return 0
        want = target - s0
        lead = 1 if s0 > 0 else 0
        pcm = self.decode(float(s0 - lead), float(want + lead + 2)) / 32768.0
        tot, high, voice = af.frame_energies(pcm.astype(np.float32))
        feat = af.seconds_from_frames(tot, high, voice, lead * af.FRAMES_PER_SEC, want)
        self.raw = np.concatenate([self.raw, feat])
        return want

    def matrix(self) -> np.ndarray:
        """Features with the speech column filled (needs the whole history for its noise floor)."""
        return af._speech_flags(self.raw.copy()) if len(self.raw) else self.raw


def energy_from_db(db_col: np.ndarray) -> np.ndarray:
    """audio_energy's 0..1 score from the loudness column of the feature matrix."""
    if len(db_col) == 0:
        return np.zeros(0, np.float32)
    db = smooth(db_col, 2)
    excess = np.clip((db - rolling_median(db, ENERGY_BASE)) / FULL_SCALE_DB, 0.0, 1.0)
    return smooth(excess, 3).clip(0, 1).astype(np.float32)


class LiveScorer:
    def __init__(self, profile: Profile):
        self.weights = {k: profile.weights[k] for k in LIVE_SIGNALS if profile.weights.get(k, 0) > 0}

    def curves(self, feat: np.ndarray, chat: list[dict], chat_ok: bool) -> tuple[int, dict[str, np.ndarray]]:
        """(offset of the first second considered, signal curves over [offset, offset+len))."""
        n = len(feat)
        off = max(0, n - WINDOW)
        f = feat[off:]
        sig = {"audio_energy": energy_from_db(f[:, 0]), "shout_laugh": shout_laugh.score(f)}
        if chat_ok:
            msgs = [{**m, "t": m["t"] - off} for m in chat if m["t"] >= off]
            sig["chat_rate"] = chat_rate.score(msgs, len(f))
        return off, {k: v for k, v in sig.items() if k in self.weights}

    def fused(self, feat: np.ndarray, chat: list[dict], chat_ok: bool) -> tuple[int, np.ndarray, dict]:
        off, sig = self.curves(feat, chat, chat_ok)
        n = len(feat) - off
        return off, fuse(sig, self.weights, n, dead=None, edges=False), sig


def chat_is_live(chat: list[dict], now: float) -> bool:
    """True when enough chat arrived recently for the chat signal to mean something."""
    return sum(1 for m in chat[-400:] if m["t"] >= now - 300) >= CHAT_MIN_RECENT


@dataclass
class Spike:
    t: int            # second of the peak (stream time)
    score: float
    start: int
    end: int


class SpikeDetector:
    """
    Reports each burst of the fused score once: when it has risen above
    `threshold` and then fallen back (or has lasted `max_run` seconds), at
    the burst's peak. Bursts inside `cooldown` of the last report, and
    anything before `warmup`, are skipped.
    """

    def __init__(self, threshold: float = 0.5, cooldown: float = 60.0, warmup: float = 120.0,
                 release: float = 0.7, max_run: int = 25):
        self.threshold, self.cooldown, self.warmup = threshold, cooldown, warmup
        self.release, self.max_run = release, max_run
        self.cursor = 0
        self.run: list | None = None             # [start, peak_t, peak_v]
        self.last = -1e9

    def update(self, values: np.ndarray, offset: int, horizon: int) -> list[Spike]:
        out: list[Spike] = []
        end = min(int(horizon), offset + len(values))
        for t in range(max(self.cursor, offset), end):
            x = float(values[t - offset])
            if self.run is None:
                if x >= self.threshold and t >= self.warmup and t - self.last >= self.cooldown:
                    self.run = [t, t, x]
                continue
            if x > self.run[2]:
                self.run[1], self.run[2] = t, x
            if x < self.threshold * self.release or t - self.run[0] >= self.max_run:
                out.append(Spike(self.run[1], round(self.run[2], 3), self.run[0], t))
                self.last = self.run[1]
                self.run = None
        self.cursor = max(self.cursor, end)
        return out

    def flush(self, last_second: int | None = None) -> list[Spike]:
        """Report a burst still open (the stream ended during it)."""
        if self.run is None:
            return []
        s = Spike(self.run[1], round(self.run[2], 3), self.run[0], last_second if last_second is not None else self.run[0])
        self.last = s.t
        self.run = None
        return [s]


def signal_breakdown(sig: dict[str, np.ndarray], offset: int, t: int) -> dict[str, float]:
    """Each signal's score around second t (the max over t-2..t+2), for the event record."""
    i = t - offset
    out = {}
    for k, v in sig.items():
        if len(v):
            out[k] = round(float(v[max(0, i - 2): i + 3].max()), 2) if i >= 0 else 0.0
    return out
