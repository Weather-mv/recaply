"""Social hub: token encryption, accounts, sign-in flow, captions, thumbnails, the queue columns and the publishers."""

import http.server
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

import pytest

from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.publish import instagram, linkedin
from recaply.publish import queue as pq
from recaply.publish import x as xpub
from recaply.publish.base import Post, PublishError
from recaply.social import accounts, captions, crypto, networks, oauth, thumbnail


# ---------- a fake API server: each test gives it a handler ----------

class Fake(http.server.BaseHTTPRequestHandler):
    handle_fn = None
    log: list = []

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        u = urllib.parse.urlparse(self.path)
        Fake.log.append((self.command, u.path, dict(urllib.parse.parse_qsl(u.query)), dict(self.headers), body))
        code, payload, headers = Fake.handle_fn(self.command, u.path, dict(urllib.parse.parse_qsl(u.query)), self.headers, body)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _go

    def log_message(self, *a):
        pass


@pytest.fixture
def fake():
    Fake.log = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def creds(monkeypatch):
    for env in ("YT", "LINKEDIN", "X", "INSTAGRAM"):
        monkeypatch.setenv(f"RECAPLY_{env}_CLIENT_ID", "cid")
        monkeypatch.setenv(f"RECAPLY_{env}_CLIENT_SECRET", "secret")


def video(tmp_path, size=300_000):
    p = tmp_path / "v.mp4"
    p.write_bytes(os.urandom(size))
    return str(p)


def account(net, owner="u1", ext="42", token=None):
    tok = token or {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3600}
    return accounts.save(owner, net, {"external_id": ext, "name": f"{net} acct", "handle": "@me"}, tok)


# ---------- crypto and accounts ----------

def test_tokens_round_trip_and_are_not_plain_text():
    enc = crypto.encrypt('{"access_token": "secret-123"}')
    assert "secret-123" not in enc
    assert crypto.decrypt(enc) == '{"access_token": "secret-123"}'


def test_accounts_are_per_owner_and_reconnect_updates_in_place():
    a = account("youtube", owner="alice", ext="chan1")
    again = account("youtube", owner="alice", ext="chan1", token={"access_token": "NEW"})
    assert again["id"] == a["id"]
    account("youtube", owner="alice", ext="chan2")
    assert len([x for x in accounts.list_accounts("alice") if x["network"] == "youtube"]) == 2
    assert all(x["network"] != "youtube" or x["owner"] == "alice" for x in accounts.list_accounts("alice"))
    assert not [x for x in accounts.list_accounts("bob")]
    assert "token_enc" not in a and "access_token" not in json.dumps(accounts.list_accounts("alice"))


def test_expired_token_is_refreshed_and_saved(monkeypatch):
    a = account("youtube", ext="r1", token={"access_token": "OLD", "refresh_token": "RT", "expires_at": time.time() - 5})
    monkeypatch.setattr(oauth, "refresh", lambda net, tok: {"access_token": "FRESH", "expires_at": time.time() + 3600})
    assert accounts.access_token(a["id"]) == "FRESH"
    assert accounts.access_token(a["id"]) == "FRESH"          # stored, not refreshed again


def test_refused_refresh_marks_the_account_for_reconnect(monkeypatch):
    a = account("x", ext="r2", token={"access_token": "OLD", "refresh_token": "RT", "expires_at": time.time() - 5})

    def refuse(net, tok):
        raise accounts.NeedsReconnect("no")
    monkeypatch.setattr(oauth, "refresh", refuse)
    with pytest.raises(accounts.NeedsReconnect):
        accounts.access_token(a["id"])
    assert accounts.get(a["id"])["status"] == "reconnect"


# ---------- sign-in flow ----------

def test_begin_builds_a_pkce_url_for_x(creds):
    f = oauth.begin("u1", "x")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(f["url"]).query))
    assert q["state"] == f["state"] and q["code_challenge_method"] == "S256" and "offline.access" in q["scope"]
    assert q["redirect_uri"].endswith("/social/callback/x")
    oauth.stop_server()


