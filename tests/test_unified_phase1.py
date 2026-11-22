"""Unified plan, Phase 1: LLM fallback chain + Groq, 4-axis virality, Whisper guard, cookies, job stages."""

import dataclasses
import json
from types import SimpleNamespace

import pytest

import pipeline
from recaply.jobs import runner, store
from recaply.jobs import stages as S
from recaply.profiles import get_profile
from recaply.providers import llm as L
from recaply.providers.http import ProviderError
from recaply.providers.transcribe import guard as G
from recaply.score import rerank as R
from recaply.score.windows import Window
from recaply.telemetry import Telemetry


class Fake(L.LLM):
    def __init__(self, provider, reply=None, error=None):
        super().__init__(f"{provider}-model")
        self.provider = provider
        self.reply = reply if reply is not None else {"ok": provider}
        self.error = error
        self.calls = 0

    def complete_json(self, system, prompt, max_tokens=8000):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.reply), L.Usage(self.provider, self.model, 1, 1)


# ---------- LLM chain

def test_chain_falls_back_and_stops_asking_a_dead_provider():
    notes = []
    groq = Fake("groq", error=ProviderError("HTTP 429: slow down", 429, True))
    gemini = Fake("gemini")
    chain = L.LLMChain([groq, gemini], notes.append)
    assert chain.provider == "groq"                                   # before any answer: who goes first
    for _ in range(3):
        data, usage = chain.complete_json("s", "p")
        assert data == {"ok": "gemini"} and usage.provider == "gemini"
    assert groq.calls == 1 and gemini.calls == 3                      # 429 after retries: skipped from then on
    assert chain.provider == "gemini" and "groq" in chain.failures
    assert len(notes) == 1 and "groq unavailable" in notes[0] and "switched to gemini" in notes[0]


def test_chain_bad_reply_skips_once_but_keeps_the_provider():
    flaky = Fake("groq", error=ProviderError("model reply wasn't JSON: 'hm'"))
    chain = L.LLMChain([flaky, Fake("ollama")])
    assert chain.complete_json("s", "p")[0] == {"ok": "ollama"}
    flaky.error = None
    assert chain.complete_json("s", "p")[0] == {"ok": "groq"}         # asked again next time
    assert chain.failures == {}


def test_chain_all_failing_raises_so_the_caller_uses_rules():
    chain = L.LLMChain([Fake("groq", error=ProviderError("HTTP 401: bad key", 401)),
                        Fake("ollama", error=OSError("refused"))])
    with pytest.raises(ProviderError, match="every AI provider failed"):
        chain.complete_json("s", "p")
    with pytest.raises(ProviderError):                                # both dead now: fails at once
        chain.complete_json("s", "p")


def test_chain_forwards_a_schema_only_to_providers_that_take_one():
    seen = {}

    class Schemaful(Fake):
        supports_schema = True

        def complete_json(self, system, prompt, max_tokens=8000, schema=None):
            seen["schema"] = schema
            return {"ok": 1}, L.Usage("ollama", "m")

    chain = L.LLMChain([Fake("groq", error=ProviderError("HTTP 500", 500, True)), Schemaful("ollama")])
    assert chain.supports_schema
    chain.complete_json("s", "p", 100, schema={"type": "object"})
    assert seen["schema"] == {"type": "object"}


def _settings(monkeypatch, **kw):
    monkeypatch.setattr(L, "settings", dataclasses.replace(L.settings, **kw))


def test_llm_chain_order_keys_and_ollama(monkeypatch):
    up = {"running": True, "model": "m", "model_ready": True, "models": ["m"], "hint": ""}
    down = {"running": False, "model": "m", "model_ready": False, "models": [], "hint": "Ollama isn't running."}
    monkeypatch.setattr(L, "ollama_status", lambda *a, **k: up)
    _settings(monkeypatch, llm_provider="auto", llm_order="", groq_api_key="g", gemini_api_key="k",
              anthropic_api_key="", openai_api_key="")
    assert [m.provider for m in L.llm_chain().llms] == ["groq", "gemini", "ollama"]
    assert [m.provider for m in L.llm_chain("ollama").llms] == ["ollama", "groq", "gemini"]   # named one first
    _settings(monkeypatch, llm_provider="auto", llm_order="ollama, gemini", groq_api_key="g", gemini_api_key="k")
    assert [m.provider for m in L.llm_chain().llms] == ["ollama", "gemini"]                  # RECAPLY_LLM_ORDER

    monkeypatch.setattr(L, "ollama_status", lambda *a, **k: down)
    notes = []
    _settings(monkeypatch, llm_provider="auto", llm_order="", groq_api_key="", gemini_api_key="",
              anthropic_api_key="", openai_api_key="")
    assert L.llm_chain(note=notes.append) is None and "Ollama isn't running" in notes[0]
    assert L.llm_chain("none", note=notes.append) is None and "switched off" in notes[1]

    notes.clear()
    _settings(monkeypatch, llm_provider="ollama", llm_order="", groq_api_key="g")
    chain = L.llm_chain(note=notes.append)
    assert [m.provider for m in chain.llms] == ["groq"] and "ollama isn't available" in notes[0]


