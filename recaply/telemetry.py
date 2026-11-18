"""
Per-stage timing and cost accounting, shown in every job summary.

    tel = Telemetry()
    with tel.stage("transcribe"):
        ...
    tel.add_tokens("gemini", 1200, 300)
    tel.summary()   # {"stages": {...}, "total_sec": ..., "est_usd": ...}
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from recaply.settings import settings


class Telemetry:
    """Collects seconds per stage, API tokens, audio minutes and estimated cost."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stages: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.audio_sec: dict[str, float] = {}
        self.notes: list[str] = []
        self._t0 = time.time()

    @contextmanager
    def stage(self, name: str, cached: bool = False):
        t = time.time()
        try:
            yield
        finally:
            with self._lock:
                s = self.stages.setdefault(name, {"sec": 0.0, "runs": 0, "cached": False})
                s["sec"] += time.time() - t
                s["runs"] += 1
                s["cached"] = s["cached"] or cached

    def mark_cached(self, name: str) -> None:
        with self._lock:
            self.stages.setdefault(name, {"sec": 0.0, "runs": 0, "cached": True})["cached"] = True

    def add_tokens(self, provider: str, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            t = self.tokens.setdefault(provider, {"in": 0, "out": 0, "calls": 0})
            t["in"] += int(tokens_in or 0)
            t["out"] += int(tokens_out or 0)
            t["calls"] += 1

    def add_audio(self, provider: str, seconds: float) -> None:
        with self._lock:
            self.audio_sec[provider] = self.audio_sec.get(provider, 0.0) + float(seconds)

    def note(self, text: str) -> None:
        with self._lock:
            self.notes.append(text)

    def est_usd(self) -> float:
        p = settings.prices
        usd = 0.0
        for prov, t in self.tokens.items():
            usd += t["in"] / 1e6 * p.llm_in.get(prov, 0.0) + t["out"] / 1e6 * p.llm_out.get(prov, 0.0)
        for prov, sec in self.audio_sec.items():
            usd += sec / 3600 * p.audio_hour.get(prov, 0.0)
        return round(usd, 5)

    def summary(self) -> dict:
        with self._lock:
            return {
                "stages": {k: {**v, "sec": round(v["sec"], 2)} for k, v in self.stages.items()},
                "total_sec": round(time.time() - self._t0, 2),
                "tokens": dict(self.tokens),
                "audio_min": {k: round(v / 60, 2) for k, v in self.audio_sec.items()},
                "est_usd": self.est_usd(),
                "notes": list(self.notes),
            }
