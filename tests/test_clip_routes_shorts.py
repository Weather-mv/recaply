"""The shorts and brand-kit HTTP routes (logged-in user stubbed, queue run inline)."""

import io
import os

import pytest
from flask import Flask

from conftest import FIXTURES

BURSTS = os.path.join(FIXTURES, "bursts.mp4")


class FakeAuth:
    charged: list = []
    watermark = False

    @staticmethod
    def uid():
        return 7

    @classmethod
    def ent(cls):
        return {"can_create": True, "priority": 0, "watermark": cls.watermark, "reason": ""}

    @staticmethod
    def can_access(owner):
        return owner in (7, None)

    @classmethod
    def charge(cls, keys):
        cls.charged += keys
        return True, len(keys)

    @staticmethod
    def refunder():
        return lambda keys: None


@pytest.fixture()
def client(monkeypatch):
    import clip_routes
    from recaply.jobs import queue, runner, store
    store.init()
    FakeAuth.charged = []
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)

    class Inline(queue.JobQueue):
        def submit(self, job_id, task="analyze", priority=0, refund=None):
            store.update(job_id, state="queued", task=task)
            runner.run_task(job_id, task)

    monkeypatch.setattr(clip_routes, "get_queue", lambda: Inline())
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(FIXTURES), "..", "templates"))
    app.config["SECRET_KEY"] = "test"
    app.jinja_env.globals.update(csrf_token=lambda: "t", user=None, ent=None, plans={}, DESKTOP=False)
    app.add_url_rule("/app", "app_home", lambda: "")
    app.register_blueprint(clip_routes.bp)
    return app.test_client()


def make_job():
    from recaply.engine import Options, analyze
    from recaply.jobs import store
    res = analyze(BURSTS, Options(profile="gaming", count=2, min_len=12, max_len=20, transcriber="none", llm="none"))
    jid = store.create(BURSTS, {}, owner=7, title="bursts")
    store.update(jid, state="ready", result=res)
    return jid, [c["id"] for c in res["clips"]]


def test_shorts_route_renders_and_charges_once_per_clip(client):
    jid, ids = make_job()
    r = client.post(f"/api/clipjob/{jid}/shorts", json={
        "ids": ids + [999], "options": {"export": "draft", "captions": "none", "use_brand": False, "layout": "bogus"}})
    assert r.status_code == 200 and r.json["queued"] == len(ids)
    assert FakeAuth.charged == [f"clip:{jid}:{i}" for i in ids]          # same key as a cut: paid once
    job = client.get(f"/api/clipjob/{jid}").json
    assert job["state"] == "ready"
    short = job["cuts"][f"s{ids[0]}"]
    assert short["state"] == "done"
    f = client.get(f"/clipjob/{jid}/file/{short['file']}")
    assert f.status_code == 200 and len(f.data) > 10_000
    assert client.get(f"/clipjob/{jid}/file/nope.mp4").status_code == 404


def test_shorts_route_validates(client):
    jid, ids = make_job()
    assert client.post(f"/api/clipjob/{jid}/shorts", json={"ids": []}).status_code == 400
    assert client.post(f"/api/clipjob/{jid}/shorts", json={"ids": ["x"]}).status_code == 400
    assert client.post("/api/clipjob/zzzz/shorts", json={"ids": ids}).status_code == 404


def test_job_page_offers_the_shorts_controls(client):
    jid, _ = make_job()
    page = client.get(f"/clipjob/{jid}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="mk-shorts"' in html and "YouTube Shorts (9:16)" in html and "Karaoke" in html


def test_brand_kit_api(client):
    r = client.post("/api/brand", json={"accent": "#ff0000", "caption_style": "neon", "logo_pos": "bl",
                                         "logo_size": "0.2"})
    assert r.status_code == 200 and r.json["kit"]["accent"] == "#ff0000" and r.json["kit"]["logo_pos"] == "bl"
    assert any(c["key"] == "neon" for c in r.json["captions"])
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (0, 255, 0, 255)).save(buf, "PNG")
    buf.seek(0)
    up = client.post("/api/brand/logo", data={"file": (buf, "logo.png")}, content_type="multipart/form-data")
    assert up.status_code == 200
    assert client.get("/api/brand").json["kit"]["logo"] is True
    bad = client.post("/api/brand/logo", data={"file": (io.BytesIO(b"not an image"), "x.png")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400
    assert client.post("/api/brand/font", data={"file": (io.BytesIO(b"x"), "x.exe")},
                       content_type="multipart/form-data").status_code == 400
    assert client.delete("/api/brand/logo").status_code == 200
    assert client.get("/api/brand").json["kit"]["logo"] is False
    assert client.post("/api/brand/bogus").status_code == 404
