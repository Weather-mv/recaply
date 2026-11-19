"""
Score the shortlist: the LLM reads each window's transcript (with absolute
timestamps) plus what the signals saw, and returns a 0-100 virality score,
the best self-contained start/end inside the window, a one-line reason, a
title, a 3-second hook, a post description, hashtags and a few words worth
emphasising in the captions. Windows go in batches of 12, in parallel.

Without an LLM (no key, provider down, rate limited after retries) every
window is scored by `heuristic()` from the signals and transcript instead,
so a job never fails because of the LLM; the job summary says which was used.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from common import fmt_hms
from recaply.log import get_logger
from recaply.profiles.base import Profile
from recaply.providers.http import ProviderError
from recaply.providers.llm import LLM
from recaply.score.boundaries import sentences
from recaply.score.windows import Window, words_between
from recaply.telemetry import Telemetry

log = get_logger("rerank")
BATCH = 12
BATCH_LOCAL = 3        # a 3-4B local model reads far less at once than a cloud model
MAX_TAGS = 5

SYSTEM = ("You are an expert short-form video editor. You pick the moments from long videos that will "
          "perform best as standalone YouTube Shorts / TikToks / Reels, and you judge them strictly and "
          "honestly. You answer with JSON only.")

SIGNAL_NAMES = {"audio_energy": "loudness spike", "shout_laugh": "shouting or laughter", "chat_rate": "chat spike",
                "motion": "fast motion",
                "valorant_rounds": "round intensity"}


def clean_tags(raw, extra: tuple[str, ...] = ()) -> list[str]:
    """Hashtags as "#word" strings: letters/digits only, no repeats, at most MAX_TAGS. Accepts a list or a "#a #b" string."""
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    out: list[str] = []
    for t in list(raw or []) + list(extra):
        tag = re.sub(r"[^0-9A-Za-z_]", "", str(t))[:30]
        if tag and not tag.isdigit() and "#" + tag.lower() not in {x.lower() for x in out}:
            out.append("#" + tag)
    return out[:MAX_TAGS]


def clean_words(raw, limit: int = 4) -> list[str]:
    """Emphasis words: single words only, at most `limit`."""
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    out = []
    for w in raw or []:
        w = re.sub(r"[^0-9A-Za-z'$%-]", "", str(w))
        if w and w.lower() not in {x.lower() for x in out}:
            out.append(w)
    return out[:limit]


def _transcript_block(w: Window) -> str:
    ws = words_between(w.words, w.start, w.end)
    if not ws:
        return "(no speech)"
    lines = []
    i = 0
    for s, e in sentences(ws):
        text = []
        while i < len(ws) and ws[i]["s"] <= e:
            text.append(ws[i]["w"])
            i += 1
        if text:
            lines.append(f"[{s:.1f}] {' '.join(text)}")
    return "\n".join(lines)


def _signal_note(w: Window) -> str:
    notes = [f"{SIGNAL_NAMES.get(k, k)} {v:.2f}" for k, v in sorted(w.signals.items(), key=lambda kv: -kv[1])
             if v >= 0.3]
    return ", ".join(notes) or "no strong signal"


def build_prompt(windows: list[tuple[int, Window]], profile: Profile, title: str) -> str:
    parts = [
        f"Content type: {profile.brief}",
        f"Video title: {title}",
        f"Clip length: between {profile.clip_min:.0f} and {profile.clip_max:.0f} seconds "
        f"(about {profile.clip_target:.0f} is ideal).",
        "",
        "Candidates follow. Each has its window in absolute seconds, what the cheap signals "
        "detected (0-1, higher = stronger), and the transcript with each sentence's start time.",
        "",
    ]
    for i, w in windows:
        parts.append(f'<candidate id="{i}" start="{w.start:.1f}" end="{w.end:.1f}" '
                     f'signals="{_signal_note(w)}">')
        parts.append(_transcript_block(w))
        parts.append("</candidate>")
    parts += [
        "",
        "For EVERY candidate return an object with:",
        '- "id": the candidate id',
        '- "score": 0-100 viral potential (strong hook in the first 3 seconds, clear payoff, makes sense '
        "without context, emotion / humour / surprise / insight, shareable). Be strict: most moments "
        "deserve 30-70; reserve 85+ for exceptional ones.",
        '- "start", "end": absolute seconds of the best self-contained part inside the window, within the '
        "length range. Start at the start of a sentence (or just before the action), end right after the "
        "payoff. Never cut mid-sentence.",
        '- "reason": why it works (or doesn\'t), concrete, max 20 words',
        '- "title": a catchy title, max 60 characters, no hashtags',
        '- "hook": on-screen text for the first 3 seconds, max 8 words',
        '- "description": the post caption, 1-2 sentences (max 200 characters), no hashtags',
        '- "hashtags": 3-5 relevant hashtags without the # sign (topic and niche, not just "shorts")',
        '- "emphasis": up to 4 single words from the clip that deserve highlighting in the captions',
        "",
        'Reply as {"clips": [ ... ]}.',
    ]
    return "\n".join(parts)


def _llm_batch(llm: LLM, batch: list[tuple[int, Window]], profile: Profile, title: str,
               telemetry: Telemetry | None) -> dict[int, dict]:
    data, usage = llm.complete_json(SYSTEM, build_prompt(batch, profile, title))
    if telemetry:
        telemetry.add_tokens(usage.provider, usage.tokens_in, usage.tokens_out)
    items = data.get("clips") or data.get("items") or []
    out: dict[int, dict] = {}
    ids = {i for i, _ in batch}
    for it in items:
        try:
            i = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        if i in ids:
            out[i] = it
    return out


def headline(words: list[dict], profile: Profile, limit: int = 60) -> str:
    """The clip's most quotable full sentence (keywords, ! and ?, length), for titles without an LLM."""
    best, best_score = "", 0.0
    i = 0
    for s, e in sentences(words):
        text = []
        while i < len(words) and words[i]["s"] <= e:
            text.append(words[i]["w"])
            i += 1
        line = " ".join(text).strip()
        n = len(text)
        if n < 4:
            continue
        low = line.lower()
        score = min(n, 12) / 12 + 0.6 * sum(low.count(k) for k in profile.keywords) + 0.4 * ("!" in line)             + 0.3 * ("?" in line)
        if score > best_score:
            best, best_score = line, score
    if len(best) > limit:
        best = best[: limit - 3].rsplit(" ", 1)[0] + "..."
    return best


