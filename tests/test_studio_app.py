"""The React AI Studio's backend glue: the SPA shell, session, project list/create and the preview media."""

import os
import shutil

import pytest
from flask import Flask

from conftest import FIXTURES
from recaply.jobs import store
from recaply.jobs.runner import job_dir


class FakeAuth:
    me = 7

    @classmethod
    def uid(cls):
        return cls.me

    @classmethod
    def current_user(cls):
        return {"id": cls.me, "name": "Ada Lovelace", "email": "ada@example.test"}

    @staticmethod
    def ent():
        return {"can_create": True, "priority": 1, "name": "Starter", "left": 4, "limit": 30, "reason": ""}

    @staticmethod
    def csrf_token():
        return "tok-123"

    @classmethod
    def can_access(cls, owner):
        return owner == cls.me


@pytest.fixture
def client(monkeypatch, tmp_path):
    import clip_routes
    import studio_app
    store.init()
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "DESKTOP", True)
    monkeypatch.setattr(studio_app, "DIST", str(tmp_path / "dist"))
    started = []

    def fake_start(text, opts, title=""):
        started.append((text, opts, title))
        return "0123456789"

    monkeypatch.setattr(clip_routes, "_start", fake_start)
    app = Flask(__name__)
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(studio_app.bp)
    c = app.test_client()
    c.started, c.dist = started, tmp_path / "dist"
    return c


def make_ready_job(owner=7, kind="file", with_source=True, beats=True):
    jid = store.new_id()
    folder = job_dir(jid)
    os.makedirs(folder, exist_ok=True)
    src = os.path.join(folder, "source.mp4")
    shutil.copy(os.path.join(FIXTURES, "speech.mp4"), src)
    clip = {"id": 1, "start": 5.0, "end": 20.0, "duration": 15.0, "score": 81.0, "title": "T",
            "beats": [{"start": 5.0, "end": 9.0, "type": "hook", "score": 70, "reason": "r"}] if beats else []}
    result = {"source": {"kind": kind, "key": "k-" + jid, "title": "t", "duration": 34.7, "extractor": "youtube", "video_id": "abc123"},
              "source_path": src if with_source else None,
              "profile": {"key": "talk", "label": "Podcast / interview / talk", "reason": "auto"}, "clips": [clip]}
    store.create(src if with_source else "https://youtu.be/abc", {}, owner=owner, title="Demo", job_id=jid)
    store.update(jid, state="ready", stage="Done", progress=1.0, result=result)
    return jid


# ---------- the single-page app ----------

def test_spa_is_served_with_the_csrf_token_under_any_path(client):
    assert client.get("/studio").status_code == 503                    # not built: says how to build it
    assert b"npm run build" in client.get("/studio").data
    client.dist.mkdir()
    (client.dist / "index.html").write_text('<meta name="csrf-token" content="__CSRF__"><div id="root"></div>')
    for path in ("/studio", "/studio/", "/studio/p/abc", "/studio/p/abc/clip/3"):
        r = client.get(path)
        assert r.status_code == 200 and b'content="tok-123"' in r.data and b"__CSRF__" not in r.data
        assert r.headers["Cache-Control"] == "no-store"


def test_session_info(client):
    d = client.get("/api/studio/session").get_json()
    assert d["csrf"] == "tok-123" and d["desktop"] is True
    assert d["user"]["name"] == "Ada Lovelace" and d["plan"]["left"] == 4 and d["plan"]["can_create"] is True
    keys = {p["key"] for p in d["profiles"]}
    assert {"talk", "gaming", "irl"} <= keys
    assert [p for p in d["profiles"] if p["key"] == "valorant_vct"][0]["hidden"] is True      # not offered in the UI


def test_capabilities_include_languages(client):
    d = client.get("/api/studio/capabilities").get_json()
    assert {"exports", "layouts", "captions", "languages"} <= set(d)
    assert d["languages"] and {"key", "label"} <= set(d["languages"][0])


# ---------- projects ----------

