"""Phase 1: story beats, the Ollama provider, lazy game plugins and the queue fallback."""

import dataclasses
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from recaply.profiles import get_profile
from recaply.profiles.plugins import has_plugin, lazy_module
from recaply.providers.llm import LLM, Ollama, Usage, ollama_status
from recaply.score import beats as B
from recaply.telemetry import Telemetry


def speech(n_sentences=40, per=8, start=0.0):
    """n sentences of `per` words, 0.4 s per word, 1 s pause between sentences."""
    words, t = [], start
    for s in range(n_sentences):
        for i in range(per):
            w = f"w{s}_{i}" + ("." if i == per - 1 else "")
            words.append({"w": w, "s": round(t, 2), "e": round(t + 0.3, 2)})
            t += 0.4
        t += 1.0
    return words


# ---------- parsing

@pytest.mark.parametrize("raw,sec", [(83.5, 83.5), ("83.5", 83.5), ("1:23", 83.0), ("1:23.5", 83.5),
                                     ("01:02:03", 3723.0), (" 12s ", 12.0), ("", None), ("soon", None), (True, None)])
def test_parse_time(raw, sec):
    assert B.parse_time(raw) == sec


def test_parse_beats_validates_and_clamps():
    data = {"beats": [
        {"start_time": "10", "end_time": "20", "type": "PAYOFF", "virality_score": 150, "reasoning": "big"},
        {"start_time": "0:25", "end_time": "0:40", "type": "weird", "virality_score": 40, "reasoning": "x"},
        {"start_time": "5", "end_time": "5.1", "type": "hook", "virality_score": 50, "reasoning": "too short"},
        {"start_time": "nope", "end_time": "9", "type": "hook", "virality_score": 50},
        "junk",
        {"start_time": "1", "end_time": "999", "type": "setup", "virality_score": "bad"},
    ]}
    got = B.parse_beats(data, lo=8.0, hi=30.0)
    assert [(b.type, b.start, b.end, b.score) for b in got] == [("payoff", 10.0, 20.0, 100.0), ("build", 25.0, 30.0, 40.0)]


def test_parse_beats_accepts_a_bare_list():
    reply = {"items": [{"start_time": 1, "end_time": 5, "type": "hook", "virality_score": 7}]}
    assert len(B.parse_beats(reply, 0, 10)) == 1


# ---------- chunking

def test_chunks_cover_everything_overlap_and_break_on_sentences():
    words = speech(60)                                   # 480 words
    chunks = B.chunk_words(words, size=100, overlap=20)
    assert len(chunks) > 3
    assert chunks[0][0] is words[0] and chunks[-1][-1] is words[-1]
    assert {id(w) for c in chunks for w in c} == {id(w) for w in words}
    for a, b in zip(chunks, chunks[1:]):
        assert b[0]["s"] > a[0]["s"]                     # always moves forward
        assert b[0]["s"] <= a[-1]["e"]                   # overlapping, no gap
    assert all(c[-1]["w"].endswith(".") for c in chunks)  # ends on a sentence edge
    assert all(c[0]["w"].endswith("_0") for c in chunks)  # starts on a sentence start


def test_short_or_empty_speech_is_one_or_no_chunk():
    assert B.chunk_words([]) == []
    assert len(B.chunk_words(speech(3))) == 1


def test_merge_keeps_the_better_of_overlapping_beats():
    a = B.Beat(10, 20, "build", 30, "a")
    b = B.Beat(11, 21, "payoff", 80, "b")
    c = B.Beat(40, 50, "hook", 55, "c")
    assert [x.reason for x in B.merge([a, b, c])] == ["b", "c"]


def test_beat_score_prefers_peak_beats():
    assert B.beat_score([{"type": "setup", "score": 90}, {"type": "payoff", "score": 60}]) == 60.0
    assert B.beat_score([{"type": "setup", "score": 90}]) == 90.0
    assert B.beat_score([]) is None


def test_clip_beats_are_cut_to_the_clip():
    beats = [{"start": 0, "end": 10, "type": "setup", "score": 1, "reason": ""},
             {"start": 10, "end": 20, "type": "payoff", "score": 9, "reason": ""},
             {"start": 30, "end": 40, "type": "tangent", "score": 1, "reason": ""}]
    out = B.clip_beats(beats, 5, 15)
    assert [(b["type"], b["start"], b["end"]) for b in out] == [("setup", 5, 10), ("payoff", 10, 15)]


# ---------- the beat pass with a fake model

