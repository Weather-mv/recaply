"""Kick VOD chat history, best effort (Kick's bot filter may refuse the requests)."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from recaply.ingest.chat import ChatUnavailable
from recaply.providers.http import ProviderError, get_json

API = "https://kick.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (Clipforge)", "Referer": "https://kick.com/"}
STEP = 5.0            # seconds of chat per request
MAX_REQUESTS = 2500
WORKERS = 6


def parse_time(text) -> datetime:
    """A Kick timestamp ("2024-05-01 18:00:00", "...T18:00:00Z", "...+00:00") as an aware UTC datetime."""
    d = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00").replace(" ", "T"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_messages(payload: dict, start: datetime) -> list[dict]:
    """Messages {"t","text","user"} (t = seconds since `start`) from one history response."""
    data = payload.get("data") if isinstance(payload, dict) else None
    out = []
    for m in ((data or {}).get("messages") or []):
        try:
            when = parse_time(m["created_at"])
        except (KeyError, ValueError):
            continue
        text = m.get("content") or ""
        if text:
            out.append({"t": (when - start).total_seconds(), "text": text,
                        "user": (m.get("sender") or {}).get("username") or ""})
    return out


def fetch(src, cache, progress=None) -> list[dict]:
    uid = src.video_id or ""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,40}", uid):
        raise ChatUnavailable("not a Kick VOD")
    try:
        info = get_json(f"{API}/v1/video/{quote(uid)}", HEADERS)
        live = (info or {}).get("livestream") or {}
        channel_id = live.get("channel_id") or ((info or {}).get("livestream") or {}).get("channel", {}).get("id")
        start = parse_time(live["start_time"])
    except (ProviderError, KeyError, TypeError, ValueError) as e:
        raise ChatUnavailable(f"Kick refused or changed its API ({str(e)[:100]})") from e
    if not channel_id:
        raise ChatUnavailable("Kick gave no channel for this VOD")
    steps = min(MAX_REQUESTS, int(src.duration / STEP) + 1)
    results: list[list[dict] | None] = [None] * steps
    errors: list[str] = []
    done = [0]
    lock = threading.Lock()

    def one(i: int):
        if len(errors) > 5:                      # the platform is refusing: stop hammering it
            return
        t0 = start + timedelta(seconds=i * STEP)
        url = f"{API}/v2/channels/{int(channel_id)}/messages?start_time={quote(t0.strftime('%Y-%m-%dT%H:%M:%S.000Z'))}"
        try:
            results[i] = parse_messages(get_json(url, HEADERS, timeout=20, retries=1), start)
        except ProviderError as e:
            errors.append(str(e)[:100])
        time.sleep(0.05)
        with lock:
            done[0] += 1
            if progress and done[0] % 25 == 0:
                progress(done[0] / steps)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(one, range(steps)))
    msgs = [m for r in results if r for m in r]
    if not msgs and errors:
        raise ChatUnavailable(f"Kick refused the chat request ({errors[0]})")
    return msgs
