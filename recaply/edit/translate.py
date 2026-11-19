"""
Caption translation.

The words are grouped into short caption segments (a sentence or a few
seconds), the segment texts go to the LLM in one request, and the translated
text of each segment is spread across that segment's time span, word by word,
so the burned-in captions still follow the speaker at roughly the right pace.
It can't know which translated word matches which spoken one, so the karaoke
highlight is approximate; segment timing is exact.

Also writes SRT / WebVTT files (translated or original) for uploading as
caption tracks.
"""

from __future__ import annotations

import re

from recaply.log import get_logger
from recaply.providers.http import ProviderError
from recaply.providers.llm import LLM, Usage
from recaply.telemetry import Telemetry

log = get_logger("translate")
LANGUAGES = {
    "en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian",
    "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic",
    "hi": "Hindi", "id": "Indonesian", "vi": "Vietnamese", "th": "Thai", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese (Simplified)",
}
NO_SPACES = {"ja", "zh", "th"}                  # scripts written without spaces between words
MAX_SEG_WORDS = 12
GAP = 0.7
SYSTEM = ("You translate subtitles for short videos. Keep each line natural, short and in the speaker's tone "
          "(slang stays slang). Keep names, game terms and brand names as they are. Answer with JSON only.")


def segments(words: list[dict]) -> list[list[dict]]:
    """Caption segments: a new one after sentence-ending punctuation, a pause over GAP, or MAX_SEG_WORDS words."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > GAP or len(cur) >= MAX_SEG_WORDS or re.search(r"[.!?…]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def seg_text(seg: list[dict]) -> str:
    return " ".join(w["w"] for w in seg).strip()


def spread(text: str, start: float, end: float, lang: str) -> list[dict]:
    """Words of `text` with times spread evenly over [start, end]."""
    toks = list(text.replace(" ", "")) if lang in NO_SPACES else text.split()
    if lang in NO_SPACES:                                   # a few characters per caption word
        toks = ["".join(toks[i:i + 2]) for i in range(0, len(toks), 2)]
    toks = [t for t in toks if t]
    if not toks:
        return []
    span = max(0.05, end - start)
    step = span / len(toks)
    return [{"w": t, "s": round(start + i * step, 3), "e": round(start + (i + 1) * step - 0.01, 3)} for i, t in enumerate(toks)]


def translate_lines(llm: LLM, lines: list[str], lang: str, tel: Telemetry | None = None, batch: int = 60) -> list[str]:
    """Translate each line; the answer always has the same length as the input (a missing line stays as it was)."""
    name = LANGUAGES.get(lang, lang)
    out: list[str] = []
    for i in range(0, len(lines), batch):
        part = lines[i:i + batch]
        prompt = (f"Translate these {len(part)} subtitle lines to {name}. Return {{\"lines\": [...]}} with exactly "
                  f"{len(part)} strings, in the same order, one per input line.\n\n"
                  + "\n".join(f"{n + 1}. {t}" for n, t in enumerate(part)))
        data, usage = llm.complete_json(SYSTEM, prompt, max_tokens=4000)
        if tel is not None and isinstance(usage, Usage):
            tel.add_tokens(usage.provider, usage.tokens_in, usage.tokens_out)
        got = data.get("lines") or data.get("items") or []
        got = [str(x) for x in got] if isinstance(got, list) else []
        if len(got) != len(part):
            log.warning("translation line count mismatch", want=len(part), got=len(got))
            got = (got + part[len(got):])[:len(part)]
        out.extend(got)
    return out


def translate_words(words: list[dict], lang: str, llm: LLM | None, tel: Telemetry | None = None) -> list[dict]:
    """The words as translated, timed words. Raises ProviderError when there's no LLM or it fails."""
    if lang not in LANGUAGES:
        raise ProviderError(f"Unknown language '{lang}'.")
    if not words:
        return []
    if llm is None:
        raise ProviderError("Translation needs an AI key (Gemini's free tier works): set GEMINI_API_KEY or RECAPLY_LLM.")
    segs = segments(words)
    texts = translate_lines(llm, [seg_text(s) for s in segs], lang, tel)
    out: list[dict] = []
    for seg, text in zip(segs, texts):
        out.extend(spread(text, seg[0]["s"], seg[-1]["e"], lang))
    return out


# ---------- subtitle files ----------

def _ts(sec: float, vtt: bool) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def cues(words: list[dict], lang: str = "en") -> list[tuple[float, float, str]]:
    """Subtitle cues (start, end, text) from words: one per caption segment, text split to two lines when long."""
    out = []
    join = "" if lang in NO_SPACES else " "
    for seg in segments(words):
        text = join.join(w["w"] for w in seg).strip()
        if len(text) > 42 and " " in text:
            mid = text.rfind(" ", 0, len(text) // 2 + 8)
            if mid > 10:
                text = text[:mid] + "\n" + text[mid + 1:]
        out.append((seg[0]["s"], max(seg[-1]["e"], seg[0]["s"] + 0.4), text))
    return out


def to_srt(words: list[dict], lang: str = "en") -> str:
    return "".join(f"{n}\n{_ts(a, False)} --> {_ts(b, False)}\n{t}\n\n" for n, (a, b, t) in enumerate(cues(words, lang), 1))


def to_vtt(words: list[dict], lang: str = "en") -> str:
    return "WEBVTT\n\n" + "".join(f"{_ts(a, True)} --> {_ts(b, True)}\n{t}\n\n" for a, b, t in cues(words, lang))
