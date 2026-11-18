"""
Content-type auto-detection.

Phase 1 decides from metadata only (title, description, tags, categories,
platform). Phase 3 adds measured cues from the first minutes (speech ratio,
speaker count, facecam/HUD presence, chat availability). The user can always
override the choice.
"""

from __future__ import annotations

import re

from recaply.profiles.base import Detection

_RULES: list[tuple[str, str]] = [
    ("valorant", r"\bvalorant\b|\bvalo\b|\bvct\b"),
    ("open_world", r"\bgta\b|grand theft auto|\brdr ?2?\b|red dead|\bnopixel\b|\broleplay\b|\bgta ?rp\b"),
    ("fps", r"\bcs ?2\b|counter[- ]strike|\bapex\b|fortnite|call of duty|\bcod\b|warzone|overwatch|"
            r"rainbow six|\br6\b|\bpubg\b|\bhalo\b|battlefield|\bdeadlock\b|marvel rivals|\bthe finals\b"),
    ("talk", r"podcast|interview|\bepisode\b|\bep\.? ?\d|conversation|\bpanel\b|webinar|lecture|keynote|"
             r"\bted\b|q ?& ?a|\bdebate\b|talk show|\bshow #?\d"),
    ("irl", r"\birl\b|just chatting|\bvlog\b|day in the life|\bwalking\b|travel stream"),
]
_TALK_CATEGORIES = {"education", "news & politics", "science & technology", "howto & style",
                    "nonprofits & activism", "people & blogs"}


def detect_profile(meta: dict) -> Detection:
    """
    Pick a profile key from source metadata. `meta` may hold title,
    description, tags, categories, extractor and game (all optional).
    """
    title = (meta.get("title") or "").lower()
    desc = (meta.get("description") or "")[:600].lower()
    tags = " ".join(meta.get("tags") or []).lower()
    game = (meta.get("game") or "").lower()
    cats = {c.lower() for c in (meta.get("categories") or [])}
    extractor = (meta.get("extractor") or "").lower()

    scores: dict[str, float] = {}
    for key, pattern in _RULES:
        rx = re.compile(pattern)
        s = 3.0 * bool(rx.search(title)) + 2.0 * bool(rx.search(game)) + \
            1.5 * bool(rx.search(tags)) + 0.5 * bool(rx.search(desc))
        if s:
            scores[key] = s
    if "gaming" in cats:
        scores["gaming"] = scores.get("gaming", 0) + 1.0
        if not re.search(r"podcast|interview|talk show", title):   # "episode 4" of a let's-play is gaming
            scores.pop("talk", None)
    if cats & _TALK_CATEGORIES:
        scores["talk"] = scores.get("talk", 0) + 1.0
    if any(x in extractor for x in ("twitch", "kick")):
        scores["gaming"] = scores.get("gaming", 0) + 0.5

    if not scores:
        return Detection("irl", "no metadata to go on; balanced profile", scores)
    best = max(scores, key=scores.get)
    # specific games beat the generic gaming profile when both matched
    reason = {
        "valorant": "Valorant in the title/tags",
        "open_world": "GTA / open-world game in the title/tags",
        "fps": "shooter game in the title/tags",
        "talk": "looks like a podcast / interview / talk",
        "irl": "looks like an IRL / just-chatting stream",
        "gaming": "gaming category or streaming platform",
    }[best]
    return Detection(best, reason, scores)