def test_begin_refuses_an_unconfigured_network(monkeypatch):
    monkeypatch.delenv("RECAPLY_LINKEDIN_CLIENT_ID", raising=False)
    with pytest.raises(oauth.OAuthError):
        oauth.begin("u1", "linkedin")


def test_callback_saves_the_account_and_rejects_bad_state(creds, monkeypatch):
    monkeypatch.setattr(oauth, "exchange", lambda net, code, ver, red: {"access_token": "AT-" + code, "expires_at": 0})
    monkeypatch.setattr(networks, "profile", lambda net, tok: {"external_id": "li-1", "name": "Pat", "avatar": ""})
    f = oauth.begin("owner-cb", "linkedin")
    assert oauth.complete("linkedin", {"state": "nope", "code": "c"})[0] is False
    url = f"http://127.0.0.1:{oauth.callback_port()}/social/callback/linkedin?state={f['state']}&code=abc"
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200 and "connected" in r.read().decode()
    st = oauth.status(f["state"], "owner-cb")
    assert st["status"] == "done" and st["account"]["name"] == "Pat"
    assert oauth.status(f["state"], "someone-else") is None
    assert oauth.complete("linkedin", {"state": f["state"], "code": "again"})[0] is False     # one use only
    oauth.stop_server()


# ---------- captions ----------

CLIP = {"id": 1, "title": "Huge clutch in the final round", "hook": "He was 1v4.",
        "description": "The comeback nobody expected.", "hashtags": ["#valorant", "#clutch", "gaming"]}


def test_fallback_captions_fit_every_network():
    out, by = captions.generate(CLIP, ["youtube", "linkedin", "x", "instagram"])
    assert by == "clip"
    for k, v in out.items():
        assert len(v["caption"]) <= networks.get(k).caption_max
    assert out["youtube"]["title"] == CLIP["title"] and out["x"]["title"] == ""
    assert "#valorant" in out["instagram"]["caption"]


def test_compose_trims_long_text_on_a_word():
    cap = captions.compose("word " * 100, ["a", "b"], 280)
    assert len(cap) <= 280 and cap.endswith("#a #b") and "…" in cap


def test_ai_captions_are_cleaned_and_capped(monkeypatch):
    class Chain:
        provider = "fake"

        def complete_json(self, system, prompt, max_tokens=0):
            return {"x": {"caption": "Insane clutch " * 40 + " #one #two", "hashtags": ["#one", "two", "three", "four"]},
                    "youtube": {"title": "T" * 300, "caption": "Watch this", "hashtags": []}}, None
    import recaply.providers.llm as llm
    monkeypatch.setattr(llm, "llm_chain", lambda *a, **k: Chain())
    out, by = captions.generate(CLIP, ["x", "youtube", "linkedin"])
    assert by == "ai:fake"
    assert len(out["x"]["caption"]) <= 280 and len(out["youtube"]["title"]) == 100
    assert out["linkedin"]["caption"]                       # missing in the reply -> the clip's own copy


# ---------- thumbnails ----------

def _job_with_frame(tmp_path):
    from PIL import Image
    jid = store.create("thumb test", {})
    d = thumbnail.thumbs_dir(jid)
    Image.new("RGB", (540, 960), (40, 90, 160)).save(os.path.join(d, "frame_01_000100.jpg"))
    return jid


@pytest.mark.parametrize("size", list(thumbnail.SIZES))
def test_thumbnail_sizes(tmp_path, size):
    from PIL import Image
    jid = _job_with_frame(tmp_path)
    name = thumbnail.render(jid, 1, "thumbs/frame_01_000100.jpg", "One tap, four kills", size, "bold")
    im = Image.open(os.path.join(job_dir(jid), name))
    assert im.size == thumbnail.SIZES[size]


def test_thumbnail_rejects_paths_outside_the_job(tmp_path):
    jid = _job_with_frame(tmp_path)
    with pytest.raises(PublishError):
        thumbnail.render(jid, 1, "thumbs/../../../secret.jpg", "x")


def test_upload_must_be_a_picture(tmp_path):
    jid = _job_with_frame(tmp_path)
    with pytest.raises(ValueError):
        thumbnail.save_upload(jid, 1, b"not an image")


