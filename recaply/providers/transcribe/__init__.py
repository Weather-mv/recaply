"""Transcription providers: faster-whisper (local, default), Groq, OpenAI."""

from __future__ import annotations

from recaply.providers.transcribe.base import Transcriber, transcribe_cached
from recaply.settings import settings


def get_transcriber(name: str | None = None) -> Transcriber | None:
    """The configured transcriber, or None for "none" (signals-only scoring)."""
    name = (name or settings.transcriber).lower()
    if name in ("none", "off", ""):
        return None
    if name in ("faster-whisper", "local", "whisper"):
        from recaply.providers.transcribe.local_whisper import LocalWhisper
        return LocalWhisper()
    if name == "groq":
        from recaply.providers.transcribe.cloud_whisper import CloudWhisper
        return CloudWhisper("groq", settings.groq_api_key, settings.groq_model)
    if name == "openai":
        from recaply.providers.transcribe.cloud_whisper import CloudWhisper
        return CloudWhisper("openai", settings.openai_api_key, settings.openai_whisper_model)
    raise ValueError(f"unknown transcriber '{name}' (faster-whisper, groq, openai or none)")


__all__ = ["Transcriber", "get_transcriber", "transcribe_cached"]
