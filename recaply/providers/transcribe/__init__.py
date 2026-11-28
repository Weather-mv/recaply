"""Transcription providers: faster-whisper (local, default), Groq, OpenAI."""

from __future__ import annotations

from recaply.providers.transcribe.base import Transcriber, transcribe_cached
from recaply.settings import settings


def get_transcriber(name: str | None = None, note=None) -> Transcriber | None:
    """The configured transcriber, or None for "none" (signals-only scoring)."""
    name = (name or settings.transcriber).lower()
    if name in ("none", "off", ""):
        return None
    if name in ("faster-whisper", "local", "whisper"):
        from recaply.providers.transcribe.local_whisper import LocalWhisper
        return LocalWhisper()
    if name == "groq":                       # Groq first; local Whisper when there's no key or it rate-limits
        from recaply.providers.transcribe import groq
        return groq.build(settings, note)
    if name == "openai":
        from recaply.providers.transcribe.cloud_whisper import CloudWhisper
        return CloudWhisper("openai", settings.openai_api_key, settings.openai_whisper_model)
    raise ValueError(f"unknown transcriber '{name}' (faster-whisper, groq, openai or none)")


__all__ = ["Transcriber", "get_transcriber", "transcribe_cached"]
