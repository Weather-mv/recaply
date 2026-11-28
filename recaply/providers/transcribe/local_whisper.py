"""
faster-whisper on this PC (the default). Requested spans are joined into
buffers of up to ~20 minutes with silence between them and run through
faster-whisper's batched pipeline, which keeps the GPU (or every CPU core)
busy instead of paying model overhead per window, while memory stays bounded
(~80 MB of audio per buffer) for multi-hour inputs. The model is loaded once
per call. Words are mapped back to their span by position.

Model: large-v3-turbo on NVIDIA GPUs, `small` on CPU (both overridable).
"""

from __future__ import annotations

import gc
import threading
from typing import Callable

import numpy as np

from recaply.providers.transcribe.base import Transcriber
from recaply.providers.transcribe.guard import drop_hallucinations, suspect_segment
from recaply.settings import settings

SR = 16000
GAP_SEC = 1.5
GROUP_SEC = 1200.0
_LOCK = threading.Lock()        # one funnel model at a time


def _groups(spans: list[tuple[float, float]]) -> list[list[int]]:
    out, cur, size = [], [], 0.0
    for i, (s, e) in enumerate(spans):
        if cur and size + (e - s) > GROUP_SEC:
            out.append(cur)
            cur, size = [], 0.0
        cur.append(i)
        size += e - s + GAP_SEC
    if cur:
        out.append(cur)
    return out


class LocalWhisper(Transcriber):
    name = "faster-whisper"

    def __init__(self, model: str | None = None):
        self._model_override = model
        self._device = None

    def _pick(self):
        if self._device is None:
            import captions
            self._device = captions._device()     # (device, compute type), CUDA when usable
        dev, ctype = self._device
        model = self._model_override or (settings.whisper_model_gpu if dev == "cuda" else settings.whisper_model_cpu)
        return model, dev, ctype

    @property
    def cache_id(self) -> str:
        return f"fw-{self._pick()[0]}"

    def _load(self):
        """The shared, warm model (captions.get_model); CUDA failing falls back to the CPU loudly."""
        import captions
        name, dev, ctype = self._pick()
        model, got_dev, got_ctype = captions.get_model(name, dev, ctype)
        if got_dev != dev:
            self._device = (got_dev, got_ctype)
            if self._model_override is None and name != settings.whisper_model_cpu:
                del model                                   # the GPU-sized model on a CPU is too slow: use the CPU one
                captions.release()
                model, got_dev, _ = captions.get_model(settings.whisper_model_cpu, got_dev, got_ctype)
        return model, got_dev

    def transcribe_spans(self, audio_path, spans, vocab="", language=None,
                         progress: Callable[[float], None] | None = None):
        from faster_whisper import BatchedInferencePipeline

        import captions
        if not spans:
            return []
        out: list[list[dict]] = [[] for _ in spans]
        total = sum(e - s for s, e in spans) or 1.0
        done_sec = 0.0
        gap = np.zeros(int(GAP_SEC * SR), np.float32)
        with _LOCK, captions.LOCK:           # the editor's captions use the same GPU
            model, dev = self._load()
            try:
                pipe = BatchedInferencePipeline(model)
                for group in _groups(spans):
                    pieces, offsets, pos = [], [], 0
                    for i in group:
                        s, e = spans[i]
                        a = captions.load_audio(audio_path, s, e)
                        offsets.append((pos / SR, (pos + len(a)) / SR, s, i))
                        pieces += [a, gap]
                        pos += len(a) + len(gap)
                    audio = np.concatenate(pieces)
                    segs, _info = pipe.transcribe(
                        audio, language=language or None, word_timestamps=True,
                        batch_size=8 if dev == "cuda" else 4, vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
                        hotwords=(vocab or "")[:900] or None, condition_on_previous_text=False)
                    for seg in segs:
                        if progress:
                            progress(min(1.0, (done_sec + seg.end) / total))
                        if suspect_segment(seg):          # invented on music / noise / silence: skip it
                            continue
                        for w in seg.words or []:
                            text = w.word.strip()
                            if not text:
                                continue
                            for a, b, base, i in offsets:
                                if a <= w.start < b:
                                    s = base + (w.start - a)
                                    e = base + min(w.end, b) - a
                                    e = min(e, s + max(0.25, 0.09 * len(text) + 0.35))
                                    out[i].append({"w": text, "s": round(s, 3), "e": round(max(e, s + 0.06), 3),
                                                   "p": round(float(w.probability), 3)})
                                    break
                    done_sec += sum(spans[i][1] - spans[i][0] for i in group)
                    del audio, pieces
                return [drop_hallucinations(ws) for ws in out]
            finally:
                del model
                captions.release()
