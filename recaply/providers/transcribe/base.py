"""
Speech-to-text providers and the block cache they share.

Audio is transcribed on a fixed grid of 30-second blocks (each read with
2 s of overlap on both sides, keeping only words that start inside the
block). Each block is cached on its own, so when a later run needs other
windows (a different clip length, say) only blocks never transcribed before
are sent to the model.
"""

from __future__ import annotations

import math
from typing import Callable

from recaply.cache import StageCache
from recaply.log import get_logger
from recaply.telemetry import Telemetry

log = get_logger("transcribe")

BLOCK = 30.0          # small blocks: a shortlist window pulls in little extra audio
OVERLAP = 2.0


class Transcriber:
    """Base class. Implement `transcribe_spans`."""

    name = ""

    @property
    def cache_id(self) -> str:
        """Identifies the model in cache keys (different models never share cached words)."""
        return self.name

    def transcribe_spans(self, audio_path: str, spans: list[tuple[float, float]], vocab: str = "",
                         language: str | None = None,
                         progress: Callable[[float], None] | None = None) -> list[list[dict]]:
        """Words for each (start, end) span, with absolute times. One list per span."""
        raise NotImplementedError


def blocks_for(spans: list[tuple[float, float]], duration: float) -> list[int]:
    """Indices of the grid blocks covering all spans."""
    ks: set[int] = set()
    last = max(0, math.ceil(duration / BLOCK) - 1) if duration else None
    for s, e in spans:
        a = max(0, int(s // BLOCK))
        b = int(max(s, e - 1e-6) // BLOCK)
        for k in range(a, b + 1):
            if last is None or k <= last:
                ks.add(k)
    return sorted(ks)


def transcribe_cached(provider: Transcriber, audio_path: str, cache: StageCache,
                      spans: list[tuple[float, float]], duration: float, vocab: str = "",
                      language: str | None = None, telemetry: Telemetry | None = None,
                      progress: Callable[[float], None] | None = None) -> list[dict]:
    """
    Words covering `spans` (sorted, absolute seconds), using cached blocks
    where possible. Transcribes only the missing blocks, in one provider call.
    """
    ks = blocks_for(spans, duration)
    params = lambda k: {"k": k, "block": BLOCK, "model": provider.cache_id, "lang": language or "", "vocab": vocab[:200]}  # noqa: E731
    have: dict[int, list] = {}
    missing: list[int] = []
    for k in ks:
        hit = cache.get_json("words", params(k))
        if hit is None:
            missing.append(k)
        else:
            have[k] = hit
    if missing:
        read = [(max(0.0, k * BLOCK - OVERLAP), min(duration or 1e12, (k + 1) * BLOCK + OVERLAP)) for k in missing]
        log.info("transcribing", provider=provider.name, blocks=len(missing), cached=len(have))
        results = provider.transcribe_spans(audio_path, read, vocab=vocab, language=language, progress=progress)
        for k, words in zip(missing, results):
            core = [w for w in words if k * BLOCK <= w["s"] < (k + 1) * BLOCK]
            cache.put_json("words", core, params(k))
            have[k] = core
        if telemetry:
            telemetry.add_audio(provider.name, sum(e - s for s, e in read))
    elif telemetry and ks:
        telemetry.note(f"transcription: all {len(ks)} blocks from cache")
    words = [w for k in sorted(have) for w in have[k]]
    words.sort(key=lambda w: w["s"])
    for a, b in zip(words, words[1:]):           # keep them non-overlapping
        if b["s"] < a["e"]:
            a["e"] = max(a["s"] + 0.04, b["s"])
    return words
