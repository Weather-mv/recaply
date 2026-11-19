"""
"Clip ready" notifications: an in-app event, a generic JSON webhook and a
Discord webhook. A notification that can't be delivered is logged and
dropped, never allowed to disturb the recording.
"""

from __future__ import annotations

import os

from recaply.live import events
from recaply.live.config import ChannelConfig
from recaply.log import get_logger
from recaply.providers.http import ProviderError, post_json

log = get_logger("live.notify")
IN_APP_ONLY = ("spike", "clip_captured", "waiting", "recording")
COLORS = {"spike": 0xF5A524, "clip_ready": 0x2EB67D, "stream_ended": 0x8A8F98, "error": 0xE5484D}


def link(event: dict) -> str:
    """A link to the clip's page when RECAPLY_PUBLIC_URL is set (e.g. http://127.0.0.1:5000)."""
    base = (os.environ.get("RECAPLY_PUBLIC_URL") or "").rstrip("/")
    return f"{base}/clipjob/{event['job']}" if base and event.get("job") else ""


def discord_payload(event: dict) -> dict:
    text = event.get("text") or event["kind"]
    emb = {"title": (event.get("title") or event["kind"].replace("_", " ").title())[:250],
           "description": text[:1800], "color": COLORS.get(event["kind"], 0x5865F2)}
    if event.get("link"):
        emb["url"] = event["link"]
    # allowed_mentions empty: a clip title can never ping @everyone
    return {"username": "Recaply", "embeds": [emb], "allowed_mentions": {"parse": []}}


def send(cfg: ChannelConfig, kind: str, **data) -> dict:
    """Record the event in-app and deliver it to the channel's webhooks. Returns the event."""
    event = {"kind": kind, "channel": cfg.name, **data}
    event["link"] = link(event)
    if cfg.in_app:
        try:
            event["id"] = events.add(cfg.name, kind, **{k: v for k, v in event.items() if k not in ("kind", "channel")})
        except Exception as e:  # noqa: BLE001
            log.warning("event not stored", error=str(e)[:200])
    if kind in IN_APP_ONLY:
        return event                         # webhooks get finished clips, ended streams and problems
    for url, build in ((cfg.webhook, lambda e: e), (cfg.discord, discord_payload)):
        if not url:
            continue
        try:
            post_json(url, build(event), timeout=10, retries=1, parse=False)
        except ProviderError as e:
            log.warning("webhook failed", channel=cfg.name, error=str(e)[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("webhook failed", channel=cfg.name, error=str(e)[:200])
    return event
