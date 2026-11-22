"""Runs many channels at once, each with its own config and its own ChannelWorker."""

from __future__ import annotations

import threading

from recaply.live import config
from recaply.live.channel import ChannelWorker
from recaply.live.config import ChannelConfig, ConfigError


class LiveManager:
    def __init__(self, submit=None):
        self.submit = submit
        self.workers: dict[str, ChannelWorker] = {}
        self.lock = threading.Lock()

    def running(self, name: str) -> bool:
        w = self.workers.get(name)
        return bool(w and w.is_alive())

    def start(self, cfg: ChannelConfig) -> ChannelWorker:
        with self.lock:
            if self.running(cfg.name):
                raise ConfigError(f"{cfg.name} is already running.")
            w = ChannelWorker(cfg, submit=self.submit)
            self.workers[cfg.name] = w
            w.start()
            return w

    def start_saved(self, name: str) -> ChannelWorker:
        cfg = config.get(name)
        if cfg is None:
            raise ConfigError(f"No channel called {name}.")
        return self.start(cfg)

    def start_enabled(self) -> list[str]:
        started = []
        for cfg in config.load_all().values():
            if cfg.enabled and not self.running(cfg.name):
                self.start(cfg)
                started.append(cfg.name)
        return started

    def stop(self, name: str, wait: float = 0) -> bool:
        w = self.workers.get(name)
        if not w or not w.is_alive():
            return False
        w.stop()
        if wait:
            w.join(wait)
        return True

    def clip_now(self, name: str, seconds: float) -> dict:
        w = self.workers.get(name)
        if not w or not w.is_alive():
            raise RuntimeError("This channel isn't being watched right now.")
        return w.clip_now(seconds)

    def stop_all(self, wait: float = 20) -> None:
        for n in list(self.workers):
            self.stop(n)
        for w in list(self.workers.values()):
            w.join(wait)

    def status(self) -> list[dict]:
        out = {n: w.status() for n, w in self.workers.items()}
        for n, c in config.load_all().items():          # saved but not started: show as idle
            out.setdefault(n, {"name": n, "state": "idle", "note": "", "url": c.url, "profile": c.profile,
                               "recording_sec": 0, "spikes": 0, "clips": [], "score": 0, "chat": None,
                               "reconnects": 0, "reconnecting": False, "buffer_mb": 0.0,
                               "buffer_limit_mb": c.max_buffer_mb, "retention_min": c.retention_min,
                               "started": None, "alive": False})
        return sorted(out.values(), key=lambda s: s["name"])


_manager: LiveManager | None = None
_mlock = threading.Lock()


def get_manager() -> LiveManager:
    global _manager
    with _mlock:
        if _manager is None:
            _manager = LiveManager()
        return _manager