def heuristic(w: Window, profile: Profile) -> dict:
    """Score / span / copy for a window without an LLM."""
    text_w = profile.weights.get("transcript", 0.0) if w.words else 0.0
    score = 100.0 * ((1 - text_w) * w.signal_score + text_w * w.text_score)
    start = max(w.start, w.peak - profile.clip_target * 0.65)
    end = min(w.end, start + profile.clip_target)
    ws = words_between(w.words, start, end)
    first = ""
    if ws:
        s0, e0 = sentences(ws)[0]
        first = " ".join(x["w"] for x in ws if s0 <= x["s"] <= e0)
    title = (first[:57] + "...") if len(first) > 60 else first
    reason = f"{_signal_note(w)} around {fmt_hms(w.peak)}"
    if w.text_score >= 0.4:
        reason += "; lively speech"
    return {"score": round(score, 1), "start": start, "end": end, "reason": reason,
            "title": title or f"Moment at {fmt_hms(w.peak)}", "hook": "", "description": "",
            "hashtags": clean_tags([], (profile.kind if profile.kind != "talk" else "podcast", "shorts")),
            "emphasis": []}


def rerank(windows: list[Window], profile: Profile, title: str, llm: LLM | None,
           telemetry: Telemetry | None = None) -> tuple[list[dict], str]:
    """
    Scored suggestions for each window (same order), and which scorer was
    used ("llm:<provider>", "heuristic", or "mixed" when some batches failed).
    Each suggestion: score (0-100, blended with the signal score), start, end,
    reason, title, hook, llm_score (None without LLM).
    """
    results: list[dict | None] = [None] * len(windows)
    used = "heuristic"
    if llm is not None and windows:
        indexed = list(enumerate(windows))
        size = BATCH_LOCAL if llm.provider == "ollama" else BATCH
        batches = [indexed[i:i + size] for i in range(0, len(indexed), size)]
        failed = 0

        def run(batch):
            try:
                return _llm_batch(llm, batch, profile, title, telemetry)
            except (ProviderError, OSError, ValueError) as e:
                log.warning("llm batch failed, using heuristic", provider=llm.provider, error=str(e)[:200])
                return None

        with ThreadPoolExecutor(1 if llm.provider == "ollama" else min(3, len(batches))) as ex:
            outs = list(ex.map(run, batches))
        llm_w = min(0.9, 0.5 + 0.4 * profile.weights.get("transcript", 0.0))
        for batch, out in zip(batches, outs):
            if out is None:
                failed += 1
                continue
            for i, w in batch:
                it = out.get(i)
                if not it:
                    continue
                try:
                    ls = max(0.0, min(100.0, float(it.get("score", 0))))
                    s = float(it.get("start", w.start))
                    e = float(it.get("end", w.end))
                except (TypeError, ValueError):
                    continue
                s = min(max(s, w.start - 5), w.end)
                e = min(max(e, s + 1), w.end + 5)
                results[i] = {
                    "score": round(llm_w * ls + (1 - llm_w) * 100 * w.signal_score, 1),
                    "llm_score": round(ls, 1), "start": s, "end": e,
                    "reason": str(it.get("reason") or "")[:200],
                    "title": str(it.get("title") or "")[:80],
                    "hook": str(it.get("hook") or "")[:60],
                    "description": " ".join(str(it.get("description") or "").split())[:300],
                    "hashtags": clean_tags(it.get("hashtags"), ("shorts",)),
                    "emphasis": clean_words(it.get("emphasis")),
                }
        used = f"llm:{llm.provider}" if failed == 0 else ("heuristic" if failed == len(batches) else "mixed")
    for i, w in enumerate(windows):
        if results[i] is None:
            results[i] = {**heuristic(w, profile), "llm_score": None}
    return results, used  # type: ignore[return-value]
