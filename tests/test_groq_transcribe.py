"""Groq transcription: parallel spans, 429 backoff, local fallback, config."""
from types import SimpleNamespace

from recaply.providers.http import ProviderError
from recaply.providers.transcribe import groq as groq_mod
from recaply.providers.transcribe.cloud_whisper import CloudWhisper


class FakeGroq(CloudWhisper):
    def __init__(self, fail_at=()):
        super().__init__("groq", "k", "whisper-large-v3")
        self.fail_at = set(fail_at)

    def _one(self, path, span, vocab, language):
        if span[0] in self.fail_at:
            raise ProviderError("HTTP 429", 429, True)
        return [{"w": f"g{int(span[0])}", "s": span[0], "e": span[0] + 0.5, "p": 1.0}]


class FakeLocal:
    name = "faster-whisper"
    cache_id = "fw-cpu"

    def transcribe_spans(self, path, spans, vocab="", language=None, progress=None):
        return [[{"w": f"l{int(s)}", "s": s, "e": s + 0.5, "p": 1.0}] for s, _ in spans]


SPANS = [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)]


def test_all_spans_via_groq():
    t = groq_mod.GroqWithFallback(FakeGroq(), FakeLocal)
    out = t.transcribe_spans("a.wav", SPANS)
    assert [o[0]["w"] for o in out] == ["g0", "g30", "g60"]
    assert t.label == "Groq Cloud"


def test_429_spans_fall_back_to_local_and_fill_gaps():
    notes = []
    t = groq_mod.GroqWithFallback(FakeGroq(fail_at=[30.0]), FakeLocal, notes.append)
    out = t.transcribe_spans("a.wav", SPANS)
    words = [o[0]["w"] for o in out]       # after a 429 Groq is left alone, so later parts may go local too
    assert words[1] == "l30" and len(words) == 3 and all(w[0] in "gl" for w in words)
    assert t.label == "Local GPU/CPU" and notes and "rate limit" in notes[0]


def test_no_key_uses_local(monkeypatch):
    notes = []
    s = SimpleNamespace(groq_api_key="", groq_model="m", groq_parallel=2, groq_retries=1)
    monkeypatch.setattr("recaply.providers.transcribe.local_whisper.LocalWhisper", FakeLocal)
    t = groq_mod.build(s, notes.append)
    assert t.groq is None and t.label == "Local GPU/CPU"
    assert t.transcribe_spans("a.wav", SPANS)[1][0]["w"] == "l30"
    assert any("GROQ_API_KEY" in n for n in notes)


def test_http_layer_backs_off_on_429(monkeypatch):
    import io
    import urllib.error

    from recaply.providers import http
    calls, sleeps = [], []

    def fake_open(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"slow down"))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    try:
        http.post_multipart("http://x", {}, {}, retries=3)
        assert False
    except ProviderError as e:
        assert e.status == 429
    assert len(calls) == 4 and sleeps == [2.0, 4.0, 8.0]


def test_default_provider_is_groq(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
    monkeypatch.delenv("RECAPLY_TRANSCRIBER", raising=False)
    from recaply.settings import Settings
    assert Settings().transcriber == "groq"
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "local")
    assert Settings().transcriber == "local"
