"""
Audio energy: how much louder each second is than the surrounding few
minutes. Reactions, shouting, crowd noise and caster hype all show up here.

Loudness is compared in decibels with a rolling median (so a quiet stream
and a loud stream score alike, and a long loud section doesn't drown out
the rest), then mapped to 0..1: 0 at the local baseline, 1 at +15 dB.
Also exposes `dead_air()`, used by the fusion to penalise silence.
"""

from __future__ import annotations

import numpy as np

from recaply.signals.base import Signal, SignalContext, register, rolling_median, smooth

FULL_SCALE_DB = 15.0        # this much over the local baseline scores 1.0
BASELINE_SEC = 180


def to_db(rms: np.ndarray) -> np.ndarray:
    return (20.0 * np.log10(np.maximum(rms, 1e-3))).astype(np.float32)


def energy_score(rms: np.ndarray) -> np.ndarray:
    """0..1 per second from per-second RMS."""
    if len(rms) == 0:
        return np.zeros(0, np.float32)
    db = smooth(to_db(rms), 2)
    base = rolling_median(db, BASELINE_SEC)
    excess = np.clip((db - base) / FULL_SCALE_DB, 0.0, 1.0)
    return smooth(excess, 3).clip(0, 1).astype(np.float32)


def dead_air(rms: np.ndarray, min_sec: int = 4) -> np.ndarray:
    """True for seconds inside a stretch of near-silence lasting >= min_sec."""
    if len(rms) == 0:
        return np.zeros(0, bool)
    db = to_db(rms)
    floor = np.percentile(db, 5)
    quiet = db < max(floor + 6.0, np.median(db) - 25.0)
    out = np.zeros_like(quiet)
    run = 0
    for i, q in enumerate(quiet):
        run = run + 1 if q else 0
        if run >= min_sec:
            out[i - run + 1: i + 1] = True
    return out


@register
class AudioEnergy(Signal):
    name = "audio_energy"
    version = 2                 # 2: full scale +15 dB

    def compute(self, ctx: SignalContext) -> np.ndarray:
        return energy_score(ctx.rms)
