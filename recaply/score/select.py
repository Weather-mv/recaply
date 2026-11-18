"""
Final selection: rank scored clips, drop near-duplicates (overlapping or
cut from the same moment), and keep the best N.
"""

from __future__ import annotations

from recaply.score.windows import overlap

MAX_OVERLAP = 0.3       # share of the shorter clip two kept clips may share


def dedupe(clips: list[dict], max_overlap: float = MAX_OVERLAP) -> list[dict]:
    """Highest-scoring first; a clip overlapping a kept one by more than
    `max_overlap` of the shorter clip's length is dropped."""
    kept: list[dict] = []
    for c in sorted(clips, key=lambda c: c["score"], reverse=True):
        clen = c["end"] - c["start"]
        if any(overlap(c["start"], c["end"], k["start"], k["end"])
               > max_overlap * min(clen, k["end"] - k["start"]) for k in kept):
            continue
        kept.append(c)
    return kept


def select(clips: list[dict], n: int, min_score: float = 0.0) -> list[dict]:
    """The best `n` distinct clips scoring at least `min_score`, ranked (rank 1 = best)."""
    out = [c for c in dedupe(clips) if c["score"] >= min_score][: max(0, n)]
    for i, c in enumerate(out, 1):
        c["rank"] = i
    return out
