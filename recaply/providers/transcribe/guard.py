"""
Whisper hallucination guard.

On music, game sound, crowd noise or silence, Whisper invents speech: the
same word or phrase over and over ("आप आप आप आप...", "thank you. thank you.
thank you."), or a run of low-confidence filler. Those words would become
clip titles, captions and LLM input, so they are dropped here:

- `suspect_segment(seg)`: a faster-whisper segment that is probably invented
  (highly repetitive text, or "probably no speech" and low confidence),
- `drop_hallucinations(words)`: on any provider's word list, removes runs where
  one word or a 2-3 word phrase repeats 4+ times in a row, and runs of 6+
  words that all have very low confidence.

Pure functions, no model needed; used by local Whisper, the cloud providers
(through the block cache) and the editor's captions.
"""

from __future__ import annotations

import unicodedata

MIN_REPEATS = 4            # "no no no" is real speech; a 4th repeat in a row almost never is
MAX_GRAM = 3
LOW_P = 0.2
LOW_P_RUN = 6

MAX_COMPRESSION = 2.4      # gzip ratio of a segment's text: repetitive text compresses far better than speech
NO_SPEECH = 0.6
LOW_LOGPROB = -1.0


def _norm(token: str) -> str:
    """Lower case, letters / digits / combining marks only (so Devanagari vowel signs survive)."""
    return "".join(ch for ch in str(token).lower()
                   if ch.isalnum() or unicodedata.category(ch).startswith("M"))


def suspect_segment(seg) -> bool:
    """True for a faster-whisper segment that is probably invented rather than heard."""
    ratio = float(getattr(seg, "compression_ratio", 0.0) or 0.0)
    no_speech = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
    logprob = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
    return ratio > MAX_COMPRESSION or (no_speech > NO_SPEECH and logprob < LOW_LOGPROB)


def _repeat_end(toks: list[str], i: int) -> int | None:
    """End index of the longest run starting at i where a 1..MAX_GRAM-word phrase repeats MIN_REPEATS+ times."""
    best = None
    for n in range(1, MAX_GRAM + 1):
        gram = toks[i:i + n]
        if len(gram) < n or not all(gram):
            continue
        j, reps = i + n, 1
        while toks[j:j + n] == gram:
            j += n
            reps += 1
        if reps >= MIN_REPEATS and (best is None or j > best):
            best = j
    return best


def drop_hallucinations(words: list[dict]) -> list[dict]:
    """`words` ({"w", "s", "e", "p"?}) without repetition loops and long low-confidence runs. Order is kept."""
    if not words:
        return words
    toks = [_norm(w.get("w", "")) for w in words]
    drop = [False] * len(words)
    i = 0
    while i < len(words):
        end = _repeat_end(toks, i)
        if end is not None:
            for k in range(i, end):
                drop[k] = True
            i = end
        else:
            i += 1
    run: list[int] = []
    for k, w in enumerate(words + [{"p": 1.0}]):            # sentinel flushes the last run
        if k < len(words) and not drop[k] and float(w.get("p", 1.0) or 0.0) < LOW_P:
            run.append(k)
            continue
        if len(run) >= LOW_P_RUN:
            for r in run:
                drop[r] = True
        run = []
    if not any(drop):
        return words
    return [w for k, w in enumerate(words) if not drop[k]]
