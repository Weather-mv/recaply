"""
Cloud Whisper over the OpenAI-compatible audio API: Groq (fast, cheap) or
OpenAI. Each span is encoded as 16 kHz mono FLAC (a 64 s block is ~1 MB, far
under the upload limits) and sent in parallel, with word timestamps.
"""

from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from recaply.log import get_logger
from recaply.providers.http import ProviderError, post_multipart
from recaply.providers.transcribe.base import Transcriber

log = get_logger("cloud_whisper")

ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
    "openai": "https://api.openai.com/v1/audio/transcriptions",
}


def _flac(path: str, start: float, end: float) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{max(0.1, end - start):.3f}",
           "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", "-f", "flac", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True, timeout=300).stdout


class CloudWhisper(Transcriber):
    def __init__(self, provider: str, api_key: str, model: str, parallel: int = 4, retries: int = 3):
        if provider not in ENDPOINTS:
            raise ValueError(f"unknown cloud transcriber {provider}")
        if not api_key:
            raise ValueError(f"{provider} transcription needs an API key "
                             f"({'GROQ_API_KEY' if provider == 'groq' else 'OPENAI_API_KEY'})")
        self.name = provider
        self._key = api_key
        self.model = model
        self.parallel = parallel
        self.retries = retries           # 429 / 5xx retries with exponential backoff (honours Retry-After)
        self.label = "Groq Cloud" if provider == "groq" else "OpenAI Cloud"

    @property
    def cache_id(self) -> str:
        return f"{self.name}-{self.model}"

    def _one(self, path: str, span: tuple[float, float], vocab: str, language: str | None) -> list[dict]:
        s, e = span
        fields = {"model": self.model, "response_format": "verbose_json",
                  "timestamp_granularities[]": ["word", "segment"], "temperature": "0"}
        if language:
            fields["language"] = language
        if vocab:
            fields["prompt"] = vocab[:600]
        res = post_multipart(ENDPOINTS[self.name], fields, {"file": ("audio.flac", _flac(path, s, e), "audio/flac")},
                             headers={"Authorization": f"Bearer {self._key}"}, retries=self.retries)
        words = []
        for w in res.get("words") or []:
            text = (w.get("word") or "").strip()
            if text:
                ws, we = float(w["start"]) + s, float(w["end"]) + s
                words.append({"w": text, "s": round(ws, 3), "e": round(max(we, ws + 0.06), 3), "p": 1.0})
        _punctuate(words, res.get("segments") or [], s)
        return words

    def transcribe_spans_partial(self, audio_path, spans, vocab="", language=None,
                                 progress: Callable[[float], None] | None = None) -> list[list[dict] | None]:
        """Like transcribe_spans, but a span that fails for good (rate limit after every retry, network)
        comes back as None instead of aborting the rest, so a caller can redo just those spans elsewhere."""
        done = [0]
        quota_hit = threading.Event()

        def job(span):
            if quota_hit.is_set():            # Groq already refused after all retries: don't hammer it
                out = None
            else:
                try:
                    out = self._one(audio_path, span, vocab, language)
                except ProviderError as e:
                    if e.status == 429:
                        quota_hit.set()
                    log.warning("cloud transcription span failed", provider=self.name, status=e.status)
                    out = None
            done[0] += 1
            if progress:
                progress(done[0] / len(spans))
            return out

        with ThreadPoolExecutor(max(1, min(self.parallel, len(spans) or 1))) as ex:
            return list(ex.map(job, spans))

    def transcribe_spans(self, audio_path, spans, vocab="", language=None,
                         progress: Callable[[float], None] | None = None):
        out = self.transcribe_spans_partial(audio_path, spans, vocab, language, progress)
        if any(o is None for o in out):
            raise ProviderError(f"{self.name} transcription failed for {sum(o is None for o in out)} of {len(out)} parts")
        return out


def _punctuate(words: list[dict], segments: list[dict], offset: float) -> None:
    """The word list comes without punctuation; copy sentence-ending marks from segment text
    onto the last word of each segment so sentence boundaries survive."""
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text or text[-1] not in ".?!":
            continue
        end = float(seg.get("end", 0)) + offset
        inside = [w for w in words if w["s"] < end + 0.05]
        if inside and inside[-1]["w"][-1:] not in ".?!":
            inside[-1]["w"] += text[-1]