# ---------- queue migration ----------

def test_old_publish_table_gets_the_new_columns(tmp_path):
    con = sqlite3.connect(str(tmp_path / "old.db"))
    con.executescript(pq.SCHEMA)          # the table as it was before the Social hub
    con.execute("insert into publish_items (job_id, file, platform, title, run_at, created, updated)"
                " values ('j', 'f.mp4', 'youtube', 't', 1, 1, 1)")
    con.commit()
    pq.migrate(con)
    pq.migrate(con)                       # running it again changes nothing
    cols = {r[1] for r in con.execute("pragma table_info(publish_items)")}
    assert con.execute("select count(*) from publish_items").fetchone()[0] == 1
    assert {"account_id", "thumbnail", "thumb_at", "post_group", "owner", "clip_id"} <= cols


def test_drafts_are_never_uploaded(tmp_path):
    jid = store.create("draft test", {})
    os.makedirs(job_dir(jid), exist_ok=True)
    open(os.path.join(job_dir(jid), "short_01.mp4"), "wb").write(b"x")
    n = pq.enqueue(jid, "short_01.mp4", "webhook", "t", run_at=time.time() - 10, status="draft", post_group="g" * 12)
    assert pq._claim(time.time()) is None or pq.get(n)["status"] == "draft"
    assert pq.get(n)["status"] == "draft"
    pq.delete(n)


# ---------- publishers against fake APIs ----------

