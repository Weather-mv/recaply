"""
Captions for a post, written for each network in one AI call.

The model starts from what Clipforge already knows about the clip (its
title, hook, description, hashtags and transcript) and returns a title (where
the network has one), a caption and hashtags per network, each within that
network's limits. The usual AI chain is used (Ollama on this PC, or the
cloud keys saved in Settings). Without any AI, the clip's own copy is used.
"""

from __future__ import annotations

import re

from recaply.log import get_logger
from recaply.social import networks

log = get_logger("social.captions")
TONES = ("hype", "casual", "professional", "funny", "informative")
SYSTEM = ("You write social media posts for short video clips. Reply with JSON only. Write in the same language as the "
          "clip's transcript. Never invent facts that aren't in the clip. No emojis unless the tone is hype or funny.")


def _tags(raw, limit: int) -> list[str]:
    out: list[str] = []
    for t in raw if isinstance(raw, list) else re.split(r"[\s,]+", str(raw or "")):
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:limit]


def compose(caption: str, tags: list[str], limit: int) -> str:
    """The caption with its hashtags on the last line, cut to fit the network's limit."""
    tail = " ".join("#" + t for t in tags)
    caption = (caption or "").strip()
    room = limit - (len(tail) + 2 if tail else 0)
    if room < 20:                                   # X: drop hashtags before cutting the words
        tail, room = "", limit
    if len(caption) > room:
        caption = caption[: room - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return f"{caption}\n\n{tail}".strip() if tail else caption


def fallback(clip: dict, nets: list[str]) -> dict:
    """Without AI: the clip's own title / hook / description, fitted to each network."""
    title = (clip.get("title") or "").strip() or "New clip"
    body = " ".join(x for x in ((clip.get("hook") or "").strip(), (clip.get("description") or "").strip()) if x) or title
    out = {}
    for k in nets:
        n = networks.get(k)
        tags = _tags(clip.get("hashtags") or [], min(5, n.hashtag_max))
        out[k] = {"title": title[: n.title_max] if n.title_max else "", "caption": compose(body, tags, n.caption_max),
                  "hashtags": tags}
    return out


def _prompt(clip: dict, nets: list[str], tone: str, transcript: str, extra: str) -> str:
    rules = []
    for k in nets:
        n = networks.get(k)
        title = f'a "title" of at most {min(n.title_max, 90)} characters, ' if n.title_max else 'no title (""), '
        style = {"youtube": "a description that hooks in the first line, then 1-2 sentences of context",
                 "linkedin": "a short story-style post: a hook line, 2-4 short lines of insight, then a question to the reader",
                 "x": "one punchy line (the hashtags count toward 280 characters, so use at most 2)",
                 "instagram": "a hook line, a short line of context, then a call to action (follow / comment)"}.get(k, "a caption")
        rules.append(f'- "{k}": {title}"caption": {style}, at most {n.caption_max - 60} characters, '
                     f'"hashtags": up to {min(n.hashtag_max, 8)} hashtags without the # sign')
    return (f"Clip title: {clip.get('title') or ''}\nHook: {clip.get('hook') or ''}\n"
            f"Description: {clip.get('description') or ''}\nExisting hashtags: {', '.join(clip.get('hashtags') or [])}\n"
            f"Transcript (start of the clip): {transcript[:1800]}\n\nTone: {tone}.\n"
            + (f"Extra instructions from the creator: {extra[:400]}\n" if extra else "")
            + "Write one post for each of these networks:\n" + "\n".join(rules)
            + '\nReply as {"<network>": {"title": "...", "caption": "...", "hashtags": ["..."]}, ...}')


def generate(clip: dict, nets: list[str], tone: str = "hype", transcript: str = "", extra: str = "") -> tuple[dict, str]:
    """({network: {title, caption, hashtags}}, who wrote it: "ai:<provider>" or "clip")."""
    nets = [k for k in nets if networks.get(k)]
    if not nets:
        return {}, "clip"
    tone = tone if tone in TONES else "hype"
    base = fallback(clip, nets)
    from recaply.providers.llm import llm_chain
    chain = llm_chain()
    if chain is None:
        return base, "clip"
    try:
        data, _ = chain.complete_json(SYSTEM, _prompt(clip, nets, tone, transcript, extra), max_tokens=2500)
    except Exception as e:  # noqa: BLE001 — fall back to the clip's own copy
        log.warning("caption AI failed", error=str(e)[:200])
        return base, "clip"
    out = {}
    for k in nets:
        n = networks.get(k)
        d = data.get(k) if isinstance(data, dict) else None
        if not isinstance(d, dict) or not str(d.get("caption") or "").strip():
            out[k] = base[k]
            continue
        tags = _tags(d.get("hashtags"), min(n.hashtag_max, 8))
        cap = re.sub(r"(\s#\w+)+\s*$", "", str(d["caption"]).strip())    # hashtags go on their own line
        title = " ".join(str(d.get("title") or "").split())[: n.title_max] if n.title_max else ""
        out[k] = {"title": title or base[k]["title"], "caption": compose(cap, tags, n.caption_max), "hashtags": tags}
    return out, f"ai:{chain.provider}"
