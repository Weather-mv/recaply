"""Edge cases across the web routes: odd ids, bad bodies, traversal, past dates, odd batch lines."""

import os
import time

from conftest import FIXTURES
from recaply import batch
from recaply.jobs import store
from test_studio import client, job_with_clip   # noqa: F401  (fixture reuse)

store.init()


def test_edit_bodies_that_are_odd(client):
    jid = job_with_clip()
    url = f"/api/clipjob/{jid}/clip/0/edit"
    for body in ({"deleted": 5}, {"deleted": "abc"}, {"deleted": [None]}, {"deleted": [[1]]}):
        assert client.post(url, json=body).status_code == 400, body
    assert client.post(url, data="not json", content_type="text/plain").status_code in (200, 400)
    store.update(jid, state="ready")
    assert client.post(url, json={"deleted": [-3, 10**12]}).status_code == 200      # absurd ids are ignored at render
    assert client.post(url, json={"deleted": [1]}).status_code == 409                # already queued
    store.update(jid, state="ready")
    assert client.post(url, json={"deleted": list(range(20000))}).status_code == 200
    assert len(store.get(jid)["task_args"]["deleted"]) <= 5000


def test_clip_ids_out_of_range_everywhere(client):
    jid = job_with_clip()
    for path in (f"/clipjob/{jid}/edit/-1", f"/clipjob/{jid}/edit/99999999999999999999"):
        assert client.get(path).status_code == 404
    assert client.get(f"/api/clipjob/{jid}/clip/5/words").status_code == 404
    assert client.post(f"/api/clipjob/{jid}/clip/5/edit", json={"deleted": []}).status_code == 404
    assert client.get(f"/api/clipjob/{jid}/clip/0/captions.exe").status_code == 404
    assert client.get("/clipjob/nope/edit/0").status_code == 404
    assert client.get("/api/clipjob/nope/clip/0/words").status_code == 404


def test_file_route_blocks_traversal(client):
    jid = job_with_clip()
    for name in ("..%2f..%2fetc%2fpasswd", "..", "%2e%2e%2fapp.py", "x.mp4", "a%00.mp4"):
        assert client.get(f"/clipjob/{jid}/file/{name}").status_code == 404, name


def test_publish_rejects_bad_input(client):
    jid = job_with_clip()
    post = lambda **b: client.post("/api/publish", json=dict(job=jid, platform="fake", **b))
    assert post(file="../../etc/passwd").status_code == 400
    assert post(file="/etc/passwd").status_code == 400
    assert post(file="").status_code == 400
    assert post(file="nope.mp4", when="garbage").status_code == 400
    assert client.post("/api/publish", json={"job": "nope", "file": "a.mp4", "platform": "fake"}).status_code == 404
    assert client.post("/api/publish", data="[]", content_type="application/json").status_code == 404
    assert client.post("/api/publish/99999/cancel").status_code == 404
    assert client.post("/api/publish/99999/explode").status_code == 404


def test_batch_lines(client):
    assert client.post("/api/batch", json={"inputs": ""}).status_code in (400, 402)
    lines = ["# comment", "", "  ", "https://youtu.be/abc", "https://youtu.be/abc", "https://evil.example/x",
             "https://youtu.be/a b", "C:\\nope.mp4", "a\x00b.mp4", "x" * 5000, "https://www.twitch.tv/videos/1"]
    parsed = batch.parse_inputs("\n".join(lines))
    assert parsed.count("https://youtu.be/abc") == 1
    res = batch.start_batch(parsed, {}, allow_local=False)
    ok = [r for r in res if "job" in r]
    assert [r["input"] for r in ok] == ["https://youtu.be/abc", "https://www.twitch.tv/videos/1"]
    assert all("error" in r for r in res if "job" not in r)
    many = batch.parse_inputs("\n".join(f"https://youtu.be/v{i}" for i in range(500)))
    assert len(many) == batch.MAX_BATCH
    assert batch.parse_inputs(None) == []


def test_cancel_and_queue_for_unknown_or_foreign_jobs(client):
    assert client.post("/api/queue/nope/cancel").status_code == 404
    other = job_with_clip(owner=77)
    assert client.post(f"/api/queue/{other}/cancel").status_code == 404
    assert client.get("/api/usage").status_code == 200
    assert client.get("/api/queue").status_code == 200
