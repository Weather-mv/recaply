"""
Shouts and laughter: the streamer (or the room) reacting.

Built on the cached per-second audio features (`audio_features.py`):

- shout: much louder than the local baseline and bright (screams and yells
  carry energy above 1.5 kHz; a loud explosion or bass drop is mostly
  low-frequency, so loudness alone scores much lower),
- laughter: a deep, fast, repeated swing of the loudness envelope (3.5-7.5 Hz,
  "ha ha ha") at a level above the baseline.

The score per second is the stronger of the two. It is a heuristic over
signal features, not a trained detector; the fusion only trusts it together
with the other signals.
"""

from __future__ import annotations

import numpy as np

from recaply.signals import audio_features
from recaply.signals.base import Signal, SignalContext, register, rolling_median, smooth

BASELINE_SEC = 180
BRIGHT_FROM, BRIGHT_FULL = 0.03, 0.12     # share of energy above 1.5 kHz mapped to 0..1
SHOUT_FULL_DB = 12.0            # this far over the baseline loudness scores 1.0 for loudness
LAUGH_MOD_FROM, LAUGH_MOD_FULL = 0.30, 0.65      # modulation share mapped to 0..1
LAUGH_DEPTH_FROM, LAUGH_DEPTH_FULL = 0.45, 0.95  # envelope depth mapped to 0..1


def _ramp(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def shout_score(feat: np.ndarray) -> np.ndarray:
    """0..1 per second: loud and bright compared with the local baseline."""
    db = smooth(feat[:, 0], 2)
    hf = smooth(feat[:, 1], 2)
    excess = _ramp(db - rolling_median(db, BASELINE_SEC), 3.0, SHOUT_FULL_DB)
    # Yells carry energy above 1.5 kHz; a low rumble (explosion, bass drop, engine) has almost none.
    bright = _ramp(hf, BRIGHT_FROM, BRIGHT_FULL)
    return (excess * (0.35 + 0.65 * bright)).astype(np.float32)


def laugh_score(feat: np.ndarray) -> np.ndarray:
    """0..1 per second: a deep 3.5-7.5 Hz swing in the envelope, above the baseline loudness."""
    db = smooth(feat[:, 0], 2)
    level = _ramp(db - rolling_median(db, BASELINE_SEC), 0.0, 6.0)
    rhythm = _ramp(feat[:, 2], LAUGH_MOD_FROM, LAUGH_MOD_FULL)
    depth = _ramp(feat[:, 3], LAUGH_DEPTH_FROM, LAUGH_DEPTH_FULL)
    return (level * rhythm * depth).astype(np.float32)


def score(feat: np.ndarray) -> np.ndarray:
    if len(feat) == 0:
        return np.zeros(0, np.float32)
    return smooth(np.maximum(shout_score(feat), laugh_score(feat)), 3).clip(0, 1).astype(np.float32)


@register
class ShoutLaugh(Signal):
    name = "shout_laugh"
    version = 1

    def compute(self, ctx: SignalContext) -> np.ndarray:
        return score(audio_features.get(ctx))
