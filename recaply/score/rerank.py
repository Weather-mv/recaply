"""
Score the shortlist: the LLM reads each window's transcript (with absolute
timestamps) plus what the signals saw, and returns four 0-10 scores (hook,
engagement, value, shareability; combined in code into a 0-100 overall_virality),
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
        "- four scores from 0 to 10 (decimals allowed). Be strict: most moments deserve 3-7; reserve 9+ "
        "for exceptional ones:",
        '  - "hook_score": do the first 3 seconds grab attention instantly?',
        '  - "engagement_score": does it keep the viewer watching to the end (build-up, tension, pace)?',
        '  - "value_score": is it funny, insightful, surprising or shocking, and does it make sense without context?',
        '  - "shareability_score": would someone send this to a friend or repost it?',
        '- "overall_virality": 0-100, your overall verdict on how well it will perform as a short',
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


AXES = ("hook_score", "engagement_score", "value_score", "shareability_score")
AXIS_WEIGHTS = {"hook_score": 0.30, "engagement_score": 0.25, "value_score": 0.25, "shareability_score": 0.20}


def _num(v, lo: float, hi: float) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x)) if x == x else None        # NaN -> None


def virality(item: dict) -> dict | None:
    """
    The 4-axis virality score from a model's reply, re-checked in code:
    each axis clamped to 0-10, and overall_virality = the weighted axes
    (hook 30%, engagement 25%, value 25%, shareability 20%) x 10, averaged
    with the model's own overall verdict when it gave one that is close
    (a verdict far from its own axes is ignored, so the number can't be made up).
    Old-style replies with only "score" (0-100) get no axes: None.
    """
    axes = {k: _num(item.get(k), 0.0, 10.0) for k in AXES}
    if any(v is None for v in axes.values()):
        return None
    computed = 10.0 * sum(AXIS_WEIGHTS[k] * v for k, v in axes.items())   # type: ignore[operator]
    stated = _num(item.get("overall_virality"), 0.0, 100.0)
    overall = (computed + stated) / 2 if stated is not None and abs(stated - computed) <= 15 else computed
    return {**{k: round(v, 1) for k, v in axes.items()}, "overall_virality": round(overall, 1)}   # type: ignore[arg-type]


def heuristic_virality(w: Window, score: float, hook_strength: float) -> dict:
    """The same four axes without an LLM, from what the signals and the transcript show. overall = the rule score."""
    sig, txt = w.signal_score, (w.text_score if w.words else w.signal_score)
    axes = {"hook_score": 10 * (0.5 * hook_strength + 0.3 * sig + 0.2 * txt),
            "engagement_score": 10 * (0.7 * sig + 0.3 * txt),
            "value_score": 10 * (0.4 * sig + 0.6 * txt),
            "shareability_score": 10 * (0.5 * sig + 0.5 * txt)}
    return {**{k: round(max(0.0, min(10.0, v)), 1) for k, v in axes.items()}, "overall_virality": round(score, 1)}


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
    hook_strength = max(w.signals.values(), default=w.signal_score) if w.signals else w.signal_score
    return {"score": round(score, 1), "start": start, "end": end, "reason": reason,
            "virality": heuristic_virality(w, score, float(hook_strength)),
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
                vir = virality(it)
                try:
                    ls = vir["overall_virality"] if vir else max(0.0, min(100.0, float(
                        it.get("overall_virality", it.get("score", 0)))))
                    s = float(it.get("start", w.start))
                    e = float(it.get("end", w.end))
                except (TypeError, ValueError):
                    continue
                s = min(max(s, w.start - 5), w.end)
                e = min(max(e, s + 1), w.end + 5)
                results[i] = {
                    "score": round(llm_w * ls + (1 - llm_w) * 100 * w.signal_score, 1),
                    "llm_score": round(ls, 1), "start": s, "end": e, "virality": vir,
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
