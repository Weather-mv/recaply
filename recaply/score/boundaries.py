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


# ---------- a strong opening ----------

OPENING = 3.0             # the seconds a viewer judges a clip on
MOVE_GAIN = 0.12          # a later start must open this much stronger to be worth the trimmed lead-in
TRIM_COST = 0.004         # per second cut: only breaks ties, so a long silent lead-in still goes
PEAK_LEAD = 2.0           # the start stays at least this far before the clip's peak


def opening_strength(t: float, curve: np.ndarray | None, words: list[dict], starts: list[float],
                     hook_starts: list[float]) -> float:
    """
    0..1: how strongly a clip starting at `t` opens. Loud / moving seconds in the
    first 3 s, a sentence starting right at the cut (the viewer hears a full line,
    not the middle of one) with real words in it, and the AI's "hook" beat.
    """
    action = 0.0
    if curve is not None and len(curve):
        a, b = max(0, int(t)), min(len(curve), int(t + OPENING) + 1)
        if b > a:
            action = float(np.clip(curve[a:b], 0, 1).max())
    said = [w for w in words if t - 0.05 <= w["s"] < t + OPENING]
    speech = 0.0
    if said:
        speech = min(1.0, len(said) / 6)                     # a few words is a line; one "um" isn't
        if not any(abs(s - t) <= 0.5 for s in starts):
            speech *= 0.4                                    # it opens mid-sentence
    hooked = 1.0 if any(abs(h - t) <= 1.0 for h in hook_starts) else 0.0
    return 0.45 * action + 0.3 * speech + 0.25 * hooked


def anchor_start(words: list[dict], start: float, end: float, peak: float, min_len: float,
                 curve: np.ndarray | None = None, hook_starts: list[float] | None = None,
                 max_len: float | None = None, duration: float | None = None,
                 energy: np.ndarray | None = None) -> tuple[float, float, dict]:
    """
    Move a clip's start forward to its strongest opening: a sentence start, the
    AI's "hook" beat, or the second before the action picks up, so the clip
    doesn't open on a quiet lead-in. The peak stays in the clip; when the clip
    would get shorter than `min_len`, its end is pushed out to a clean point
    (a sentence end or a quiet second, never past `max_len` or the video).
    Returns (start, end, info); info says what changed and why.
    """
    words = sorted((w for w in words if w["e"] > w["s"]), key=lambda w: w["s"])
    starts = [s for s, _ in sentences(words)]
    ends = [e for _, e in sentences(words)]
    hook_starts = list(hook_starts or [])
    duration = float(duration) if duration else float(len(curve)) if curve is not None and len(curve) else end
    max_len = float(max_len) if max_len else max(min_len, end - start)
    # the end may grow to keep min_len, so the start is limited by the peak and by max_len, not by the end
    latest = min(peak - PEAK_LEAD, duration - min_len)
    base = opening_strength(start, curve, words, starts, hook_starts)
    info = {"moved": 0.0, "opening": round(base, 3), "was": round(base, 3), "why": "", "end_moved": 0.0}
    if latest <= start + 0.5:
        return start, end, info
    cands: list[tuple[float, str]] = [(s - PAD_BEFORE, "a line starts") for s in starts if start < s <= latest]
    cands += [(h, "the hook beat") for h in hook_starts if start < h <= latest]
    if curve is not None and len(curve):                     # the second before the action picks up
        for t in range(int(start) + 1, int(latest) + 1):
            if t < len(curve) and curve[t] >= 0.35 and curve[t] - curve[max(0, t - 1)] >= 0.15:
                cands.append((max(start, t - 1.0), "the action starts"))
    best, best_v, best_why = start, base, ""
    for t, why in cands:
        if t <= start or _inside_word(words, t):             # never open mid-word
            continue
        v = opening_strength(t, curve, words, starts, hook_starts) - TRIM_COST * (t - start)
        if v > best_v:
            best, best_v, best_why = t, v, why
    if best <= start or best_v - base < MOVE_GAIN:
        return start, end, info
    new_start, new_end = round(max(0.0, best), 2), end
    if new_end - new_start < min_len:                        # keep the clip long enough: push the end out
        lo, hi = new_start + min_len, min(duration, new_start + max_len)
        fits = [e + PAD_AFTER for e in ends if lo <= e + PAD_AFTER <= hi]
        if fits:
            new_end = min(fits)
        else:
            target = min(lo + 1.5, hi)
            new_end = _quiet_point(energy, target) if energy is not None else target
            new_end = min(max(new_end, lo), hi)
            w = _inside_word(words, new_end)
            if w:
                new_end = w["e"] + 0.05 if w["e"] + 0.05 <= hi else w["s"] - 0.02
        new_end = round(min(new_end, duration), 2)
    if new_end - new_start < min_len - 0.5:                  # the video ends too soon: leave the clip as it was
        return start, end, info
    info = {"moved": round(new_start - start, 2), "opening": round(best_v + TRIM_COST * (best - start), 3),
            "was": round(base, 3), "why": best_why, "end_moved": round(new_end - end, 2)}
    return new_start, new_end, info
