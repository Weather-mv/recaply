"""
Typed settings for the clipping core, read once from the environment (and
`.env`, loaded by config.py). Every knob has a default that works on a
normal PC with no API keys: local faster-whisper and a heuristic scorer.

    from recaply.settings import settings
    settings.llm_provider          # "gemini"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import config  # noqa: F401  (loads .env into os.environ before we read it)
from config import CLIPS_DIR


def _str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Prices:
    """Estimated list prices in USD per million tokens / per audio hour.
    Only used for the cost line in job summaries; override with env vars."""
    llm_in: dict = field(default_factory=lambda: {
        "gemini": _float("RECAPLY_PRICE_GEMINI_IN", 0.30),
        "anthropic": _float("RECAPLY_PRICE_ANTHROPIC_IN", 1.00),
        "openai": _float("RECAPLY_PRICE_OPENAI_IN", 0.25),
        "ollama": 0.0,
    })
    llm_out: dict = field(default_factory=lambda: {
        "gemini": _float("RECAPLY_PRICE_GEMINI_OUT", 2.50),
        "anthropic": _float("RECAPLY_PRICE_ANTHROPIC_OUT", 5.00),
        "openai": _float("RECAPLY_PRICE_OPENAI_OUT", 2.00),
        "ollama": 0.0,
    })
    audio_hour: dict = field(default_factory=lambda: {
        "faster-whisper": 0.0,
        "groq": _float("RECAPLY_PRICE_GROQ_HOUR", 0.04),
        "openai": _float("RECAPLY_PRICE_OPENAI_HOUR", 0.36),
    })


@dataclass(frozen=True)
class Settings:
    # where caches and job outputs live
    data_dir: str = CLIPS_DIR
    cache_dir: str = field(default_factory=lambda: _str("RECAPLY_CACHE_DIR", os.path.join(CLIPS_DIR, "_cache")))
    jobs_db: str = field(default_factory=lambda: os.path.join(CLIPS_DIR, "_clipjobs.db"))

    # transcription: faster-whisper (local) | groq | openai | none
    transcriber: str = field(default_factory=lambda: _str("RECAPLY_TRANSCRIBER", "faster-whisper").lower())
    whisper_model_gpu: str = field(default_factory=lambda: _str("RECAPLY_FUNNEL_MODEL_GPU", "large-v3-turbo"))
    whisper_model_cpu: str = field(default_factory=lambda: _str("RECAPLY_FUNNEL_MODEL_CPU", "small"))
    whisper_device: str = field(default_factory=lambda: _str("RECAPLY_WHISPER_DEVICE", "auto"))
    groq_api_key: str = field(default_factory=lambda: _str("GROQ_API_KEY"), repr=False)
    groq_model: str = field(default_factory=lambda: _str("RECAPLY_GROQ_MODEL", "whisper-large-v3-turbo"))
    openai_api_key: str = field(default_factory=lambda: _str("OPENAI_API_KEY"), repr=False)
    openai_whisper_model: str = field(default_factory=lambda: _str("RECAPLY_OPENAI_WHISPER_MODEL", "whisper-1"))

    # LLM scoring and copy: gemini | anthropic | openai | ollama | none
    llm_provider: str = field(default_factory=lambda: _str("RECAPLY_LLM", "gemini").lower())
    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY") or _str("GOOGLE_API_KEY"), repr=False)
    gemini_model: str = field(default_factory=lambda: _str("RECAPLY_GEMINI_MODEL", "gemini-flash-latest"))
    anthropic_api_key: str = field(default_factory=lambda: _str("ANTHROPIC_API_KEY"), repr=False)
    anthropic_model: str = field(default_factory=lambda: _str("RECAPLY_ANTHROPIC_MODEL", "claude-haiku-5-5"))
    openai_model: str = field(default_factory=lambda: _str("RECAPLY_OPENAI_MODEL", "gpt-5-mini"))
    ollama_url: str = field(default_factory=lambda: _str("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _str("RECAPLY_OLLAMA_MODEL", "llama3.1"))

    # signals (Phase 3)
    motion_for_links: bool = field(default_factory=lambda: _flag("RECAPLY_MOTION_LINKS", True))   # read a small video stream for motion
    chat_replay: bool = field(default_factory=lambda: _flag("RECAPLY_CHAT", True))                # fetch chat replay for VODs

    # Phase 5
    pexels_key: str = field(default_factory=lambda: _str("RECAPLY_PEXELS_KEY"), repr=False)    # B-roll videos (free key)

    # funnel
    # windows sent to transcription + LLM; 0 = per profile (24 for talk, 16 otherwise)
    shortlist_size: int = field(default_factory=lambda: _int("RECAPLY_SHORTLIST", 0))
    workers: int = field(default_factory=lambda: _int("RECAPLY_SCAN_WORKERS", os.cpu_count() or 4))
    clip_workers: int = field(default_factory=lambda: _int("RECAPLY_CLIP_WORKERS", 3))
    job_workers: int = field(default_factory=lambda: _int("RECAPLY_JOB_WORKERS", 2))
    queue_backend: str = field(default_factory=lambda: _str("RECAPLY_QUEUE", "local").lower())  # local | rq
    redis_url: str = field(default_factory=lambda: _str("REDIS_URL", "redis://localhost:6379/0"))

    json_logs: bool = field(default_factory=lambda: _flag("RECAPLY_JSON_LOGS", False))
    prices: Prices = field(default_factory=Prices)

    def llm_ready(self) -> bool:
        """True when the configured LLM provider can be called."""
        p = self.llm_provider
        return ((p == "gemini" and bool(self.gemini_api_key))
                or (p == "anthropic" and bool(self.anthropic_api_key))
                or (p == "openai" and bool(self.openai_api_key))
                or p == "ollama")


settings = Settings()
os.makedirs(settings.cache_dir, exist_ok=True)
