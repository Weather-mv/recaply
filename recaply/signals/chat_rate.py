"""
Chat activity: spikes in how fast (and how many different people) chat types,
and how much of it is hype (emotes, ALL CAPS, spam, laugh and "clip it" words).

Chat reacts a few seconds after the moment, so the curve is shifted earlier
by LAG seconds: the score at second t describes what chat did at t+LAG in
response to what happened at t. Rate is compared with the local baseline
(scaled by sqrt, so a quiet chat's single message isn't a "spike"), and
counts distinct chatters as well as messages, so one spammer doesn't look
like a crowd.
"""

from __future__ import annotations

import re

import numpy as np

from recaply.signals.base import Signal, SignalContext, register, rolling_median, smooth

LAG = 6                         # seconds chat takes to react
BASELINE_SEC = 300
Z_FROM, Z_FULL = 1.0, 5.0       # standardised rate mapped to 0..1
HYPE_FROM, HYPE_FULL = 0.25, 0.7
HYPE_WORDS = {
    "lul", "lulw", "lol", "lmao", "lmfao", "kekw", "kek", "omegalul", "pog", "pogchamp", "pogchamps", "poggers",
    "pogu", "omg", "wtf", "gg", "ggs", "insane", "clip", "clipped", "sheesh", "monkas", "pepega", "pepe",
    "catjam", "kappa", "icant", "ww", "www", "wow", "crazy", "nuts", "holy", "sheeesh", "clutch", "ace",
}
HYPE_EMOJI = set("💀😂🔥🤣😭😱🤯👏")
_WORD = re.compile(r"\w+", re.UNICODE)


def is_hype(text: str) -> bool:
    """A message that reads as excitement: emotes / laugh words, ALL CAPS, one word spammed, "!!"."""
    if any(c in HYPE_EMOJI for c in text):
        return True
    words = _WORD.findall(text)
    if not words:
        return False
    low = [w.lower() for w in words]
    if sum(w in HYPE_WORDS for w in low) >= 1 and len(low) <= 6:
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return True
    if len(low) >= 3 and max(low.count(w) for w in set(low)) >= 3:
        return True
    return text.count("!") >= 2 or text.count("?") >= 3


def per_second(messages: list[dict], n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(message count, distinct chatters, hype messages) per second for n seconds."""
    count = np.zeros(n, np.float32)
    hype = np.zeros(n, np.float32)
    users: list[set] = [set() for _ in range(n)]
    for m in messages:
        i = int(m["t"])
        if 0 <= i < n:
            count[i] += 1
            hype[i] += is_hype(m.get("text", ""))
            users[i].add(m.get("user") or id(m))
    return count, np.array([len(u) for u in users], np.float32), hype


def score(messages: list[dict], n: int) -> np.ndarray:
    """0..1 per second (already shifted by LAG) from chat messages."""
    if n <= 0 or not messages:
        return np.zeros(max(n, 0), np.float32)
    count, uniq, hype = per_second(messages, n)
    rate = smooth(0.5 * count + 0.5 * uniq, 5)
    base = rolling_median(rate, BASELINE_SEC)
    z = (rate - base) / np.sqrt(base + 0.5)
    spike = np.clip((z - Z_FROM) / (Z_FULL - Z_FROM), 0.0, 1.0)
    win = 7
    c, h = smooth(count, win) * win, smooth(hype, win) * win
    frac = np.divide(h, c, out=np.zeros_like(h), where=c > 0)
    hype_s = np.clip((frac - HYPE_FROM) / (HYPE_FULL - HYPE_FROM), 0.0, 1.0) * np.clip(c / 6.0, 0.0, 1.0)
    out = np.clip(0.75 * spike + 0.25 * hype_s, 0.0, 1.0)
    if LAG and n > LAG:
        out = np.concatenate([out[LAG:], np.zeros(LAG, np.float32)])
    return out.astype(np.float32)


def messages_per_minute(messages: list[dict], duration: float) -> float:
    return 60.0 * len(messages) / duration if duration > 0 else 0.0


@register
class ChatRate(Signal):
    name = "chat_rate"
    version = 1

    def available(self, ctx: SignalContext) -> bool:
        return bool(ctx.extras.get("chat"))

    def cache_params(self, ctx: SignalContext) -> dict:
        msgs = ctx.extras.get("chat") or []
        return {"n": len(msgs), "last": msgs[-1]["t"] if msgs else 0}

    def compute(self, ctx: SignalContext) -> np.ndarray:
        return score(ctx.extras["chat"], ctx.seconds)
