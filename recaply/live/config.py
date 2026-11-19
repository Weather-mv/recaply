"""
Per-channel configuration for live mode, kept in `<data dir>/_live.json`.

A channel is one stream to watch: its link, which profile's signal weights
to use, how much to cut around a spike, how sensitive to be and where to
send the "clip ready" notification.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, fields

from recaply.settings import settings

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_lock = threading.Lock()


class ConfigError(ValueError):
    """The channel settings aren't valid (shown to the user as is)."""


@dataclass
class ChannelConfig:
    name: str
    url: str
    profile: str = "gaming"          # whose signal weights and clip lengths to use
    pre: float = 30.0                # seconds kept before the spike
    post: float = 15.0               # ...and after it
    threshold: float = 0.5           # fused score (0..1) that counts as a spike; lower = more clips
    cooldown: float = 60.0           # no second clip within this many seconds of the last one
    warmup: float = 120.0            # learn the stream's normal level for this long first
    segment_sec: int = 300           # rolling buffer segment length
    retention_min: int = 45          # how much of the stream stays on disk
    count: int = 3                   # clips the pipeline keeps from each captured moment
    auto_shorts: bool = False        # also render the best one as a finished short
    transcriber: str | None = None
    llm: str | None = None
    chat: bool = True                # listen to live chat (Twitch, YouTube)
    wait_live: bool = True           # offline now? keep checking and start when it goes live
    poll_sec: int = 60
    end_grace: int = 90              # stream gone this long = it has ended, stop
    max_hours: float = 0.0           # 0 = no limit
    webhook: str = ""                # generic JSON webhook
    discord: str = ""                # Discord webhook URL
    in_app: bool = True
    enabled: bool = False            # start watching when the app starts
    owner: int | None = None         # the account whose clips these are (None = CLI / single user)
    source_cmd: list | None = None   # advanced: a command that writes the stream to stdout ({url} is replaced)

    def public(self) -> dict:
        d = asdict(self)
        for k in ("webhook", "discord"):        # secrets: say whether set, not what
            d[k] = bool(d[k])
        return d


_NUM = {"pre": (5, 120), "post": (5, 60), "threshold": (0.15, 0.95), "cooldown": (10, 1800),
        "warmup": (30, 1800), "segment_sec": (5, 600), "retention_min": (5, 360), "count": (1, 10),
        "poll_sec": (10, 900), "end_grace": (10, 1800), "max_hours": (0, 48)}


def validate(raw: dict) -> ChannelConfig:
    known = {f.name for f in fields(ChannelConfig)}
    d = {k: v for k, v in (raw or {}).items() if k in known}
    name = str(d.get("name") or "").strip().lower()
    if not NAME.match(name):
        raise ConfigError("Channel name: 1-40 letters, digits, - or _.")
    url = str(d.get("url") or "").strip()
    if not d.get("source_cmd"):
        from common import allowed_url
        if not url.lower().startswith(("http://", "https://")) or not allowed_url(url):
            raise ConfigError("Use a YouTube, Twitch or Kick channel / live link.")
    d["name"], d["url"] = name, url
    from recaply.profiles import get_profile
    try:
        get_profile(str(d.get("profile") or "gaming"))
    except KeyError:
        raise ConfigError(f"Unknown profile '{d.get('profile')}'.") from None
    for k, (lo, hi) in _NUM.items():
        if k in d and d[k] is not None:
            try:
                v = float(d[k])
            except (TypeError, ValueError):
                raise ConfigError(f"{k} must be a number.") from None
            v = min(hi, max(lo, v))
            d[k] = int(v) if isinstance(ChannelConfig.__dataclass_fields__[k].default, int) else float(v)
    for k in ("webhook", "discord"):
        v = str(d.get(k) or "").strip()
        if v and not v.lower().startswith(("http://", "https://")):
            raise ConfigError(f"{k} must be an http(s) URL.")
        d[k] = v
    if d.get("source_cmd") is not None and not (isinstance(d["source_cmd"], list) and all(isinstance(x, str) for x in d["source_cmd"])):
        raise ConfigError("source_cmd must be a list of strings.")
    return ChannelConfig(**d)


def _path() -> str:
    return os.path.join(settings.data_dir, "_live.json")


def load_all() -> dict[str, ChannelConfig]:
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for item in raw.get("channels", []):
        try:
            c = validate(item)
            out[c.name] = c
        except ConfigError:
            continue
    return out


def _write(chs: dict[str, ChannelConfig]) -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.data_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"channels": [asdict(c) for c in chs.values()]}, f, indent=1)
    os.replace(tmp, _path())


def save(cfg: ChannelConfig) -> None:
    with _lock:
        chs = load_all()
        chs[cfg.name] = cfg
        _write(chs)


def remove(name: str) -> bool:
    with _lock:
        chs = load_all()
        if name not in chs:
            return False
        del chs[name]
        _write(chs)
        return True


def get(name: str) -> ChannelConfig | None:
    return load_all().get(name)