def test_ai_status_reads_ready_when_a_cloud_provider_leads(monkeypatch):
    monkeypatch.setattr(L, "ollama_status", lambda *a, **k: {"running": False, "model": "m", "model_ready": False,
                                                           "models": [], "hint": "start ollama"})
    _settings(monkeypatch, llm_provider="auto", llm_order="", groq_api_key="g", gemini_api_key="",
              anthropic_api_key="", openai_api_key="")
    st = L.ai_status()
    assert st["provider"] == "groq" and st["order"] == ["groq"] and st["ready"]
    assert st["ollama"]["running"] and st["ollama"]["hint"] == ""
    _settings(monkeypatch, groq_api_key="")
    st = L.ai_status()
    assert st["provider"] == "ollama" and not st["ready"] and st["ollama"]["hint"] == "start ollama"


def test_groq_provider_request(monkeypatch):
    sent = {}

    def fake_post(url, body, headers=None, **kw):
        sent.update(url=url, body=body, headers=headers)
        return {"choices": [{"message": {"content": '{"clips": []}'}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    monkeypatch.setattr(L, "post_json", fake_post)
    data, usage = L.Groq("secret", "llama-3.3-70b-versatile").complete_json("sys", "hi")
    assert data == {"clips": []} and (usage.provider, usage.tokens_in, usage.tokens_out) == ("groq", 7, 3)
    assert sent["url"].startswith("https://api.groq.com/openai/v1/chat/completions")
    assert sent["headers"]["Authorization"] == "Bearer secret" and "secret" not in sent["url"]
    assert sent["body"]["response_format"] == {"type": "json_object"}


# ---------- 4-axis virality

def test_virality_is_recomputed_in_code():
    item = {"hook_score": 9, "engagement_score": 7, "value_score": 8, "shareability_score": 6}
    assert R.virality(item)["overall_virality"] == 76.5                       # (0.3*9 + 0.25*7 + 0.25*8 + 0.2*6) x 10
    assert R.virality({**item, "overall_virality": 80})["overall_virality"] == round((76.5 + 80) / 2, 1)
    assert R.virality({**item, "overall_virality": 99})["overall_virality"] == 76.5          # far from its own axes: ignored
    assert R.virality({**item, "hook_score": 42})["hook_score"] == 10.0                      # clamped
    assert R.virality({"score": 80}) is None and R.virality({**item, "value_score": "x"}) is None


def _windows():
    words = [{"w": "what", "s": 105.0, "e": 105.3}, {"w": "a", "s": 105.4, "e": 105.5}, {"w": "play.", "s": 105.6, "e": 106.0}]
    return [Window(100, 160, 120, 0.5, text_score=0.6, words=words, signals={"audio_energy": 0.9}),
            Window(500, 560, 520, 0.8)]


def test_rerank_returns_the_four_axes():
    reply = {"clips": [{"id": 0, "hook_score": 8, "engagement_score": 8, "value_score": 8, "shareability_score": 8,
                        "overall_virality": 80, "start": 104, "end": 130, "title": "T"},
                       {"id": 1, "score": 55, "start": 500, "end": 530}]}               # old-style reply still works
    out, used = R.rerank(_windows(), get_profile("gaming"), "t", Fake("groq", reply=reply), Telemetry())
    assert used == "llm:groq"
    assert out[0]["virality"]["overall_virality"] == 80.0 and out[0]["llm_score"] == 80.0
    assert out[1]["virality"] is None and out[1]["llm_score"] == 55.0


def test_rule_based_scores_have_axes_too():
    out, used = R.rerank(_windows(), get_profile("gaming"), "t", None)
    assert used == "heuristic"
    for o in out:
        v = o["virality"]
        assert set(v) == {*R.AXES, "overall_virality"} and v["overall_virality"] == o["score"]
        assert all(0 <= v[k] <= 10 for k in R.AXES)


# ---------- Whisper guard

def _w(tokens, p=0.9, t0=0.0):
    return [{"w": t, "s": t0 + i * 0.4, "e": t0 + i * 0.4 + 0.3, "p": p} for i, t in enumerate(tokens)]


def test_repetition_loops_are_dropped_real_speech_kept():
    loop = _w(["आप"] * 12)
    assert G.drop_hallucinations(loop) == []
    real = _w("no no no that was close".split())
    assert G.drop_hallucinations(real) == real                                 # 3 repeats is speech
    mixed = _w(["okay", "let's", "go"]) + _w(["thank", "you."] * 5, t0=2) + _w(["nice", "shot"], t0=8)
    assert [w["w"] for w in G.drop_hallucinations(mixed)] == ["okay", "let's", "go", "nice", "shot"]


def test_long_low_confidence_runs_are_dropped():
    words = _w(["real", "words"]) + _w(list("abcdefg"), p=0.05, t0=1) + _w(["back"], t0=5)
    assert [w["w"] for w in G.drop_hallucinations(words)] == ["real", "words", "back"]
    short = _w(["real"]) + _w(["um", "uh"], p=0.05, t0=1)
    assert G.drop_hallucinations(short) == short


def test_suspect_segment():
    ok = SimpleNamespace(compression_ratio=1.4, no_speech_prob=0.1, avg_logprob=-0.3)
    assert not G.suspect_segment(ok)
    assert G.suspect_segment(SimpleNamespace(compression_ratio=3.1, no_speech_prob=0.0, avg_logprob=-0.2))
    assert G.suspect_segment(SimpleNamespace(compression_ratio=1.0, no_speech_prob=0.9, avg_logprob=-1.4))
    assert not G.suspect_segment(SimpleNamespace())                            # unknown fields: kept


# ---------- cookies

def test_netscape_cookies_repairs_and_rejects():
    good = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tabc\n"
    assert pipeline.netscape_cookies(good) == (good, None)
    spaced, why = pipeline.netscape_cookies(".youtube.com TRUE / TRUE 0 PREF f=1\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tX\ty\n")
    assert why is None and spaced.startswith(pipeline.NETSCAPE_HEADER + "\n")       # header always written
    assert ".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf=1" in spaced and "#HttpOnly_.youtube.com" in spaced
    clean, why = pipeline.netscape_cookies("<html>not cookies</html>")
    assert clean is None and "isn't in the Netscape" in why


def test_each_yt_dlp_call_gets_its_own_cookie_copy(tmp_path, monkeypatch):
    f = tmp_path / "cookies.txt"
    f.write_text(json.dumps([{"domain": ".youtube.com", "name": "SID", "value": "v", "path": "/",
                              "secure": True, "expirationDate": 1999999999}]), encoding="utf-8")
    monkeypatch.setattr(pipeline, "COOKIES_FILE", str(f))
    monkeypatch.setitem(pipeline._cookies, "mtime", None)
    a, b = pipeline.cookie_copy(), pipeline.cookie_copy()
    assert a != b and open(a, encoding="utf-8").readline().strip() == pipeline.NETSCAPE_HEADER
    assert pipeline.cookie_problem() == ""
    f.write_text("garbage", encoding="utf-8")
    import os
    os.utime(f, (1, 1))                                                  # a different mtime: re-read
    assert pipeline.cookie_copy() is None and "being ignored" in pipeline.cookie_problem()


# ---------- job stages

def test_stage_mapping_and_tracker():
    assert S.stage_for("Downloading & Extracting Audio...") == "download"
    assert S.stage_for("Analyzing Audio Energy (Finding Hype Moments)...") == "analyze"
    assert S.stage_for("Transcribing Moments (Groq Cloud)...") == "transcribe"
    assert S.stage_for("Story Beat Analysis (Groq)...") == "score" and S.stage_for("Scoring with AI") == "score"
    assert S.stage_for("Done") is None
    t = S.StageTracker()
    assert t.progress("Downloading & Extracting Audio...", 0.11)
    assert t.stages["download"]["progress"] == 0.5
    assert t.progress("Transcribing Moments...", 0.34)                  # analyze jumped over: skipped
    assert t.stages["download"]["state"] == "done" and t.stages["analyze"]["state"] == "skipped"
    where = t.fail("Groq refused")
    assert where == "transcribe" and t.failed_stage == "transcribe"
    assert t.log[-1]["level"] == "error" and t.log[-1]["stage"] == "transcribe"
    pub = S.public({"stages": t.stages})
    assert [s["key"] for s in pub["stages"]] == list(S.STAGES) and pub["failed_stage"] == "transcribe"
    assert S.public({})["stages"][0]["state"] == "pending"              # jobs made before stages existed


def test_store_adds_the_new_columns_to_an_old_database(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as con:
        con.executescript(store.SCHEMA)
    store.init(path)
    with sqlite3.connect(path) as con:
        cols = {r[1] for r in con.execute("pragma table_info(clip_jobs)")}
    assert {"stages", "log"} <= cols
    store.init(path)                                                     # idempotent


def test_a_failed_analysis_records_where_and_why(monkeypatch):
    store.init()

    def boom(source, opts, progress=None, telemetry=None):
        progress("Downloading & Extracting Audio...", 0.1)
        telemetry.note("chat replay not read: nope")
        progress("Transcribing Moments (Local GPU/CPU)...", 0.4)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(runner, "analyze", boom)
    jid = store.create("https://youtu.be/x", {})
    runner.run_task(jid, "analyze")
    job = store.get(jid)
    assert job["state"] == "failed" and job["stage"] == "Failed while transcribing"
    pub = S.public(job)
    assert pub["failed_stage"] == "transcribe" and pub["stages"][0]["state"] == "done"
    assert any(e["level"] == "warning" and e["stage"] == "download" and "chat replay" in e["msg"] for e in job["log"])
    assert job["log"][-1]["level"] == "error"
