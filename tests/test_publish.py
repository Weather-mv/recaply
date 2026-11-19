"""Publishing: YouTube upload against a fake API, the webhook publisher, and the schedule queue."""

import http.server
import json
import os
import stat
import threading
import time
import urllib.parse
import urllib.request

import pytest

from recaply import publish
from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.publish import queue as pq
from recaply.publish import youtube
from recaply.publish.base import Post, PublishError, Publisher, Result
from recaply.publish.webhook import WebhookPublisher
from recaply.publish.youtube import YouTubePublisher, shorts_text


class FakeGoogle(http.server.BaseHTTPRequestHandler):
    """Token endpoint + resumable upload endpoint."""
    log: list = []
    size = 0
    received = 0
    fail_auth_once = False
    quota = False
    meta: dict = {}

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self._body()
        path = urllib.parse.urlparse(self.path).path
        if path == "/token":
            form = urllib.parse.parse_qs(body.decode())
            FakeGoogle.log.append(("token", {k: v[0] for k, v in form.items()}))
            out = {"access_token": "AT-" + str(len(FakeGoogle.log)), "expires_in": 3600}
            if form.get("grant_type") == ["authorization_code"]:
                out["refresh_token"] = "RT-1"
            return self._send(200, json.dumps(out).encode(), {"Content-Type": "application/json"})
        if path == "/upload/youtube/v3/videos":
            if FakeGoogle.quota:
                err = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
                return self._send(403, json.dumps(err).encode())
            if FakeGoogle.fail_auth_once:
                FakeGoogle.fail_auth_once = False
                return self._send(401, b"{}")
            FakeGoogle.meta = json.loads(body)
            FakeGoogle.size = int(self.headers["X-Upload-Content-Length"])
            FakeGoogle.received = 0
            FakeGoogle.log.append(("start", self.headers["Authorization"]))
            return self._send(200, b"", {"Location": f"http://127.0.0.1:{self.server.server_address[1]}/session/1"})
        self._send(404)

    def do_PUT(self):
        body = self._body()
        FakeGoogle.received += len(body)
        FakeGoogle.log.append(("chunk", self.headers["Content-Range"]))
        if FakeGoogle.received >= FakeGoogle.size:
            return self._send(200, json.dumps({"id": "vid123"}).encode())
        self._send(308, b"", {"Range": f"bytes=0-{FakeGoogle.received - 1}"})

    def log_message(self, *a):
        pass


@pytest.fixture
def google():
    FakeGoogle.log, FakeGoogle.quota, FakeGoogle.fail_auth_once = [], False, False
    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield YouTubePublisher("cid", "secret", auth_url=base + "/auth", token_url=base + "/token", api_base=base)
    srv.shutdown()


@pytest.fixture(autouse=True)
def clean_tokens():
    youtube.disconnect()
    yield
    youtube.disconnect()


def video(tmp_path, size=700_000):
    p = tmp_path / "v.mp4"
    p.write_bytes(os.urandom(size))
    return str(p)


# ---------- YouTube ----------

def test_shorts_text_limits_and_hashtag():
    t, d, tags = shorts_text(Post("x", "  A  very   long title " + "x" * 200, "desc", ["#GTA", "gta", "clips", " "]))
    assert len(t) == 100 and d.endswith("#Shorts") and tags == ["GTA", "clips"]
    _, d2, _ = shorts_text(Post("x", "t #shorts", "hi"))
    assert d2 == "hi"
    _, _, many = shorts_text(Post("x", "t", "", [f"tag{i}" + "y" * 40 for i in range(40)]))
    assert sum(len(t) + 1 for t in many) <= 450


def test_status_messages(monkeypatch):
    assert not YouTubePublisher("", "").status()["connected"]
    p = YouTubePublisher("cid", "sec")
    assert "not signed in" in p.status()["note"].lower()
    youtube._write_tokens({"refresh_token": "r"})
    assert p.status()["connected"]


def test_authorize_stores_tokens_privately(google):
    def browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
        redirect = q["redirect_uri"][0] + "/?" + urllib.parse.urlencode({"code": "CODE", "state": q["state"][0]})
        threading.Thread(target=lambda: urllib.request.urlopen(redirect, timeout=10).read(), daemon=True).start()

    google.authorize(open_browser=browser, timeout=20)
    t = youtube._read_tokens()
    assert t["refresh_token"] == "RT-1" and google.status()["connected"]
    ex = [x for k, x in FakeGoogle.log if k == "token"][0]
    assert ex["code"] == "CODE" and ex["code_verifier"] and ex["redirect_uri"].startswith("http://127.0.0.1:")
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(youtube._store_path()).st_mode) == 0o600


