"""Unified plan, Phase 2 backend: stages in the API, the free 5-second preview, and the Settings page's API."""

import dataclasses
import json
import os

import pytest
from flask import Flask

from recaply import user_settings as US
from recaply.jobs import runner, store
from recaply.jobs import stages as S
from test_studio_app import FakeAuth


@pytest.fixture
def client(monkeypatch, tmp_path):
    import clip_routes
    import studio_app
    store.init()
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "DESKTOP", True)
    monkeypatch.setattr(studio_app, "DIST", str(tmp_path / "dist"))
    submitted = []

    class Q:
        def submit(self, job_id, task="analyze", priority=0, refund=None):
            submitted.append((job_id, task))

    monkeypatch.setattr(clip_routes, "get_queue", lambda: Q())
    app = Flask(__name__)
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(studio_app.bp)
    c = app.test_client()
    c.submitted = submitted
    return c


def make_job(state="ready", **extra):
    jid = store.create("https://youtu.be/x", {}, owner=FakeAuth.me)
    result = {"source": {"kind": "url", "key": "k", "title": "T", "duration": 600.0, "location": "https://youtu.be/x"},
              "profile": {"key": "gaming", "label": "Gaming", "reason": "", "clip_min": 10, "clip_max": 60},
              "scorer": "llm:groq", "options": {},
              "clips": [{"id": 1, "start": 100.0, "end": 140.0, "duration": 40.0, "title": "A", "hook": "H",
                         "score": 80, "virality": {"hook_score": 8, "engagement_score": 7, "value_score": 7,
                                                   "shareability_score": 6, "overall_virality": 72.5}}]}
    store.update(jid, state=state, result=result, **extra)
    return jid


# ---------- stages in the API

def test_project_list_and_job_view_carry_the_stepper(client):
    t = S.StageTracker()
    t.progress("Transcribing Moments...", 0.5)
    jid = make_job(state="running", stage="Transcribing", stages=t.stages, log=[])
    row = next(p for p in client.get("/api/studio/projects").get_json()["projects"] if p["id"] == jid)
    assert [s["key"] for s in row["stages"]] == list(S.STAGES) and row["current"] == "transcribe"
    assert row["scorer"] == "llm:groq"
    view = client.get(f"/api/clipjob/{jid}").get_json()
    assert view["current"] == "transcribe" and view["result"]["clips"][0]["virality"]["overall_virality"] == 72.5


def test_log_endpoint_and_ownership(client):
    t = S.StageTracker()
    t.progress("Downloading", 0.05)
    t.add("warning", "chat replay not read")
    t.fail("boom")
    jid = make_job(state="failed", stages=t.stages, log=t.log, error="boom", summary={"notes": ["n1"]})
    data = client.get(f"/api/clipjob/{jid}/log").get_json()
    assert data["failed_stage"] == "download" and data["error"] == "boom" and data["notes"] == ["n1"]
    assert [e["level"] for e in data["log"]] == ["warning", "error"]
    assert client.get("/api/clipjob/0123456789/log").status_code == 404
    other = store.create("x", {}, owner=999)
    assert client.get(f"/api/clipjob/{other}/log").status_code == 404


# ---------- preview

def test_preview_route_queues_a_free_preview(client):
    jid = make_job()
    r = client.post(f"/api/clipjob/{jid}/preview", json={"id": 1, "options": {"layout": "face", "captions": "bold"}, "start": 110})
    assert r.status_code == 200 and client.submitted == [(jid, "preview")]
    job = store.get(jid)
    assert job["task_args"]["id"] == 1 and job["task_args"]["start"] == 110.0 and job["task_args"]["options"]["layout"] == "face"
    assert job["cuts"]["p1"]["state"] == "queued"
    assert client.post(f"/api/clipjob/{jid}/preview", json={"id": 99}).status_code == 404
    assert client.post(f"/api/clipjob/{jid}/preview", json={"id": "x"}).status_code == 400
    running = make_job(state="running")
    assert client.post(f"/api/clipjob/{running}/preview", json={"id": 1}).status_code == 409


def test_preview_task_records_the_file_and_never_fails_the_project(monkeypatch):
    jid = make_job()
    store.update(jid, task_args={"id": 1, "options": {}, "start": None, "end": None})
    seen = {}

    def fake(result, cid, out_dir, opts, start, end, owner=None, progress=None, telemetry=None):
        seen.update(cid=cid, opts=opts)
        progress(0.5, "Rendering")
        return {"file": "preview_short_01_9x16.mp4", "layout": "face", "duration": 5.0, "start": 100.0, "end": 105.0, "notes": []}

    monkeypatch.setattr(runner, "render_preview", fake)
    runner.run_task(jid, "preview")
    job = store.get(jid)
    assert job["state"] == "ready" and job["cuts"]["p1"]["state"] == "done" and job["cuts"]["p1"]["file"] == "preview_short_01_9x16.mp4"
    assert job["cuts"]["p1"]["t"] > 0 and not job.get("stages")           # the analysis stepper is untouched

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(runner, "render_preview", boom)
    runner.run_task(jid, "preview")
    job = store.get(jid)
    assert job["state"] == "ready" and job["cuts"]["p1"]["state"] == "failed" and "ffmpeg" in job["cuts"]["p1"]["error"]