def test_projects_list_shows_only_my_jobs_with_detected_type_and_counts(client):
    mine = make_ready_job(owner=7)
    other = make_ready_job(owner=8)
    running = store.create("https://youtu.be/zzz", {}, owner=7, title="")
    store.update(running, state="running", stage="Checking what kind of video this is", progress=0.3)
    rows = {p["id"]: p for p in client.get("/api/studio/projects").get_json()["projects"]}
    assert mine in rows and other not in rows and running in rows
    assert rows[mine]["profile"]["label"] == "Podcast / interview / talk"
    assert (rows[mine]["clips"], rows[mine]["beats"], rows[mine]["best"]) == (1, 1, 81.0)
    assert rows[mine]["thumb"] == "abc123" and rows[mine]["duration"] == 34.7
    assert rows[running]["profile"] is None and rows[running]["stage"].startswith("Checking")      # still detecting


def test_create_project_validates_and_starts(client):
    assert client.post("/api/studio/projects", json={}).status_code == 400
    r = client.post("/api/studio/projects", json={"url": "https://evil.example.com/v"})
    assert r.status_code == 400 and "YouTube" in r.get_json()["error"]
    r = client.post("/api/studio/projects", json={"url": "C:/nope/missing.mp4"})
    assert r.status_code == 400 and "wasn't found" in r.get_json()["error"]
    r = client.post("/api/studio/projects", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "profile": "talk",
                                                  "count": 7, "length": "short", "junk": 1})
    assert r.status_code == 200 and r.get_json() == {"id": "0123456789"}
    text, opts, _ = client.started[-1]
    assert text.endswith("dQw4w9WgXcQ") and (opts["profile"], opts["count"], opts["min_len"], opts["max_len"]) == ("talk", 7, 15, 30)
    # a local file works in the desktop app
    r = client.post("/api/studio/projects", json={"url": os.path.join(FIXTURES, "speech.mp4")})
    assert r.status_code == 200 and client.started[-1][2] == "speech.mp4"


def test_create_project_refuses_when_the_plan_is_used_up(client, monkeypatch):
    import studio_app
    monkeypatch.setattr(FakeAuth, "ent", staticmethod(lambda: {"can_create": False, "reason": "No clips left."}))
    r = client.post("/api/studio/projects", json={"url": "https://youtu.be/abc"})
    assert r.status_code == 402 and r.get_json()["upgrade"] is True and "No clips" in r.get_json()["error"]
    assert studio_app.auth is FakeAuth


# ---------- preview media ----------

def test_source_video_is_streamed_with_ranges_and_flagged_in_the_job(client):
    jid = make_ready_job()
    job = client.get(f"/api/clipjob/{jid}").get_json()
    assert job["media"] == {"source": True, "audio": False}
    r = client.get(f"/api/clipjob/{jid}/media/source", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100 and r.headers["Content-Type"].startswith("video/")
    assert client.get(f"/api/clipjob/{jid}/media/bogus").status_code == 404


def test_no_source_means_no_preview_and_other_peoples_jobs_are_hidden(client):
    link_job = make_ready_job(kind="url", with_source=False)
    assert client.get(f"/api/clipjob/{link_job}").get_json()["media"] == {"source": False, "audio": False}
    assert client.get(f"/api/clipjob/{link_job}/media/source").status_code == 404
    other = make_ready_job(owner=8)
    assert client.get(f"/api/clipjob/{other}/media/source").status_code == 404
    assert client.get(f"/api/clipjob/{other}").status_code == 404


def test_analysis_audio_is_the_fallback_preview_for_links(client):
    from recaply.cache import StageCache
    jid = make_ready_job(kind="url", with_source=False)
    key = store.get(jid)["result"]["source"]["key"]
    folder = StageCache(key).dir
    os.makedirs(folder, exist_ok=True)
    shutil.copy(os.path.join(FIXTURES, "speech.mp4"), os.path.join(folder, "audio.m4a"))
    open(os.path.join(folder, "audio.m4a.part"), "wb").write(b"x")                    # half-downloaded files are ignored
    assert client.get(f"/api/clipjob/{jid}").get_json()["media"] == {"source": False, "audio": True}
    assert client.get(f"/api/clipjob/{jid}/media/audio").status_code == 200


def test_classic_dashboard_redirect():
    import app as recaply_app
    c = recaply_app.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = 1
    # logged out it goes to login first; what matters is that /app never renders the Valorant page by default
    r = c.get("/app")
    assert r.status_code == 302 and ("/studio" in r.headers["Location"] or "/login" in r.headers["Location"])