def test_authorize_rejects_a_wrong_state(google):
    def browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        bad = q["redirect_uri"][0] + "/?code=EVIL&state=wrong"
        good = q["redirect_uri"][0] + "/?error=access_denied&state=" + q["state"][0]

        def go():
            try:
                urllib.request.urlopen(bad, timeout=10)
            except Exception:  # noqa: BLE001 — the 404 is the point
                pass
            urllib.request.urlopen(good, timeout=10).read()
        threading.Thread(target=go, daemon=True).start()

    with pytest.raises(PublishError, match="cancelled"):
        google.authorize(open_browser=browser, timeout=20)
    assert not [1 for k, _ in FakeGoogle.log if k == "token"]            # the forged code was never exchanged


def test_upload_in_chunks_returns_the_video_link(google, monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    youtube._write_tokens({"refresh_token": "RT", "access_token": "AT", "expires": time.time() + 600})
    prog = []
    res = google.publish(Post(video(tmp_path), "My clip", "great", ["gta"], "unlisted"), progress=prog.append)
    assert res.id == "vid123" and res.url == "https://youtu.be/vid123"
    chunks = [v for k, v in FakeGoogle.log if k == "chunk"]
    assert len(chunks) == 3 and chunks[0].startswith("bytes 0-262143/700000") and chunks[-1].endswith("/700000")
    assert FakeGoogle.meta["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    assert FakeGoogle.meta["snippet"]["description"].endswith("#Shorts") and prog[-1] == 1.0


def test_expired_token_is_refreshed_and_a_401_retries_once(google, tmp_path):
    youtube._write_tokens({"refresh_token": "RT", "access_token": "OLD", "expires": time.time() - 5})
    FakeGoogle.fail_auth_once = True
    res = google.publish(Post(video(tmp_path, 1000), "t"))
    assert res.id == "vid123"
    assert [k for k, _ in FakeGoogle.log].count("token") == 2           # expiry refresh + forced refresh after the 401


def test_quota_error_is_final_and_clear(google, tmp_path):
    youtube._write_tokens({"refresh_token": "RT", "access_token": "AT", "expires": time.time() + 600})
    FakeGoogle.quota = True
    with pytest.raises(PublishError) as e:
        google.publish(Post(video(tmp_path, 1000), "t"))
    assert "quota" in str(e.value).lower() and not e.value.retryable


def test_network_error_is_retryable(tmp_path):
    p = YouTubePublisher("c", "s", api_base="http://127.0.0.1:9", token_url="http://127.0.0.1:9/token")
    youtube._write_tokens({"refresh_token": "RT", "access_token": "AT", "expires": time.time() + 600})
    with pytest.raises(PublishError) as e:
        p.publish(Post(video(tmp_path, 1000), "t"))
    assert e.value.retryable


# ---------- webhook ----------

class Hook(http.server.BaseHTTPRequestHandler):
    got: list = []
    reply = b'{"id": "zap1", "url": "https://x/y"}'

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        Hook.got.append((self.headers.get("Authorization"), self.headers["Content-Type"], self.rfile.read(n)))
        self.send_response(200)
        self.send_header("Content-Length", str(len(Hook.reply)))
        self.end_headers()
        self.wfile.write(Hook.reply)

    def log_message(self, *a):
        pass


def test_webhook_posts_video_and_text(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Hook.got.clear()
    p = WebhookPublisher(f"http://127.0.0.1:{srv.server_address[1]}/h", "tok")
    res = p.publish(Post(video(tmp_path, 2000), "Hello", "desc", ["a", "b"]))
    Hook.reply = b""                                                      # an empty 2xx still counts as sent
    res2 = p.publish(Post(video(tmp_path, 2000), "Hello"))
    srv.shutdown()
    auth, ctype, body = Hook.got[0]
    assert auth == "Bearer tok" and ctype.startswith("multipart/form-data")
    assert b'name="title"' in body and b"Hello" in body and b'name="video"' in body and b'["a", "b"]' in body
    assert (res.id, res.url) == ("zap1", "https://x/y") and res2.id == "sent"
    assert not WebhookPublisher("", "").status()["connected"]


# ---------- the schedule ----------

class Fake(Publisher):
    key, label = "fake", "Fake"

    def __init__(self):
        self.calls, self.errors = [], []

    def status(self):
        return {"connected": True, "note": ""}

    def publish(self, post, progress=None):
        self.calls.append(post)
        if self.errors:
            raise self.errors.pop(0)
        return Result("id" + str(len(self.calls)), "https://example.com/v")


@pytest.fixture
def fake(monkeypatch):
    with pq._lock, pq._con() as con:                                       # every test starts with an empty schedule
        con.execute("delete from publish_items")
    f = Fake()
    monkeypatch.setattr(publish, "publishers", lambda: {"fake": f})
    return f


def make_job_file(name="short_01.mp4"):
    store.init()
    jid = store.create("x", {})
    os.makedirs(job_dir(jid), exist_ok=True)
    with open(os.path.join(job_dir(jid), name), "wb") as fh:
        fh.write(b"0" * 100)
    return jid, name


def test_enqueue_validates_and_blocks_path_tricks(fake):
    jid, name = make_job_file()
    pq.enqueue(jid, name, "fake", "t")
    with pytest.raises(PublishError):
        pq.enqueue(jid, "../../etc/passwd.mp4", "fake", "t")
    with pytest.raises(PublishError):
        pq.enqueue(jid, "missing.mp4", "fake", "t")
    with pytest.raises(PublishError):
        pq.enqueue(jid, name, "myspace", "t")
    other = store.create("y", {})
    with pytest.raises(PublishError):
        pq.resolve_file(other, "../" + jid + "/" + name)


def test_scheduled_item_waits_then_uploads(fake):
    jid, name = make_job_file()
    now = time.time()
    later = pq.enqueue(jid, name, "fake", "Later", "d", ["x"], run_at=now + 3600)
    soon = pq.enqueue(jid, name, "fake", "Soon", run_at=now - 1)
    assert pq.run_due() >= 1
    assert [c.title for c in fake.calls] == ["Soon"]
    assert pq.get(soon)["status"] == "done" and pq.get(soon)["result"]["url"] == "https://example.com/v"
    assert pq.get(later)["status"] == "queued"
    assert pq.reschedule(later, now - 1) and pq.run_due() >= 1
    assert pq.get(later)["status"] == "done" and fake.calls[-1].tags == ["x"]


def test_retryable_errors_back_off_then_fail(fake):
    jid, name = make_job_file()
    iid = pq.enqueue(jid, name, "fake", "t")
    fake.errors = [PublishError("503", retryable=True)] * 3
    t0 = time.time()
    pq.run_due()
    it = pq.get(iid)
    assert it["status"] == "queued" and it["attempts"] == 1 and 55 < it["run_at"] - t0 < 65
    pq.run_due()                                                          # not due yet: nothing happens
    assert len(fake.calls) == 1
    pq.run_due(now=time.time() + 100)
    assert pq.get(iid)["attempts"] == 2 and 290 < pq.get(iid)["run_at"] - t0 < 310
    pq.run_due(now=time.time() + 400)
    it = pq.get(iid)
    assert it["status"] == "failed" and it["attempts"] == 3 and "503" in it["error"]
    assert pq.retry(iid) and pq.get(iid)["status"] == "queued"
    pq.run_due()
    assert pq.get(iid)["status"] == "done"


def test_final_errors_fail_at_once_and_cancel_works(fake):
    jid, name = make_job_file()
    a = pq.enqueue(jid, name, "fake", "a")
    b = pq.enqueue(jid, name, "fake", "b", run_at=time.time() + 999)
    fake.errors = [PublishError("quota used up")]
    pq.run_due()
    assert pq.get(a)["status"] == "failed" and pq.get(a)["attempts"] == 1
    assert pq.cancel(b) and pq.get(b)["status"] == "cancelled" and not pq.cancel(b + 1000)
    assert not pq.cancel(a) is False                                      # a failed item can be cancelled too
    fake.errors = [RuntimeError("bug")]
    c = pq.enqueue(jid, name, "fake", "c")
    pq.run_due()
    assert pq.get(c)["status"] == "failed" and "unexpected" in pq.get(c)["error"]


def test_stuck_uploads_return_to_the_queue(fake):
    jid, name = make_job_file()
    iid = pq.enqueue(jid, name, "fake", "t")
    pq._set(iid, status="uploading")
    assert pq.reset_stuck() >= 1 and pq.get(iid)["status"] == "queued"