def test_render_preview_trims_to_five_seconds_and_uses_the_draft_preset(monkeypatch, tmp_path):
    from recaply import engine
    from recaply.render import shorts
    calls = {}

    def fake_cut(result, ids, out_dir, quality="1080", source=None, progress=None, telemetry=None):
        c = result["clips"][0]
        calls["cut"] = (c["start"], c["end"], quality, os.path.basename(out_dir))
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, "c.mp4"), "wb").close()
        return {ids[0]: {"file": "c.mp4"}}

    def fake_render(clip, src, words, out_dir, opts, **kw):
        calls["render"] = (opts.export, opts.tighten, opts.broll, opts.translate, kw["tag"], kw["watermark"], clip["end"] - clip["start"])
        return {"file": "preview_short_01_9x16.mp4", "layout": "center", "duration": 5.0, "notes": []}

    monkeypatch.setattr(engine, "cut_clips", fake_cut)
    monkeypatch.setattr(engine, "clip_words", lambda *a, **k: [])
    monkeypatch.setattr(shorts, "render_short", fake_render)
    result = store.get(make_job())["result"]
    out = engine.render_preview(result, 1, str(tmp_path), {"export": "tiktok", "tighten": True, "broll": True, "translate": "es"},
                                start=110, end=400)
    assert calls["cut"] == (110.0, 115.0, "720", "_preview")                  # only 5 s are cut, at 720p, in its own folder
    assert calls["render"] == ("draft", False, False, "", "preview_", False, 5.0)
    assert out["start"] == 110.0 and out["end"] == 115.0
    with pytest.raises(Exception, match="isn't in this project"):
        engine.render_preview(result, 42, str(tmp_path))


# ---------- settings

@pytest.fixture
def user_settings(monkeypatch, tmp_path):
    from recaply import settings as settings_mod
    monkeypatch.setattr(US, "_path", lambda: str(tmp_path / "_user_settings.json"))
    monkeypatch.setattr(US, "_from_ui", set())
    fresh = dataclasses.replace(settings_mod.settings)
    for env in US.KEYS:
        monkeypatch.delenv(env, raising=False)
    return fresh


def test_settings_save_status_and_removal(user_settings, monkeypatch):
    assert US.update(user_settings, {"GROQ_API_KEY": "  gsk_secret  ", "RECAPLY_LLM_ORDER": "ollama,groq", "NOPE": "x"}) == {}
    assert user_settings.groq_api_key == "gsk_secret" and user_settings.llm_order == "ollama,groq"
    rows = {r["name"]: r for r in US.status()}
    assert rows["GROQ_API_KEY"]["set"] and rows["GROQ_API_KEY"]["source"] == "ui" and rows["GROQ_API_KEY"]["value"] == ""   # secrets never leave
    assert rows["RECAPLY_LLM_ORDER"]["value"] == "ollama,groq" and "NOPE" not in rows
    saved = json.load(open(US._path(), encoding="utf-8"))
    assert saved == {"GROQ_API_KEY": "gsk_secret", "RECAPLY_LLM_ORDER": "ollama,groq"}
    assert US.update(user_settings, {"GEMINI_API_KEY": "bad\nkey"}) == {"GEMINI_API_KEY": "That doesn't look like a valid value."}
    assert US.update(user_settings, {"GROQ_API_KEY": ""}) == {} and user_settings.groq_api_key == ""
    assert not {r["name"]: r for r in US.status()}["GROQ_API_KEY"]["set"]
    monkeypatch.setattr(US, "_from_ui", set())
    US.load_into_environment()                                              # the saved order is applied at start-up
    assert os.environ["RECAPLY_LLM_ORDER"] == "ollama,groq"
    monkeypatch.delenv("RECAPLY_LLM_ORDER")


def test_settings_api_never_returns_keys_and_is_local_only(client, user_settings, monkeypatch):
    import studio_app
    monkeypatch.setattr(studio_app, "settings", user_settings)
    r = client.post("/api/studio/settings", json={"set": {"GROQ_API_KEY": "gsk_topsecret"}})
    assert r.status_code == 200 and "gsk_topsecret" not in r.get_data(as_text=True)
    body = r.get_json()
    assert {s["name"]: s for s in body["settings"]}["GROQ_API_KEY"]["set"] and "groq" in body["providers"]
    assert "gsk_topsecret" not in client.get("/api/studio/settings").get_data(as_text=True)
    monkeypatch.setattr(studio_app, "DESKTOP", False)                        # a hosted copy: visitors can't read or change keys
    assert client.get("/api/studio/settings", environ_base={"REMOTE_ADDR": "203.0.113.5"}).status_code == 403
    assert client.post("/api/studio/settings/test", json={"provider": "groq"}, environ_base={"REMOTE_ADDR": "203.0.113.5"}).status_code == 403
    assert client.get("/api/studio/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_settings_test_button(client, user_settings, monkeypatch):
    import studio_app
    from recaply.providers import llm as L
    from recaply.providers.http import ProviderError
    monkeypatch.setattr(studio_app, "settings", user_settings)
    assert "Save an API key" in client.post("/api/studio/settings/test", json={"provider": "groq"}).get_json()["message"]

    class Fake(L.LLM):
        provider = "groq"

        def __init__(self, fail):
            super().__init__("m")
            self.fail = fail

        def complete_json(self, *a, **k):
            if self.fail:
                raise ProviderError("HTTP 401: invalid key", 401)
            return {"ok": True}, L.Usage("groq", "m")

    monkeypatch.setattr(L, "get_llm", lambda name=None: Fake(True))
    bad = client.post("/api/studio/settings/test", json={"provider": "groq"}).get_json()
    assert bad["ok"] is False and "401" in bad["message"]
    monkeypatch.setattr(L, "get_llm", lambda name=None: Fake(False))
    assert client.post("/api/studio/settings/test", json={"provider": "groq"}).get_json()["ok"] is True
    monkeypatch.setattr(studio_app, "ollama_status", lambda: {"running": False, "model": "m", "model_ready": False, "models": [], "hint": "start it"})
    assert client.post("/api/studio/settings/test", json={"provider": "ollama"}).get_json() == {"ok": False, "message": "start it"}
