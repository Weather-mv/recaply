"""Unified plan, Phase 4: live mode reconnects with backoff, keeps its buffer under a size cap, and clips "the last N seconds"."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from flask import Flask

from recaply.live import config, events
from recaply.live.buffer import Buffer, Seg
from recaply.live.channel import BACKOFF_MAX, BACKOFF_MIN, ChannelWorker
from test_live import FakeMgr, make_cfg, ts_stream_cmd


def realtime(cmd):
    """The same synthetic feed, but read at real time (like a live stream) instead of all at once."""
    out = []
    for i, a in enumerate(cmd):
        if a == "-i":
            out.append("-re")
        out.append(a)
    return out


# ---------- reconnect with backoff

FLAKY = """
import subprocess, sys, os
counter, ffmpeg_cmd = sys.argv[1], sys.argv[2:]
n = int(open(counter).read() or 0) if os.path.exists(counter) else 0
open(counter, "w").write(str(n + 1))
if n >= 2:                         # third attempt on: the stream is gone for good
    sys.exit(1)
sys.exit(subprocess.call(ffmpeg_cmd))
"""


def test_a_dropped_connection_is_retried_and_the_recording_carries_on(tmp_path):
    counter = str(tmp_path / "count.txt")
    cmd = realtime(ts_stream_cmd(duration=8, burst=(100, 101)))    # a quiet 8 s feed at real time, then the link drops
    cfg = make_cfg(name="flaky", segment_sec=5, end_grace=15, source_cmd=[sys.executable, "-c", FLAKY, counter, *cmd])
    w = ChannelWorker(cfg, root=str(tmp_path / "live"), submit=lambda *a: "j", tick=1)
    w.start()
    w.join(120)
    assert not w.is_alive()
    assert w.state == "ended" and "ended" in w.note
    assert w.reconnects >= 2                                         # attempt 2 and 3 were reconnections
    assert int(open(counter).read()) >= 3
    kinds = [(e["kind"], e["title"]) for e in events.list_events(channel="flaky")]
    assert any(k == "recording" and "reconnected" in t for k, t in kinds)
    assert w.buf.length > 12                                         # two 8 s feeds were stitched into one recording


def test_the_backoff_grows_and_is_capped():
    assert BACKOFF_MIN < BACKOFF_MAX
    b = BACKOFF_MIN
    seen = []
    for _ in range(8):
        b = min(BACKOFF_MAX, b * 2)
        seen.append(b)
    assert seen[0] == 2 * BACKOFF_MIN and seen[-1] == BACKOFF_MAX


# ---------- disk limit

def _buffer_with(tmp_path, sizes, durs=30.0):
    folder = str(tmp_path / "buf")
    os.makedirs(folder)
    b = Buffer(folder)
    t = 0.0
    for i, mb in enumerate(sizes):
        p = os.path.join(folder, f"seg_{i:06d}.ts")
        with open(p, "wb") as f:
            f.write(b"\0" * int(mb * 1e6))
        b._segs.append(Seg(p, t, durs, i < len(sizes) - 1))
        t += durs
    return b


def test_the_buffer_is_trimmed_to_its_size_oldest_first(tmp_path):
    b = _buffer_with(tmp_path, [1, 1, 1, 1, 1])
    assert b.size_bytes() == 5_000_000
    assert b.prune_to_size(2_500_000) == 3
    assert b.size_bytes() == 2_000_000 and b.oldest == 90.0
    assert sorted(os.listdir(b.folder)) == ["seg_000003.ts", "seg_000004.ts"]
    assert b.prune_to_size(10) == 1                                  # the newest segment is never removed
    assert len(os.listdir(b.folder)) == 1


def test_a_moment_waiting_to_be_cut_is_protected_from_the_size_limit(tmp_path):
    b = _buffer_with(tmp_path, [1, 1, 1, 1, 1])
    assert b.prune_to_size(1_000_000, protect_from=65.0) == 2        # segments ending after 65 s stay for the pending clip
    assert b.oldest == 60.0 and len(os.listdir(b.folder)) == 3


def test_buffer_limit_is_a_validated_setting():
    assert config.validate({"name": "a", "url": "https://www.twitch.tv/abc"}).max_buffer_mb == 4096
    assert config.validate({"name": "a", "url": "https://www.twitch.tv/abc", "max_buffer_mb": 5}).max_buffer_mb == 200
    assert config.validate({"name": "a", "url": "https://www.twitch.tv/abc", "max_buffer_mb": "1500"}).max_buffer_mb == 1500


# ---------- clip the last N seconds

def test_clip_the_last_seconds_while_recording(tmp_path):
    cfg = make_cfg(name="manual", segment_sec=5, source_cmd=realtime(ts_stream_cmd(duration=30, burst=(100, 101))))
    calls = []
    w = ChannelWorker(cfg, root=str(tmp_path / "live"), submit=lambda c, p, i: calls.append((p, dict(i))) or "job-m", tick=1)
    with pytest.raises(RuntimeError, match="Nothing is being recorded"):
        w.clip_now(10)                                               # not started yet
    w.start()
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not (w.state == "recording" and w.buf and w.buf.length >= 12):
            time.sleep(0.5)
        assert w.state == "recording", (w.state, w.note)
        info = w.clip_now(8)
        st = w.status()
        assert st["buffer_mb"] > 0 and st["buffer_limit_mb"] == 4096 and st["reconnects"] == 0 and st["reconnecting"] is False
    finally:
        w.stop()
        w.join(30)
    assert info["manual"] is True and info["job"] == "job-m" and 6 <= info["end"] - info["start"] <= 9
    assert calls and calls[0][0].endswith("_manual.mp4")
    assert w.clips and w.clips[0]["manual"]


# ---------- the route

def test_clip_now_route(monkeypatch):
    import live_routes
    mgr = FakeMgr()
    asked = []

    def clip_now(name, seconds):
        asked.append((name, seconds))
        if name == "idle":
            raise RuntimeError("This channel isn't being watched right now.")
        return {"t": 90.0, "clock": "0:01:30", "manual": True, "job": "j1", "file": "C:/x/clip.mp4", "start": 60.0, "end": 90.0}

    mgr.clip_now = clip_now
    monkeypatch.setattr(live_routes, "LIVE_OK", True)
    monkeypatch.setattr(live_routes, "get_manager", lambda: mgr)
    monkeypatch.setattr(live_routes, "auth", type("A", (), {"uid": staticmethod(lambda: 1)}))
    app = Flask(__name__)
    app.register_blueprint(live_routes.bp)
    c = app.test_client()
    r = c.post("/api/live/channels/rt/clip", json={"seconds": 30})
    assert r.status_code == 200 and asked == [("rt", 30.0)]
    body = r.get_json()
    assert body["clip"]["job"] == "j1" and "file" not in body["clip"]               # local paths never leave the PC
    assert c.post("/api/live/channels/rt/clip", json={}).status_code == 200 and asked[-1] == ("rt", 60.0)
    assert c.post("/api/live/channels/rt/clip", json={"seconds": "soon"}).status_code == 400
    assert c.post("/api/live/channels/idle/clip", json={"seconds": 30}).status_code == 409
    assert c.post("/api/live/channels/BAD NAME/clip", json={}).status_code == 404


def test_studio_sends_live_links_to_the_react_live_page(monkeypatch):
    import studio_app
    src = open(studio_app.__file__, encoding="utf-8").read()
    assert '"redirect": "/studio/live"' in src


def test_live_page_never_starts_a_normal_project(monkeypatch):
    import studio_app
    from test_studio_app import FakeAuth
    from recaply.jobs import store
    import clip_routes
    import live_routes
    store.init()
    monkeypatch.setattr(clip_routes, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "auth", FakeAuth)
    monkeypatch.setattr(studio_app, "DESKTOP", True)
    started = []
    monkeypatch.setattr(clip_routes, "_start", lambda *a, **k: started.append(a) or "0123456789")
    monkeypatch.setattr(live_routes, "available", lambda: True)
    monkeypatch.setattr(live_routes, "is_live_link", lambda url: "twitch.tv" in url)
    from recaply.live import links
    monkeypatch.setattr(links, "video_status", lambda url: "ended" if "OIeLbmyraRQ" in url else "video")
    watched = []
    monkeypatch.setattr(live_routes, "watch_link", lambda url, profile="gaming": watched.append(url) or "name")
    app = Flask(__name__)
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(studio_app.bp)
    c = app.test_client()
    ok = c.post("/api/studio/projects", json={"url": "https://www.twitch.tv/somebody", "live_only": True})
    assert ok.get_json() == {"live": True, "redirect": "/studio/live"} and watched == ["https://www.twitch.tv/somebody"]
    no = c.post("/api/studio/projects", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "live_only": True})
    assert no.status_code == 400 and "isn't live" in no.get_json()["error"] and started == []     # no project was created
    assert c.post("/api/studio/projects", json={"url": os.path.abspath(__file__), "live_only": True}).status_code == 400
    ended = c.post("/api/studio/projects", json={"url": "https://www.youtube.com/live/OIeLbmyraRQ", "live_only": True})
    assert ended.status_code == 200 and ended.get_json()["ended"] and ended.get_json()["id"] and len(started) == 1
    started.clear()                                                                   # an ended stream: clipped as a video
    normal = c.post("/api/studio/projects", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert normal.status_code == 200 and started                                                     # without live_only: a normal project
