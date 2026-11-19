"""
Chat replay for VODs: a list of messages {"t": seconds from the start of the
video, "text": str, "user": str}, fetched once per source and cached.

- Twitch VODs: Twitch's public web chat-replay query (the one twitch.tv's own
  player uses), several time ranges in parallel.
- YouTube past live streams and premieres: yt-dlp's `live_chat` replay.
- Kick VODs: Kick's public chat history endpoint (best effort: Kick sits
  behind a bot filter that can refuse requests).
- A chat file (`--chat FILE` on the command line): TwitchDownloader JSON,
  yt-dlp `live_chat` JSON lines, or a plain JSON list of {t, text}.

Chat is optional. When it can't be had (no replay, chat disabled, the
platform refuses, offline) `fetch()` returns None and says why through the
`note` callback; the scoring carries on with the other signals, with the
chat weight shared between them.

Only public replay data is read, at a polite rate, and only when
`RECAPLY_CHAT` is on. Follow each platform's terms.
"""

from __future__ import annotations

from typing import Callable

from recaply.cache import StageCache
from recaply.ingest.sources import Source
from recaply.log import get_logger

log = get_logger("ingest.chat")
VERSION = 1


class ChatUnavailable(RuntimeError):
    """This source has no chat replay we can read (the message is shown in the job notes)."""


def clean_messages(msgs: list[dict], duration: float = 0.0) -> list[dict]:
    """Sorted, de-duplicated, clipped to [0, duration] (when known), text trimmed."""
    seen, out = set(), []
    for m in msgs:
        try:
            t = float(m["t"])
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(m.get("text") or "").split())[:300]
        if t < 0 or not text or (duration and t > duration + 5):
            continue
        key = (round(t, 1), str(m.get("user") or ""), text)
        if key in seen:
            continue
        seen.add(key)
        out.append({"t": round(t, 2), "text": text, "user": str(m.get("user") or "")[:40]})
    out.sort(key=lambda m: m["t"])
    return out


def platform(src: Source) -> str:
    e = (src.extractor or "").lower()
    if "twitch" in e:
        return "twitch"
    if "youtube" in e:
        return "youtube"
    if "kick" in e:
        return "kick"
    return ""


def fetch(src: Source, cache: StageCache, progress: Callable[[float], None] | None = None,
          note: Callable[[str], None] | None = None, enabled: bool = True) -> list[dict] | None:
    """The chat replay of `src` (cached), or None when there isn't one."""
    say = note or (lambda s: None)
    if src.kind != "url":
        return None
    cached = cache.get_json("chat", {"v": VERSION})
    if cached is not None:
        return cached or None            # [] cached = "looked, nothing there"
    plat = platform(src)
    if not enabled or not plat or src.is_live:
        return None
    try:
        if plat == "twitch":
            from recaply.ingest.chat import twitch as impl
        elif plat == "youtube":
            from recaply.ingest.chat import youtube as impl
        else:
            from recaply.ingest.chat import kick as impl
        msgs = clean_messages(impl.fetch(src, cache, progress), src.duration)
    except ChatUnavailable as e:
        say(f"no chat replay: {e}")
        cache.put_json("chat", [], {"v": VERSION})
        return None
    except Exception as e:  # noqa: BLE001 — chat must never fail a job
        log.warning("chat fetch failed", platform=plat, error=str(e)[:200])
        say(f"chat replay not read ({plat}): {str(e)[:120]}")
        return None                      # not cached: may work next time
    if not msgs:
        say("the chat replay is empty")
    cache.put_json("chat", msgs, {"v": VERSION})
    return msgs or None


def load_file(path: str, duration: float = 0.0) -> list[dict]:
    """Messages from a chat export file (see module docstring)."""
    from recaply.ingest.chat.files import parse_file
    return clean_messages(parse_file(path), duration)
