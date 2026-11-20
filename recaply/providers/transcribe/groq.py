"""
Groq Whisper with a local safety net.

`GroqWithFallback` sends every span to Groq in parallel (whisper-large-v3, 429s retried with exponential
backoff inside the HTTP layer). Spans Groq still refuses afterwards, or all of them when there is no
API key, are transcribed by local faster-whisper on the very same audio spans. Words from the two
sources are interchangeable, so the rest of the pipeline never knows which one produced them.
"""

from __future__ import annotations

from typing import Callable

from recaply.log import get_logger
from recaply.providers.transcribe.base import Transcriber

log = get_logger("groq")


class GroqWithFallback(Transcriber):
    def __init__(self, groq, local_factory: Callable[[], Transcriber], note: Callable[[str], None] | None = None):
        self.groq = groq                      # CloudWhisper, or None when there is no API key
        self._local_factory = local_factory
        self._local: Transcriber | None = None
        self.note = note or (lambda _m: None)
        self.used_local = groq is None
        self.name = "groq" if groq is not None else "faster-whisper"

    # what the UI shows while this runs
    @property
    def label(self) -> str:
        return "Local GPU/CPU" if self.used_local else "Groq Cloud"

    @property
    def cache_id(self) -> str:
        return self.groq.cache_id if self.groq is not None else self._get_local().cache_id

    def _get_local(self) -> Transcriber:
        if self._local is None:
            self._local = self._local_factory()
        return self._local

    def transcribe_spans(self, audio_path, spans, vocab="", language=None, progress=None):
        if self.groq is None:
            return self._get_local().transcribe_spans(audio_path, spans, vocab, language, progress)
        out = self.groq.transcribe_spans_partial(audio_path, spans, vocab, language, progress)
        failed = [i for i, o in enumerate(out) if o is None]
        if failed:
            self.used_local = True
            msg = f"Groq unavailable for {len(failed)} of {len(spans)} parts (rate limit?); used local Whisper for them."
            log.warning(msg)
            self.note(msg)
            redo = self._get_local().transcribe_spans(audio_path, [spans[i] for i in failed], vocab, language, None)
            for i, words in zip(failed, redo):
                out[i] = words
        return out


def build(settings, note: Callable[[str], None] | None = None) -> GroqWithFallback:
    from recaply.providers.transcribe.cloud_whisper import CloudWhisper
    from recaply.providers.transcribe.local_whisper import LocalWhisper
    groq = None
    if settings.groq_api_key:
        groq = CloudWhisper("groq", settings.groq_api_key, settings.groq_model,
                            parallel=settings.groq_parallel, retries=settings.groq_retries)
    elif note:
        note("No GROQ_API_KEY set; transcribing locally instead.")
    return GroqWithFallback(groq, LocalWhisper, note)
