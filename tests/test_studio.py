"""Usage dashboard, batch queue and the studio routes (editor, publish, queue, usage)."""

import json
import os
import shutil
import time

import pytest
from flask import Flask

from conftest import FIXTURES
from recaply import batch, publish, usage
from recaply.jobs import runner, store
from recaply.publish import queue as pq
from recaply.publish.base import Publisher, Result


def clean_jobs(owner):
    store.init()
    for j in store.list_jobs(owner=owner, limit=1000):
        store.delete(j["id"])


def make_job(owner, state="ready", duration=3600.0, total=120.0, usd=0.05, clips=3, created=None, tokens=None):
    jid = store.create("https://youtu.be/x" + os.urandom(3).hex(), {}, owner=owner, title="Job " + os.urandom(2).hex())
    summary = {"total_sec": total, "est_usd": usd, "tokens": tokens or {"gemini": {"in": 1_000_000, "out": 100_000, "calls": 4}},
               "audio_min": {"groq": 30.0},
               "stages": {"download": {"sec": 60.0, "runs": 1, "cached": False}, "score": {"sec": 20.0, "runs": 1, "cached": True}}}
    result = {"source": {"duration": duration, "key": "k", "kind": "url", "title": "t", "location": "x"},
              "clips": [{"id": i} for i in range(clips)]}
    store.update(jid, state=state, summary=summary, result=result)
    if created:
        with store._lock, store._db() as con:
            con.execute("update clip_jobs set created=? where id=?", (created, jid))
    return jid


# ---------- usage ----------

def test_usage_totals_and_rates():
    owner = 9001
    clean_jobs(owner)
    now = time.time()
    make_job(owner, duration=3600, total=100, usd=0.10, clips=4)
    make_job(owner, duration=7200, total=200, usd=0.20, clips=5, created=now - 86400 * 2)
    make_job(owner, state="failed", duration=0, total=5, usd=0.0, clips=0)
    make_job(owner, duration=1800, total=50, usd=1.0, created=now - 86400 * 60)                 # outside the 30 days
    d = usage.collect(owner=owner, days=30)
    t = d["totals"]
    assert t["jobs"] == 3 and t["ready"] == 2 and t["failed"] == 1 and t["clips"] == 9
    assert t["video_hours"] == 3.0 and t["usd"] == 0.3
    assert t["sec_per_video_hour"] == 100.0 and t["usd_per_video"] == 0.15 and t["usd_per_video_hour"] == 0.1
    assert len(d["by_day"]) == 2 and sum(x["jobs"] for x in d["by_day"]) == 3
    g = d["providers"]["gemini"]
    assert g["calls"] == 12 and g["tokens_in"] == 3_000_000 and g["usd"] > 0
    assert d["providers"]["groq (audio)"]["minutes"] == 90.0
    names = {s["name"]: s for s in d["stages"]}
    assert names["download"]["sec"] == 180.0 and abs(sum(s["share"] for s in d["stages"]) - 1) < 0.01
    assert usage.collect(owner=owner, days=90)["totals"]["jobs"] == 4
    assert usage.collect(owner=424242)["totals"]["jobs"] == 0                                   # other people's jobs don't show


def test_usage_with_no_jobs_has_no_division_errors():
    clean_jobs(9002)
    t = usage.collect(owner=9002)["totals"]
    assert t["jobs"] == 0 and t["sec_per_video_hour"] is None and t["usd_per_video"] is None


def test_later_tasks_add_their_cost_to_the_job_summary():
    from recaply.telemetry import Telemetry
    tel = Telemetry()
    tel.add_tokens("gemini", 1_000_000, 0)
    tel.add_audio("groq", 3600)
    with tel.stage("render"):
        pass
    base = {"est_usd": 0.05, "tokens": {"gemini": {"in": 500_000, "out": 10, "calls": 2}}, "audio_min": {"groq": 10.0}, "stages": {"x": {"sec": 1}}}
    m = runner.merge_summary(base, tel, "shorts")
    assert m["tokens"]["gemini"] == {"in": 1_500_000, "out": 10, "calls": 3} and m["audio_min"]["groq"] == 70.0
    assert m["est_usd"] > 0.05 + 0.3 and "render" in m["shorts"] and m["stages"] == {"x": {"sec": 1}}
    assert runner.merge_summary(None, Telemetry(), "cut")["est_usd"] == 0


# ---------- batch ----------

def test_parse_inputs_dedupes_skips_comments_and_caps():
    text = "# my list\nhttps://youtu.be/a\n\n  https://youtu.be/a  \n\"https://www.twitch.tv/videos/1\"\n" + "\n".join(f"https://youtu.be/{i}" for i in range(80))
    out = batch.parse_inputs(text)
    assert out[:2] == ["https://youtu.be/a", "https://www.twitch.tv/videos/1"] and len(out) == batch.MAX_BATCH


