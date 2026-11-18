"""
Clean cut points. A clip must never start or end mid-word, should start at
the beginning of a sentence and end at the end of one, and must stay within
the profile's length range.

When there's no speech near a boundary (pure gameplay), it snaps to the
quietest second nearby instead (a natural beat between moments).
"""

from __future__ import annotations

import numpy as np

SENTENCE_END = (".", "?", "!", "…")
SENTENCE_GAP = 0.7          # a pause this long also ends a sentence
SEARCH = 6.0                # how far a boundary may move to find a sentence edge
PAD_BEFORE = 0.15
PAD_AFTER = 0.35


def sentences(words: list[dict]) -> list[tuple[float, float]]:
    """(start, end) of each sentence: split at . ? ! and at pauses > SENTENCE_GAP."""
    out: list[tuple[float, float]] = []
    cur_start = None
    for i, w in enumerate(words):
        if cur_start is None:
            cur_start = w["s"]
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = w["w"].rstrip('"\')]').endswith(SENTENCE_END)
        gap = (nxt["s"] - w["e"]) if nxt else 99.0
        if ends or gap > SENTENCE_GAP or nxt is None:
            out.append((cur_start, w["e"]))
            cur_start = None
    return out


def _inside_word(words: list[dict], t: float) -> dict | None:
    for w in words:
        if w["s"] < t < w["e"]:
            return w
    return None


def _quiet_point(energy: np.ndarray | None, t: float, radius: float = 2.0) -> float:
    if energy is None or len(energy) == 0:
        return t
    a, b = max(0, int(t - radius)), min(len(energy), int(t + radius) + 1)
    if b <= a:
        return t
    return float(a + int(np.argmin(energy[a:b])))


def snap(words: list[dict], start: float, end: float, min_len: float, max_len: float,
         duration: float, energy: np.ndarray | None = None) -> tuple[float, float]:
    """
    Move [start, end] to clean boundaries. `words` are the transcript words
    near the clip (any order); `energy` is an optional per-second loudness
    curve used when there is no speech nearby. Returns (start, end) within
    [0, duration] with min_len <= end - start <= max_len whenever the video
    is long enough.
    """
    words = sorted((w for w in words if w["e"] > w["s"]), key=lambda w: w["s"])
    duration = float(duration) if duration else max(end, words[-1]["e"] if words else end)
    sents = sentences(words)
    starts = [s for s, _ in sents]
    ends = [e for _, e in sents]

    # --- start: nearest sentence start, preferring one at or before `start`
    near = [s for s in starts if start - SEARCH <= s <= start + SEARCH / 2]
    if near:
        before = [s for s in near if s <= start + 0.3]
        new_start = max(before) if before else min(near)
    else:
        w = _inside_word(words, start)
        new_start = w["s"] if w else _quiet_point(energy, start)
    new_start = max(0.0, new_start - (PAD_BEFORE if near or _inside_word(words, start) else 0.0))

    lo, hi = new_start + min_len, new_start + max_len

    # --- end: a sentence end inside [lo, hi], closest to the requested end
    cands = [e for e in ends if lo <= e + PAD_AFTER <= hi + PAD_AFTER and abs(e - end) <= SEARCH * 2]
    if not cands:
        cands = [e for e in ends if lo <= e + PAD_AFTER <= hi + PAD_AFTER]
    if cands:
        new_end = min(cands, key=lambda e: abs(e - end)) + PAD_AFTER
    else:
        target = min(max(end, lo), hi)
        w = _inside_word(words, target)
        if w:
            # finish the word if that still fits, else cut before it
            new_end = w["e"] + 0.05 if w["e"] + 0.05 <= hi else w["s"] - 0.02
        else:
            new_end = _quiet_point(energy, target) if energy is not None else target
            new_end = min(max(new_end, lo), hi)
            w = _inside_word(words, new_end)
            if w:
                new_end = w["s"] - 0.02 if w["s"] - 0.02 >= lo else w["e"] + 0.05

    new_end = min(new_end, duration)
    # keep the clip at least min_len long when the video allows it
    if new_end - new_start < min_len:
        new_start = max(0.0, new_end - min_len)
        if new_end - new_start < min_len:
            new_end = min(duration, new_start + min_len)
    if new_end - new_start > max_len:
        new_end = new_start + max_len
    return round(new_start, 2), round(new_end, 2)
