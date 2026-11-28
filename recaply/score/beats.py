"""
Story beats: a second LLM pass that labels what each part of a moment *does*
in the story, so a clip can be judged (and later edited) as hook -> setup ->
build -> payoff instead of as a blob of seconds.

    beats = score_beats(words, llm, profile, title, telemetry)
    # [{"start": 104.0, "end": 111.5, "type": "setup", "score": 55, "reason": "..."}, ...]

The transcript is cut into overlapping chunks of a few hundred words (a
local 3-4B model can't read a whole podcast, and a chunk keeps every call
small and fast). Each chunk goes to the model with a JSON schema; the model
answers `{"beats": [{"start_time", "end_time", "type", "virality_score",
"reasoning"}]}`. Replies are validated, clamped to the chunk, and the
overlapping chunks are merged. A chunk that fails is skipped: the clip keeps
its normal score, the job never fails because of beats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from common import fmt_hms
from recaply.log import get_logger
from recaply.profiles.base import Profile
from recaply.providers.http import ProviderError
from recaply.providers.llm import LLM
from recaply.score.boundaries import sentences
from recaply.settings import settings
from recaply.telemetry import Telemetry

log = get_logger("beats")

TYPES = ("hook", "setup", "build", "payoff", "reaction", "tangent")
PEAK_TYPES = ("payoff", "hook", "reaction")      # the beats that make a clip worth watching

SYSTEM = ("You are a story editor for short-form video. You split a transcript into story beats and rate "
          "how shareable each beat is. You answer with JSON only.")

SCHEMA = {
    "type": "object",
    "properties": {"beats": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "start_time": {"type": "string"},
            "end_time": {"type": "string"},
            "type": {"type": "string", "enum": list(TYPES)},
            "virality_score": {"type": "integer"},
            "reasoning": {"type": "string"},
        },
        "required": ["start_time", "end_time", "type", "virality_score", "reasoning"]}}},
    "required": ["beats"],
}


@dataclass
class Beat:
    start: float
    end: float
    type: str
    score: float
    reason: str

    def public(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2), "type": self.type,
                "score": round(self.score, 1), "reason": self.reason}


def parse_time(v) -> float | None:
    """Seconds from 83.5, "83.5", "1:23", "1:23.5" or "01:02:03" (None if it isn't a time)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().lower().rstrip("s").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", s)
    if not m:
        return None
    h, mi, sec = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + float(sec)


def chunk_words(words: list[dict], size: int | None = None, overlap: int | None = None) -> list[list[dict]]:
    """
    Cut `words` into chunks of about `size` words that start and end at sentence
    edges, each sharing about `overlap` words with the one before it (so a beat
    that straddles a cut is still seen whole by one chunk).
    """
    size = max(20, size or settings.beat_chunk_words)
    overlap = max(0, min(size // 2, settings.beat_overlap_words if overlap is None else overlap))
    if len(words) <= size:
        return [words] if words else []
    # sentence -> index range into words
    spans: list[tuple[int, int]] = []
    i = 0
    for _, e in sentences(words):
        j = i
        while j < len(words) and words[j]["s"] <= e:
            j += 1
        spans.append((i, max(j, i + 1)))
        i = max(j, i + 1)
    chunks: list[list[dict]] = []
    k = 0
    while k < len(spans):
        a = spans[k][0]
        m = k
        while m + 1 < len(spans) and spans[m][1] - a < size:
            m += 1
        b = spans[m][1]
        chunks.append(words[a:b])
        if b >= len(words):
            break
        # next chunk starts ~overlap words back, on a sentence start, and always moves forward
        nk = m + 1
        while nk - 1 > k and b - spans[nk - 1][0] <= overlap:
            nk -= 1
        k = max(nk, k + 1)
    return chunks


def transcript_lines(words: list[dict]) -> str:
    """"[83.5] sentence text" per sentence, with absolute seconds (what the model reads)."""
    lines, i = [], 0
    for s, e in sentences(words):
        text = []
        while i < len(words) and words[i]["s"] <= e:
            text.append(words[i]["w"])
            i += 1
        if text:
            lines.append(f"[{s:.1f}] {' '.join(text)}")
    return "\n".join(lines)


def build_prompt(words: list[dict], profile: Profile, title: str) -> str:
    a, b = words[0]["s"], words[-1]["e"]
    return "\n".join([
        f"Content type: {profile.brief}",
        f"Video title: {title}",
        f"This excerpt runs from {a:.1f}s to {b:.1f}s. Each line starts with its time in seconds.",
        "",
        transcript_lines(words),
        "",
        "Split the excerpt into 2-6 consecutive story beats. For each beat return:",
        '- "start_time" and "end_time": seconds inside the excerpt, as numbers in quotes (e.g. "104.5")',
        f'- "type": one of {", ".join(TYPES)}. hook = grabs attention; setup = context; build = rising '
        "tension or detail; payoff = the punchline, reveal or climax; reaction = response to the payoff; "
        "tangent = off-topic filler.",
        '- "virality_score": 0-100, how shareable this beat is on its own. Be strict: most beats deserve 20-70.',
        '- "reasoning": one short sentence.',
        "",
        'Reply as {"beats": [{"start_time": "...", "end_time": "...", "type": "payoff", '
        '"virality_score": 85, "reasoning": "..."}]}.',
    ])


def parse_beats(data: dict, lo: float, hi: float) -> list[Beat]:
    """Valid beats from a model reply, clamped to [lo, hi]. Bad items are dropped, never raised."""
    items = data.get("beats") or data.get("items") or []
    out: list[Beat] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        s, e = parse_time(it.get("start_time", it.get("start"))), parse_time(it.get("end_time", it.get("end")))
        if s is None or e is None:
            continue
        s, e = max(lo, min(s, hi)), max(lo, min(e, hi))
        if e - s < 0.5:
            continue
        kind = str(it.get("type") or "").strip().lower()
        if kind not in TYPES:
            kind = "build"
        try:
            score = max(0.0, min(100.0, float(it.get("virality_score", it.get("score", 0)))))
        except (TypeError, ValueError):
            continue
        out.append(Beat(s, e, kind, score, " ".join(str(it.get("reasoning") or it.get("reason") or "").split())[:160]))
    return sorted(out, key=lambda b: b.start)


def merge(beats: list[Beat]) -> list[Beat]:
    """One list from overlapping chunks: beats that cover the same seconds keep the higher-rated one."""
    out: list[Beat] = []
    for b in sorted(beats, key=lambda x: (x.start, -x.score)):
        dup = next((o for o in out if min(o.end, b.end) - max(o.start, b.start) > 0.5 * min(o.end - o.start, b.end - b.start)), None)
        if dup is None:
            out.append(b)
        elif b.score > dup.score:
            out[out.index(dup)] = b
    return sorted(out, key=lambda x: x.start)


def beat_score(beats: list[dict]) -> float | None:
    """0-100 for a clip from its beats: its best payoff / hook / reaction (None without beats)."""
    if not beats:
        return None
    peaks = [b["score"] for b in beats if b["type"] in PEAK_TYPES]
    return float(max(peaks) if peaks else max(b["score"] for b in beats))


def clip_beats(beats: list[dict], start: float, end: float) -> list[dict]:
    """The beats that overlap a clip, cut to the clip's edges."""
    out = []
    for b in beats:
        s, e = max(b["start"], start), min(b["end"], end)
        if e - s >= 0.5:
            out.append({**b, "start": round(s, 2), "end": round(e, 2), "time": fmt_hms(s)})
    return out


def score_beats(words: list[dict], llm: LLM, profile: Profile, title: str,
                telemetry: Telemetry | None = None) -> list[dict]:
    """Beats for the speech in `words` (absolute seconds), as plain dicts. [] when there is no speech or the model fails."""
    out: list[Beat] = []
    for chunk in chunk_words(words):
        lo, hi = chunk[0]["s"], chunk[-1]["e"]
        try:
            kwargs = {"schema": SCHEMA} if getattr(llm, "supports_schema", False) else {}
            data, usage = llm.complete_json(SYSTEM, build_prompt(chunk, profile, title), 2000, **kwargs)
        except (ProviderError, OSError, ValueError) as e:
            log.warning("beat chunk failed", provider=llm.provider, error=str(e)[:200])
            continue
        if telemetry:
            telemetry.add_tokens(usage.provider, usage.tokens_in, usage.tokens_out)
        out += parse_beats(data, lo, hi)
    return [b.public() for b in merge(out)]