def test_check_lines(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    assert batch.check("https://www.youtube.com/watch?v=abc", False) is None
    assert "YouTube, Twitch and Kick" in batch.check("https://evil.example/x", False)
    assert "look right" in batch.check("https://www.youtube.com/a b", False)
    assert batch.check(str(f), False) == "Only links are accepted here."
    assert batch.check(str(f), True) is None
    assert "not found" in batch.check(str(tmp_path / "no.mp4"), True)


def test_start_batch_creates_jobs_and_reports_bad_lines(monkeypatch):
    queued = []

    class Q:
        def submit(self, jid, task="analyze", **kw):
            queued.append(jid)

    monkeypatch.setattr(batch, "get_queue", lambda: Q())
    res = batch.start_batch(["https://youtu.be/ok1", "https://evil.example/x", "https://youtu.be/ok2"], {"profile": "gaming", "count": 4}, owner=77)
    assert [("job" in r) for r in res] == [True, False, True] and queued == [res[0]["job"], res[2]["job"]]
    assert store.get(res[0]["job"])["options"]["count"] == 4 and store.get(res[0]["job"])["owner"] == 77


def test_retry_failed_and_cancel_queued(monkeypatch):
    queued = []

    class Q:
        def submit(self, jid, task="analyze", **kw):
            queued.append(jid)

    monkeypatch.setattr(batch, "get_queue", lambda: Q())
    clean_jobs(78)
    a, b, c = (store.create("x", {}, owner=78) for _ in range(3))
    store.update(a, state="failed", error="boom")
    store.update(b, state="ready")
    assert batch.retry_failed(owner=78) == [a] and queued == [a]
    assert store.get(a)["state"] == "queued" and store.get(a)["error"] is None
    assert batch.cancel_queued(c) and store.get(c)["state"] == "cancelled"
    assert not batch.cancel_queued(b)


# ---------- routes ----------

class FakeAuth:
    me = 5

    @classmethod
    def uid(cls):
        return cls.me

    @staticmethod
    def ent():
        return {"can_create": True, "priority": 1, "watermark": False, "reason": ""}

    @classmethod
    def can_access(cls, owner):
        return owner == cls.me


class FakePub(Publisher):
    key, label = "fake", "Fake"

    def status(self):
        return {"connected": True, "note": ""}

    def publish(self, post, progress=None):
        return Result("x", "https://example.com/x")


@pytest.fixture
def client(monkeypatch):
    import clip_routes
    import studio_routes
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)
    monkeypatch.setattr(studio_routes, "auth", FakeAuth)
    monkeypatch.setattr(studio_routes, "DESKTOP", True)
    monkeypatch.setattr(publish, "publishers", lambda: {"fake": FakePub()})
    queued = []

    class Q:
        def submit(self, jid, task="analyze", priority=0, **kw):
            queued.append((jid, task))
            store.update(jid, state="queued", task=task)

    monkeypatch.setattr(studio_routes, "get_queue", lambda: Q())
    monkeypatch.setattr(batch, "get_queue", lambda: Q())
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(studio_routes.__file__), "templates"))
    app.jinja_env.globals.update(csrf_token=lambda: "t", user=None, ent=None, plans={})
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(studio_routes.bp)
    c = app.test_client()
    c.queued = queued
    return c


def job_with_clip(owner=5):
    jid = store.create("x", {}, owner=owner, title="My job")
    res = {"source": {"key": "k", "kind": "file", "duration": 100.0, "title": "t", "location": "x"}, "profile": {"key": "gaming"},
           "clips": [{"id": 0, "start": 10, "end": 44.7, "duration": 34.7, "title": "Big play", "description": "wow", "hashtags": ["#gta"]}]}
    store.update(jid, state="ready", result=res)
    return jid


def test_editor_page_words_and_edit_request(client, monkeypatch):
    from recaply import engine
    jid = job_with_clip()
    assert client.get(f"/api/clipjob/{job_with_clip(owner=6)}/clip/0/words").status_code == 404   # someone else's job
    words = [{"w": "um", "s": 0.5, "e": 0.8}, {"w": "hello", "s": 1.0, "e": 1.4}]
    monkeypatch.setattr(engine, "clip_words", lambda r, c, tel=None: words)
    d = client.get(f"/api/clipjob/{jid}/clip/0/words").get_json()
    assert [w["i"] for w in d["words"]] == [0, 1] and d["fillers"] == [0] and d["duration"] == 34.7
    r = client.post(f"/api/clipjob/{jid}/clip/0/edit", json={"deleted": [1, 0, 1, "x"]})
    assert r.status_code == 400
    r = client.post(f"/api/clipjob/{jid}/clip/0/edit", json={"deleted": [0], "broll": True})
    j = store.get(jid)
    assert r.get_json()["ok"] and (jid, "edit") in client.queued
    assert j["task_args"] == {"clip": 0, "deleted": [0], "broll": True} and j["cuts"]["e0"]["state"] == "queued"
    store.update(jid, state="running")
    assert client.post(f"/api/clipjob/{jid}/clip/0/edit", json={"deleted": []}).status_code == 409
    monkeypatch.setattr(engine, "clip_words", lambda r, c, tel=None: [])
    store.update(jid, state="ready")
    assert client.get(f"/api/clipjob/{jid}/clip/0/words").status_code == 422