class BeatLLM(LLM):
    provider = "fake"
    supports_schema = True

    def __init__(self, fail=False):
        super().__init__("fake-1")
        self.calls, self.schemas, self.fail = 0, [], fail

    def complete_json(self, system, prompt, max_tokens=8000, schema=None):
        self.calls += 1
        self.schemas.append(schema)
        if self.fail:
            from recaply.providers.http import ProviderError
            raise ProviderError("boom", 500, True)
        first = float(prompt.split("runs from ")[1].split("s to")[0])
        return {"beats": [{"start_time": f"{first + 1:.1f}", "end_time": f"{first + 6:.1f}", "type": "payoff",
                           "virality_score": 85, "reasoning": "big"}]}, Usage("fake", "fake-1", 100, 20)


def test_score_beats_runs_per_chunk_with_a_schema():
    llm, tel = BeatLLM(), Telemetry()
    out = B.score_beats(speech(60), llm, get_profile("gaming"), "t", tel)   # 480 words > one 350-word chunk
    assert llm.calls >= 2 and all(s is B.SCHEMA for s in llm.schemas)
    assert out and all(b["type"] == "payoff" and b["score"] == 85 for b in out)
    assert tel.summary()["tokens"]["fake"]["calls"] == llm.calls


def test_score_beats_skips_failed_chunks_and_never_raises():
    assert B.score_beats(speech(60), BeatLLM(fail=True), get_profile("gaming"), "t") == []
    assert B.score_beats([], BeatLLM(), get_profile("gaming"), "t") == []


# ---------- Ollama over real HTTP (a throwaway local server)

class _Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen2.5:3b-instruct"}]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append(req)
        out = {"message": {"content": json.dumps({"beats": []})}, "prompt_eval_count": 11, "eval_count": 3}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


@pytest.fixture()
def fake_ollama():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Handler.seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ollama_sends_schema_ctx_and_keep_alive(fake_ollama):
    llm = Ollama(fake_ollama, "qwen2.5:3b-instruct", num_ctx=4096, keep_alive="1m")
    data, usage = llm.complete_json("sys", "hi", schema=B.SCHEMA)
    req = _Handler.seen[0]
    assert req["format"] == B.SCHEMA and req["keep_alive"] == "1m" and req["options"]["num_ctx"] == 4096
    assert req["stream"] is False and data == {"beats": []} and (usage.tokens_in, usage.tokens_out) == (11, 3)
    llm.complete_json("sys", "hi")
    assert _Handler.seen[1]["format"] == "json"


def test_ollama_status(fake_ollama):
    ok = ollama_status(fake_ollama, "qwen2.5:3b-instruct")
    assert ok["running"] and ok["model_ready"] and ok["hint"] == ""
    missing = ollama_status(fake_ollama, "llama3.2:3b")
    assert missing["running"] and not missing["model_ready"] and "ollama pull llama3.2:3b" in missing["hint"]
    down = ollama_status("http://127.0.0.1:9", "x", timeout=0.5)
    assert not down["running"] and "ollama pull x" in down["hint"]


# ---------- game plugins load only when used

def test_lazy_module_defers_the_import():
    sys.modules.pop("recaply.profiles.valorant.round_detector", None)
    lazy = lazy_module("recaply.profiles.valorant.round_detector")
    assert "recaply.profiles.valorant.round_detector" not in sys.modules
    assert callable(lazy.segment_rounds)
    assert "recaply.profiles.valorant.round_detector" in sys.modules


def test_has_plugin():
    assert has_plugin("valorant_killfeed") and has_plugin("valorant_rounds") and not has_plugin("nope")


def test_non_valorant_profiles_need_no_game_plugin():
    for p in ("talk", "gaming", "fps", "open_world", "irl"):
        prof = get_profile(p)
        assert prof.segmenter is None and not any(d.startswith("valorant") for d in prof.detectors)


# ---------- queue

def test_rq_backend_falls_back_to_threads_when_redis_is_down(monkeypatch):
    from recaply.jobs import queue as Q
    monkeypatch.setattr(Q, "settings", dataclasses.replace(Q.settings, queue_backend="rq",
                                                           redis_url="redis://127.0.0.1:9/0"))
    monkeypatch.setattr(Q, "_queue", None)
    assert isinstance(Q.get_queue(), Q.LocalQueue)
    assert Q.queue_info()["backend"] == "local"


# ---------- the whole pipeline with a fake local model

