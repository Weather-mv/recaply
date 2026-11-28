"""
Text-based editing: the clip's words are the timeline. Deleting words deletes
the video and sound under them; the rest is joined with a tiny audio fade at
each join. Everything here is pure (words in, segments out) except `render`,
which reuses the one-pass ffmpeg joiner from `render.tighten`.
"""

from __future__ import annotations

from recaply.render import tighten

PAD = 0.04            # seconds kept around a kept word next to a cut, so consonants aren't clipped
MIN_SEG = 0.15
EDGE_KEEP = 1.5        # more than this before the first word (after the last) is content, not a lead-in


def number(words: list[dict]) -> list[dict]:
    """Words with a stable id (their position), as the editor page shows and returns them."""
    return [{"i": i, "w": w["w"], "s": round(float(w["s"]), 3), "e": round(float(w["e"]), 3)} for i, w in enumerate(words)]


def filler_ids(words: list[dict]) -> list[int]:
    """Ids of filler words (um, uh...), for the editor's "select fillers"."""
    return [i for i, w in enumerate(words) if tighten.is_filler(w["w"])]


def keep_segments(words: list[dict], deleted: set[int], duration: float) -> list[tuple[float, float]]:
    """
    Segments of [0, duration] to keep when the words in `deleted` are removed.
    Runs of kept words that were neighbours stay in one piece (their pauses
    included); a cut is made only where deleted words were.
    """
    n = len(words)
    deleted = {i for i in deleted if 0 <= i < n}
    if not deleted:
        return [(0.0, float(duration))]
    dur = float(duration)
    if len(deleted) == n:
        return []
    # Each run of deleted words is cut out with the pauses between its words. Deleting the first (or last)
    # words trims the clip's start (end) too, unless what comes before the first word (after the last) is a
    # long stretch without speech, like gameplay or an intro: that isn't a word, so it stays.
    runs: list[list[int]] = []
    for i in sorted(deleted):
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    cuts: list[tuple[float, float]] = []
    for r in runs:
        i, j = r[0], r[-1]
        if i == 0:
            a = 0.0 if words[0]["s"] < EDGE_KEEP else words[0]["s"] - PAD
        else:
            a = min(words[i - 1]["e"] + PAD, words[i]["s"])
        if j == n - 1:
            b = dur if dur - words[j]["e"] < EDGE_KEEP else words[j]["e"] + PAD
        else:
            b = max(words[j + 1]["s"] - PAD, words[j]["e"])
        cuts.append((max(0.0, a), min(dur, max(a, b))))
    segs: list[list[float]] = []
    pos = 0.0
    for a, b in cuts:
        if a > pos:
            segs.append([pos, a])
        pos = max(pos, b)
    if pos < dur:
        segs.append([pos, dur])
    merged: list[list[float]] = []
    for a, b in segs:
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= MIN_SEG]


def retime(words: list[dict], deleted: set[int], segs: list[tuple[float, float]]) -> list[dict]:
    """The kept words with times in the edited clip (what captions are drawn from)."""
    out, offset = [], 0.0
    for a, b in segs:
        for i, w in enumerate(words):
            if i in deleted or not (a - 1e-6 <= w["s"] < b):
                continue
            out.append({"w": w["w"], "s": round(offset + (w["s"] - a), 3), "e": round(offset + (min(w["e"], b) - a), 3),
                        **({"o": w["o"]} if "o" in w else {})})
        offset += b - a
    return out


def edited_duration(segs: list[tuple[float, float]]) -> float:
    return round(sum(b - a for a, b in segs), 2)


def render(src: str, dst: str, segs: list[tuple[float, float]], has_audio: bool = True) -> str:
    """Write the kept segments of `src` into `dst`. Returns the encoder used."""
    if not segs:
        raise ValueError("Everything was deleted: keep at least a few words.")
    return tighten.apply(src, dst, segs, has_audio=has_audio)