def test_caption_files_original_and_translated(client, monkeypatch):
    from recaply import engine
    from recaply.providers import llm as llm_mod
    jid = job_with_clip()
    words = [{"w": "hello", "s": 0.5, "e": 0.9}, {"w": "friend.", "s": 1.0, "e": 1.5}]
    monkeypatch.setattr(engine, "clip_words", lambda r, c, tel=None: words)
    r = client.get(f"/api/clipjob/{jid}/clip/0/captions.srt")
    assert r.status_code == 200 and "hello friend." in r.get_data(as_text=True) and "clip0_en.srt" in r.headers["Content-Disposition"]
    assert client.get(f"/api/clipjob/{jid}/clip/0/captions.vtt").get_data(as_text=True).startswith("WEBVTT")
    assert client.get(f"/api/clipjob/{jid}/clip/0/captions.txt").status_code == 404
    monkeypatch.setattr(llm_mod, "get_llm", lambda *a: None)
    r = client.get(f"/api/clipjob/{jid}/clip/0/captions.srt?lang=es")
    assert r.status_code == 422 and "AI key" in r.get_json()["error"]

    class L:
        def complete_json(self, s, p, max_tokens=0):
            return {"lines": ["hola amigo."]}, llm_mod.Usage("x", "m", 1, 1)

    monkeypatch.setattr(llm_mod, "get_llm", lambda *a: L())
    r = client.get(f"/api/clipjob/{jid}/clip/0/captions.srt?lang=es")
    assert "hola amigo." in r.get_data(as_text=True) and "clip0_es.srt" in r.headers["Content-Disposition"]


def test_publish_routes(client):
    jid = job_with_clip()
    os.makedirs(runner.job_dir(jid), exist_ok=True)
    with open(os.path.join(runner.job_dir(jid), "short_00.mp4"), "wb") as f:
        f.write(b"0" * 50)
    with pq._lock, pq._con() as con:
        con.execute("delete from publish_items")
    s = client.get("/api/publish").get_json()
    assert s["platforms"][0]["key"] == "fake" and s["items"] == []
    r = client.post("/api/publish", json={"job": jid, "file": "short_00.mp4", "platform": "fake", "title": "Hi", "tags": "a, b",
                                          "when": "2099-01-01T09:00"})
    n = r.get_json()["id"]
    it = pq.get(n)
    assert it["tags"] == ["a", "b"] and it["run_at"] > time.time() + 86400 * 365 and it["status"] == "queued"
    assert client.post("/api/publish", json={"job": jid, "file": "../x.mp4", "platform": "fake", "title": "x"}).status_code == 400
    assert client.post("/api/publish", json={"job": jid, "file": "short_00.mp4", "platform": "fake", "title": "x", "when": "tomorrow-ish"}).status_code == 400
    assert client.post(f"/api/publish/{n}/reschedule", json={"when": "2099-02-02T10:00"}).get_json()["ok"]
    assert client.post(f"/api/publish/{n}/cancel").get_json()["ok"] and pq.get(n)["status"] == "cancelled"
    assert client.post(f"/api/publish/{n}/retry").get_json()["ok"] and pq.get(n)["status"] == "queued"
    assert client.post(f"/api/publish/{n}/explode").status_code == 404
    FakeAuth.me = 6                                                                              # another user can't touch it
    try:
        assert client.post(f"/api/publish/{n}/cancel").status_code == 404
        assert client.get("/api/publish").get_json()["items"] == []
    finally:
        FakeAuth.me = 5


def test_publish_routes_are_desktop_only(client, monkeypatch):
    import studio_routes
    monkeypatch.setattr(studio_routes, "DESKTOP", False)
    assert client.get("/api/publish").status_code == 404
    assert client.post("/api/publish", json={}).status_code == 404


def test_queue_batch_and_usage_routes(client):
    clean_jobs(5)
    r = client.post("/api/batch", json={"inputs": "https://youtu.be/aaa\nhttps://evil.example/x\n", "options": {"profile": "gaming", "count": "5", "length": "short"}})
    d = r.get_json()
    assert d["started"] == 1 and d["results"][1]["error"] and len(client.queued) == 1
    job = store.get(d["results"][0]["job"])
    assert job["options"]["count"] == 5 and job["options"]["min_len"] == 15 and job["options"]["profile"] == "gaming"
    assert client.post("/api/batch", json={"inputs": "  \n"}).status_code == 400
    q = client.get("/api/queue").get_json()
    assert q["counts"] == {"queued": 1} and q["jobs"][0]["id"] == job["id"]
    store.update(job["id"], state="failed", error="x")
    assert client.post("/api/queue/retry-failed").get_json()["jobs"] == [job["id"]]
    assert client.post(f"/api/queue/{job['id']}/cancel").get_json()["ok"]
    u = client.get("/api/usage?days=7").get_json()
    assert u["days"] == 7 and "totals" in u and "publish" in u