def test_analyze_adds_story_beats_and_survives_a_dead_beat_model(monkeypatch):
    import re

    from recaply import engine
    from recaply.providers.transcribe.base import Transcriber
    from tests.conftest import FIXTURES

    class FakeTranscriber(Transcriber):
        name = "fake"

        def transcribe_spans(self, audio_path, spans, vocab="", language=None, progress=None):
            out = []
            for a, b in spans:
                out.append([{"w": w, "s": round(a + 9 + i * 0.5, 2), "e": round(a + 9.4 + i * 0.5, 2)}
                            for i, w in enumerate("No way. That was insane. Wow.".split())])
            return out

    class FakeLocalLLM(LLM):
        provider = "fake"
        supports_schema = True
        beats_ok = True

        def __init__(self):
            super().__init__("fake-1")

        def complete_json(self, system, prompt, max_tokens=8000, schema=None):
            if "Candidates follow" in prompt:
                ids = [int(i) for i in re.findall(r'<candidate id="(\d+)"', prompt)]
                starts = dict(re.findall(r'id="(\d+)" start="([\d.]+)"', prompt))
                ends = dict(re.findall(r'id="(\d+)" start="[\d.]+" end="([\d.]+)"', prompt))
                return {"clips": [{"id": i, "score": 80, "start": float(starts[str(i)]), "end": float(ends[str(i)]),
                                   "reason": "r", "title": "t", "hook": "h"} for i in ids]}, Usage("fake", "f", 10, 2)
            if not self.beats_ok:
                from recaply.providers.http import ProviderError
                raise ProviderError("down", 500, True)
            a = float(prompt.split("runs from ")[1].split("s to")[0])
            return {"beats": [{"start_time": str(a), "end_time": str(a + 12), "type": "payoff",
                               "virality_score": 100, "reasoning": "big"}]}, Usage("fake", "f", 10, 2)

    llm = FakeLocalLLM()
    monkeypatch.setattr(engine, "llm_chain", lambda name=None, note=None: llm)
    monkeypatch.setattr(engine, "get_transcriber", lambda name=None, note=None: FakeTranscriber())
    bursts = f"{FIXTURES}/bursts.mp4"
    opts = engine.Options(profile="gaming", count=3, min_len=12, max_len=25)

    res = engine.analyze(bursts, opts)
    assert res["scorer"] == "llm:fake" and res["clips"]
    top = res["clips"][0]
    assert top["beats"] and top["beats"][0]["type"] == "payoff" and top["beat_score"] == 100.0
    monkeypatch.setattr(engine, "settings", dataclasses.replace(engine.settings, story_beats=False))
    plain = {c["id"]: c for c in engine.analyze(bursts, opts)["clips"]}[top["id"]]
    monkeypatch.undo()                                      # (re-applied below) beats off: no labels, no nudge
    assert plain["beats"] == [] and top["score"] == round(0.8 * plain["score"] + 0.2 * top["beat_score"], 1)
    monkeypatch.setattr(engine, "llm_chain", lambda name=None, note=None: llm)
    monkeypatch.setattr(engine, "get_transcriber", lambda name=None, note=None: FakeTranscriber())
    assert all(b["start"] >= top["start"] - 0.01 and b["end"] <= top["end"] + 0.01 for b in top["beats"])
    assert "beats" in res["summary"]["stages"]

    llm.beats_ok = False                                   # the beat pass fails: clips still come back, scored as before
    res2 = engine.analyze(bursts, opts)
    assert res2["clips"] and all(c["beats"] == [] and c["beat_score"] is None for c in res2["clips"])


def test_rq_queue_enqueues_and_a_worker_runs_the_job(monkeypatch):
    """The Redis path end to end, on an in-memory Redis: submit -> worker (SimpleWorker, no fork) -> job finished."""
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("rq")
    import redis
    from rq import Queue, SimpleWorker

    from recaply.jobs import queue as Q
    from recaply.jobs import runner, store

    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: fakeredis.FakeStrictRedis(server=server)))
    store.init()
    ran = []
    monkeypatch.setattr(runner, "run_task", lambda job_id, task="analyze": ran.append((job_id, task)))

    q = Q.RQQueue("redis://fake/0")
    jid = store.create("https://youtu.be/abc", {}, priority=0)
    q.submit(jid, "analyze")
    assert q.info()["waiting"] == 1 and q.info()["backend"] == "rq"
    assert store.get(jid)["state"] == "queued"

    conn = fakeredis.FakeStrictRedis(server=server)
    SimpleWorker([Queue("recaply", connection=conn)], connection=conn).work(burst=True)
    assert ran == [(jid, "analyze")] and q.info()["waiting"] == 0
