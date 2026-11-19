"""
Signal plugins. Each one turns something cheap to measure over the whole
video (loudness, chat rate, motion...) into one score per second in 0..1,
so the fusion can weigh them without knowing what they are.

Add a signal: subclass `Signal`, set `name` (the key profiles use in their
weights), implement `available()` and `compute()`, and decorate the class
with `@register`. The pipeline runs every signal a profile weights that is
available for the input; a signal that fails is logged and skipped, and the
job carries on with the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from recaply.cache import StageCache
from recaply.ingest.sources import Source
from recaply.profiles.base import Profile


@dataclass
class SignalContext:
    """What signal plugins may read. Heavier inputs are loaded lazily by the plugin."""
    source: Source
    profile: Profile
    cache: StageCache
    audio_path: str
    duration: float
    rms: np.ndarray                                 # per-second loudness (always present)
    extras: dict = field(default_factory=dict)      # e.g. chat messages, once ingested
    progress: Callable[[float], None] | None = None

    @property
    def seconds(self) -> int:
        return len(self.rms)


class Signal:
    """Base class: one normalized score per second."""

    name: str = ""
    version: int = 1                    # bump to invalidate cached results

    def available(self, ctx: SignalContext) -> bool:
        return True

    def cache_params(self, ctx: SignalContext) -> dict:
        """Extra inputs the cached result depends on (e.g. which chat was read), part of the cache key."""
        return {}

    def compute(self, ctx: SignalContext) -> np.ndarray:
        raise NotImplementedError


_SIGNALS: dict[str, Signal] = {}


def register(cls: type[Signal]) -> type[Signal]:
    inst = cls()
    if not inst.name:
        raise ValueError(f"{cls.__name__} needs a name")
    _SIGNALS[inst.name] = inst
    return cls


def get_signal(name: str) -> Signal | None:
    return _SIGNALS.get(name)


def all_signals() -> dict[str, Signal]:
    return dict(_SIGNALS)


# ---------- helpers shared by plugins ----------

def smooth(x: np.ndarray, sec: int) -> np.ndarray:
    """Centered moving average over `sec` samples."""
    if sec <= 1 or len(x) == 0:
        return x.astype(np.float32)
    k = np.ones(sec, np.float32) / sec
    return np.convolve(x, k, mode="same").astype(np.float32)


def rolling_median(x: np.ndarray, sec: int) -> np.ndarray:
    """Rolling median with a window of `sec` (edges use a shrunk window)."""
    if len(x) == 0:
        return x
    from scipy.ndimage import median_filter
    return median_filter(x, size=max(1, min(sec, len(x))), mode="nearest")


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    """Pad with zeros / truncate to exactly n samples."""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x), x.dtype)])