def test_linkedin_upload_and_post(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(linkedin, "API", fake)
    acc = account("linkedin", ext="abc")

    def h(method, path, q, headers, body):
        if path == "/videos" and q.get("action") == "initializeUpload":
            return 200, {"value": {"video": "urn:li:video:9", "uploadToken": "", "uploadInstructions": [
                {"uploadUrl": fake + "/part/0", "firstByte": 0, "lastByte": 199_999},
                {"uploadUrl": fake + "/part/1", "firstByte": 200_000, "lastByte": 299_999}]}}, None
        if path.startswith("/part/"):
            return 200, b"", {"ETag": f'"etag{path[-1]}"'}
        if path == "/videos" and q.get("action") == "finalizeUpload":
            return 200, b"", None
        if path == "/posts":
            return 201, b"", {"x-restli-id": "urn:li:share:77"}
        return 404, {}, None
    Fake.handle_fn = staticmethod(h)
    res = linkedin.LinkedInPublisher().publish(Post(video(tmp_path), "Title", "Hello (world) #clutch", account_id=acc["id"]))
    assert res.url.endswith("urn:li:share:77")
    fin = [json.loads(b) for m, p, q, hd, b in Fake.log if q.get("action") == "finalizeUpload"][0]
    assert fin["finalizeUploadRequest"]["uploadedPartIds"] == ["etag0", "etag1"]
    post = [json.loads(b) for m, p, q, hd, b in Fake.log if p == "/posts"][0]
    assert post["author"] == "urn:li:person:abc" and "{hashtag|\\#|clutch}" in post["commentary"] and "\\(world\\)" in post["commentary"]
    sizes = [len(b) for m, p, q, hd, b in Fake.log if p.startswith("/part/")]
    assert sizes == [200_000, 100_000]


def test_x_chunked_upload_then_tweet(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(xpub, "API", fake)
    monkeypatch.setattr(xpub, "CHUNK", 100_000)
    acc = account("x", ext="x1")
    state = {"polls": 0}

    def h(method, path, q, headers, body):
        if path == "/media/upload/initialize":
            return 200, {"data": {"id": "m1"}}, None
        if path == "/media/upload/m1/append":
            return 200, {}, None
        if path == "/media/upload/m1/finalize":
            return 200, {"data": {"id": "m1", "processing_info": {"state": "pending"}}}, None
        if path == "/media/upload" and q.get("command") == "STATUS":
            state["polls"] += 1
            return 200, {"data": {"processing_info": {"state": "succeeded"}}}, None
        if path == "/tweets":
            return 201, {"data": {"id": "t9"}}, None
        return 404, {}, None
    Fake.handle_fn = staticmethod(h)
    monkeypatch.setattr(xpub.XPublisher, "_wait", staticmethod(lambda check, what, **k: check() or check()))
    res = xpub.XPublisher().publish(Post(video(tmp_path), "T", "Look at this " * 40, account_id=acc["id"]))
    assert res.url.endswith("/t9")
    assert len([1 for m, p, *_ in Fake.log if p.endswith("/append")]) == 3
    tweet = [json.loads(b) for m, p, q, hd, b in Fake.log if p == "/tweets"][0]
    assert len(tweet["text"]) <= 280 and tweet["media"]["media_ids"] == ["m1"]


def test_x_free_plan_gets_a_clear_message(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(xpub, "API", fake)
    acc = account("x", ext="x2")
    Fake.handle_fn = staticmethod(lambda *a: (403, {"title": "Client Forbidden", "detail": "client-not-enrolled"}, None))
    with pytest.raises(PublishError, match="paid X API"):
        xpub.XPublisher().publish(Post(video(tmp_path), "T", "hi", account_id=acc["id"]))


def test_instagram_resumable_reel(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(instagram, "GRAPH", fake)
    monkeypatch.setattr(instagram, "RUPLOAD", fake + "/rupload")
    monkeypatch.setattr(instagram.InstagramPublisher, "_wait", staticmethod(lambda check, what, **k: check()))
    acc = account("instagram", ext="ig1", token={"access_token": "AT", "expires_at": time.time() + 50 * 86400})

    def h(method, path, q, headers, body):
        if path == "/ig1/media":
            return 200, {"id": "c1"}, None
        if path == "/rupload/c1":
            assert headers["Authorization"].startswith("OAuth ") and headers["offset"] == "0"
            return 200, {"success": True}, None
        if path == "/c1":
            return 200, {"status_code": "FINISHED"}, None
        if path == "/ig1/media_publish":
            return 200, {"id": "p1"}, None
        if path == "/p1":
            return 200, {"permalink": "https://www.instagram.com/reel/abc/"}, None
        return 404, {}, None
    Fake.handle_fn = staticmethod(h)
    res = instagram.InstagramPublisher().publish(Post(video(tmp_path), "T", "Caption #tag", account_id=acc["id"], thumb_at=2.5))
    assert res.url == "https://www.instagram.com/reel/abc/"
    create = [q for m, p, q, hd, b in Fake.log if p == "/ig1/media"][0]
    assert create["media_type"] == "REELS" and create["upload_type"] == "resumable" and create["thumb_offset"] == "2500"


def test_rate_limit_is_retryable_and_5xx_too(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(xpub, "API", fake)
    acc = account("x", ext="x3")
    for code in (429, 503):
        Fake.handle_fn = staticmethod(lambda *a, c=code: (c, {"detail": "slow down"}, None))
        with pytest.raises(PublishError) as e:
            xpub.XPublisher().publish(Post(video(tmp_path), "T", "hi", account_id=acc["id"]))
        assert e.value.retryable


def test_401_refreshes_once_then_succeeds(fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(xpub, "API", fake)
    acc = account("x", ext="x4")
    calls = {"n": 0}
    monkeypatch.setattr(oauth, "refresh", lambda net, tok: {"access_token": "AT2", "expires_at": time.time() + 3600})

    def h(method, path, q, headers, body):
        if path == "/media/upload/initialize":
            calls["n"] += 1
            if headers["Authorization"] == "Bearer AT":
                return 401, {}, None
            return 200, {"data": {"id": "m2"}}, None
        if path == "/media/upload/m2/finalize":
            return 200, {"data": {"id": "m2"}}, None
        if path == "/tweets":
            return 201, {"data": {"id": "t2"}}, None
        return 200, {}, None
    Fake.handle_fn = staticmethod(h)
    res = xpub.XPublisher().publish(Post(video(tmp_path), "T", "hi", account_id=acc["id"]))
    assert res.id == "t2" and calls["n"] == 2


def test_publishing_without_an_account_is_a_clear_error(creds, tmp_path):
    with pytest.raises(PublishError, match="Pick a connected"):
        linkedin.LinkedInPublisher().publish(Post(video(tmp_path), "T", "hi"))
