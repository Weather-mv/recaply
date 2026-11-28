"""The Editor: any video becomes one editable clip (no analysis), and the media bin lists every clip."""
import io
import os
import time

from flask import Flask

from conftest import FIXTURES

VIDEO = os.path.join(FIXTURES, "speech.mp4")


def test_an_imported_video_is_one_clip_over_the_whole_video():
    from recaply.edit.importer import EDIT_MAX, SCORER, edit_result
    r = edit_result(VIDEO, "My video", "talk")
    (clip,) = r["clips"]
    dur = r["source"]["duration"]
    assert dur > 0 and clip["id"] == 1 and clip["start"] == 0 and abs(clip["end"] - min(dur, EDIT_MAX)) < 0.01
    assert r["scorer"] == SCORER and r["source_path"] == VIDEO and r["source"]["title"] == "My video"
    assert clip["score"] is None and clip["beats"] == [] and r["profile"]["key"] == "talk"
    assert edit_result(VIDEO, "", "no-such-profile")["profile"]["key"] == "talk"


def test_a_long_video_opens_its_first_part(monkeypatch):
    from recaply.edit import importer
    from recaply.ingest import sources
    real = sources.resolve_file

    def long(path):
        s = real(path)
        s.duration = 3600.0
        return s
    monkeypatch.setattr(importer, "resolve_file", long)
    r = importer.edit_result(VIDEO)
    assert r["clips"][0]["end"] == importer.EDIT_MAX and r["editor"]["long"]


def _client(monkeypatch):
    import clip_routes
    import editor_routes
    from test_studio_app import FakeAuth
    from recaply.jobs import runner, store
    store.init()
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)
    monkeypatch.setattr(editor_routes, "auth", FakeAuth)

    class NowQueue:                                  # run the task right away, in the test's thread
        def submit(self, jid, task="analyze", priority=0, refund=None):
            store.update(jid, state="queued", task=task)
            runner.run_task(jid, task)
    monkeypatch.setattr(editor_routes, "get_queue", lambda: NowQueue())
    app = Flask(__name__)
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(editor_routes.bp)
    return app.test_client(), store


def test_dropping_a_video_opens_it_ready_to_edit(monkeypatch):
    c, store = _client(monkeypatch)
    with open(VIDEO, "rb") as f:
        r = c.post("/api/editor/upload", data={"file": (io.BytesIO(f.read()), "Team talk.mp4")},
                   content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    jid = r.get_json()["id"]
    job = store.get(jid)
    assert job["state"] == "ready" and job["title"] == "Team talk" and len(job["result"]["clips"]) == 1
    assert (job.get("options") or {}).get("mode") == "edit"

    lib = c.get("/api/editor/library").get_json()["items"]
    mine = next(i for i in lib if i["id"] == jid)
    assert mine["editor"] and mine["clips"][0]["id"] == 1 and mine["media"]["source"]
    assert "source_path" not in str(lib)                                 # no local paths leak to the page

    job_view = c.get(f"/api/clipjob/{jid}").get_json()
    assert job_view["state"] == "ready" and job_view["result"]["clips"][0]["title"] == "Team talk"


def test_only_videos_can_be_dropped(monkeypatch):
    c, _ = _client(monkeypatch)
    r = c.post("/api/editor/upload", data={"file": (io.BytesIO(b"hello"), "notes.txt")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "video" in r.get_json()["error"]
    assert c.post("/api/editor/upload", data={}, content_type="multipart/form-data").status_code == 400


def test_a_broken_file_fails_with_a_reason(monkeypatch):
    c, store = _client(monkeypatch)
    r = c.post("/api/editor/upload", data={"file": (io.BytesIO(b"\x00" * 2048), "broken.mp4")},
               content_type="multipart/form-data")
    job = store.get(r.get_json()["id"])
    assert job["state"] == "failed" and job["error"]
    lib = c.get("/api/editor/library").get_json()["items"]
    assert all(i["id"] != job["id"] for i in lib)                          # failed imports stay out of the bin
