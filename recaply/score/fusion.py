"""
Signal fusion: one 0..1 "interest" curve per second from all available
signals, weighted by the profile.

- Weights of signals that aren't available are dropped and the rest are
  re-normalised (no chat replay -> audio and motion count for more).
- Seconds where several independent signals are high at once get a boost:
  agreement between, say, chat and audio is much stronger evidence than
  either alone.
- Dead air and the intro/outro of a video are penalised.
"""

from __future__ import annotations

import numpy as np

OVERLAP_LEVEL = 0.5          # a signal "fires" above this
OVERLAP_BOOST = 0.25         # +25% per extra signal firing at the same time
DEAD_AIR_FACTOR = 0.3
EDGE_FACTOR = 0.5


def edge_mask(n: int, intro: float = 90.0, outro: float = 120.0, share: float = 0.03) -> np.ndarray:
    """True for seconds in the intro/outro: min(intro, share*n) at the start, min(outro, share*n) at the end."""
    m = np.zeros(n, bool)
    if n == 0:
        return m
    a = int(min(intro, share * n))
    b = int(min(outro, share * n))
    m[:a] = True
    if b:
        m[n - b:] = True
    return m


def fuse(signals: dict[str, np.ndarray], weights: dict[str, float], n: int,
         dead: np.ndarray | None = None, edges: bool = True) -> np.ndarray:
    """
    Weighted fusion of per-second signals into one 0..1 curve of length n.
    `signals` maps names to arrays (any length; padded/truncated to n);
    names without a positive weight are ignored.
    """
    used = {k: v for k, v in signals.items() if weights.get(k, 0) > 0 and v is not None and len(v)}
    if n <= 0:
        return np.zeros(0, np.float32)
    if not used:
        return np.zeros(n, np.float32)
    total_w = sum(weights[k] for k in used)
    stack = []
    for k, v in used.items():
        v = np.asarray(v, np.float32)
        v = v[:n] if len(v) >= n else np.concatenate([v, np.zeros(n - len(v), np.float32)])
        stack.append((weights[k] / total_w, np.clip(v, 0, 1)))
    fused = np.zeros(n, np.float32)
    firing = np.zeros(n, np.int32)
    for w, v in stack:
        fused += w * v
        firing += (v >= OVERLAP_LEVEL).astype(np.int32)
    if len(stack) > 1:
        fused *= 1.0 + OVERLAP_BOOST * np.maximum(firing - 1, 0)
    if dead is not None and len(dead):
        d = np.asarray(dead[:n], bool)
        fused[: len(d)][d] *= DEAD_AIR_FACTOR
    if edges:
        fused[edge_mask(n)] *= EDGE_FACTOR
    return np.clip(fused, 0, 1).astype(np.float32)
