"""Live-stream mode: detection, buffer, chat listeners, notifications, the channel loop and its routes."""

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

from recaply.live import chat, config, events, notify
from recaply.live.buffer import Buffer
from recaply.live.channel import ChannelWorker
from recaply.live.config import ConfigError
from recaply.live.detect import LiveFeatures, LiveScorer, SpikeDetector, chat_is_live
from recaply.profiles import get_profile

RATE = 16000


# ---------- helpers ----------

def synth(seconds=200, burst=(120, 124), seed=1):
    """Quiet brown-ish noise (a talker's baseline) with one loud bright burst, as int16-scale floats."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.normal(0, 1, seconds * RATE)).astype(np.float32)
    base = base / (np.abs(base).max() + 1e-9) * 0.02
    x = base.copy()
    a, b = burst
    x[a * RATE:b * RATE] += rng.normal(0, 0.4, (b - a) * RATE).astype(np.float32)
    return x * 32768.0


def serve(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Catcher(http.server.BaseHTTPRequestHandler):
    got: list = []
    status = 204

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Catcher.got.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
        self.send_response(Catcher.status)
        self.end_headers()

    def log_message(self, *a):
        pass


def ts_stream_cmd(duration=100, burst=(60, 64), size="320x180"):
    """ffmpeg command writing a synthetic live feed (quiet noise + one loud white-noise burst) to stdout."""
    return ["ffmpeg", "-v", "error", "-nostdin",
            "-f", "lavfi", "-i", f"color=c=black:s={size}:r=10:d={duration}",
            "-f", "lavfi", "-i", f"anoisesrc=d={duration}:c=brown:a=0.02:r=16000",
            "-f", "lavfi", "-i", f"anoisesrc=d={duration}:c=white:a=0.5:r=16000",
            "-filter_complex",
            f"[2:a]volume=enable='between(t,{burst[0]},{burst[1]})':volume=1,"
            f"volume=enable='not(between(t,{burst[0]},{burst[1]}))':volume=0[b];[1:a][b]amix=inputs=2:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
            "-c:a", "aac", "-f", "mpegts", "pipe:1"]


ONCE = """
import os, subprocess, sys
flag, cmd = sys.argv[1], sys.argv[2:]
if os.path.exists(flag):
    sys.exit(1)                  # a real stream that has ended is not there to reconnect to
open(flag, "w").close()
sys.exit(subprocess.call(cmd))
"""


def once(cmd):
    """Run the synthetic feed a single time: a second attempt (the channel reconnects after a drop) finds nothing,
    like a stream that has really ended, instead of replaying the same recording."""
    return [sys.executable, "-c", ONCE, tempfile.mktemp(prefix="feed_"), *cmd]


def make_cfg(**kw):
    d = {"name": "test", "url": "x", "source_cmd": ts_stream_cmd(), "segment_sec": 30, "warmup": 30, "cooldown": 10,
         "pre": 10, "post": 5, "end_grace": 10, "chat": False, "profile": "gaming"}
    d.update(kw)
    if d["source_cmd"] and d["source_cmd"][0] == "ffmpeg":
        d["source_cmd"] = once(d["source_cmd"])
    return config.validate(d)


# ---------- spike detector ----------

def test_spike_reported_once_at_the_peak_after_it_falls():
    v = np.zeros(300, np.float32)
    v[100:108] = [0.4, 0.6, 0.8, 0.95, 0.7, 0.6, 0.55, 0.3]
    det = SpikeDetector(threshold=0.5, cooldown=60, warmup=30)
    assert det.update(v, 0, 103) == []                    # still rising: not reported yet
    out = det.update(v, 0, 200)
    assert len(out) == 1 and out[0].t == 103 and out[0].score == 0.95
    assert det.update(v, 0, 300) == []                    # never reported twice


def test_spike_cooldown_warmup_and_long_runs():
    v = np.zeros(400, np.float32)
    v[10:14] = 0.9                                         # inside warmup: ignored
    v[100:104] = 0.9
    v[130:134] = 0.9                                       # 30 s after the last: inside the cooldown
    v[200:260] = 0.8                                       # a long burst is cut into reports of <= max_run
    out = SpikeDetector(0.5, 60, 60, max_run=25).update(v, 0, 400)
    assert [s.t for s in out][:1] == [100]
    assert all(s.t not in range(130, 134) for s in out)
    assert len([s for s in out if 200 <= s.t < 260]) == 1              # the cooldown spaces long bursts out too
    out = SpikeDetector(0.5, 20, 60, max_run=25).update(v, 0, 400)
    assert len([s for s in out if 200 <= s.t < 260]) >= 2


def test_spike_flush_reports_open_burst():
    v = np.zeros(100, np.float32)
    v[90:] = 0.9
    det = SpikeDetector(0.5, 10, 30)
    assert det.update(v, 0, 100) == []
    assert [s.t for s in det.flush(99)] == [90]


def test_spike_works_with_an_offset_window():
    v = np.zeros(100, np.float32)
    v[40:44] = 0.9                                         # absolute seconds 1040..1044
    out = SpikeDetector(0.5, 10, 30).update(v, 1000, 1100)
    assert len(out) == 1 and 1040 <= out[0].t < 1044


# ---------- features and scoring ----------

def test_live_features_in_pieces_match_one_pass():
    pcm = synth(150, burst=(100, 104))
    dec = lambda s, d: pcm[max(0, int(s * RATE)): int((s + d) * RATE)]           # noqa: E731
    a, b = LiveFeatures(dec), LiveFeatures(dec)
    for n in (40, 75, 111, 150):
        a.update(n)
    b.update(150)
    assert a.seconds == b.seconds == 149
    assert np.allclose(a.raw[:, :2], b.raw[:, :2], atol=1.0)
    assert np.allclose(a.raw[:, 0], b.raw[:, 0], atol=0.5)


def test_burst_scores_high_and_calm_stays_low():
    pcm = synth(200, burst=(120, 124))
    f = LiveFeatures(lambda s, d: pcm[max(0, int(s * RATE)): int((s + d) * RATE)])
    f.update(200)
    off, fused, sig = LiveScorer(get_profile("gaming")).fused(f.matrix(), [], False)
    assert set(sig) == {"audio_energy", "shout_laugh"}
    assert fused[118:128].max() > 0.7
    calm = np.concatenate([fused[40:110], fused[135:190]])
    assert calm.max() < 0.3


def test_chat_adds_a_signal_and_is_dropped_when_quiet():
    pcm = synth(200, burst=(120, 124))
    f = LiveFeatures(lambda s, d: pcm[max(0, int(s * RATE)): int((s + d) * RATE)])
    f.update(200)
    msgs = [{"t": 20.0 + i * 5, "text": "hello there", "user": f"u{i % 7}"} for i in range(30)]
    msgs += [{"t": 126 + i * 0.1, "text": "LUL LUL", "user": f"v{i}"} for i in range(80)]
    msgs.sort(key=lambda m: m["t"])
    sc = LiveScorer(get_profile("gaming"))
    _, _, sig = sc.fused(f.matrix(), msgs, True)
    assert "chat_rate" in sig and sig["chat_rate"][110:126].max() > 0.5       # shifted earlier by its reaction lag
    _, _, sig2 = sc.fused(f.matrix(), msgs, chat_is_live(msgs[:12], 700))
    assert "chat_rate" not in sig2                                              # too little recent chat: not trusted


# ---------- config ----------

def test_config_validation_and_secrets():
    c = config.validate({"name": "Chan_1", "url": "https://www.twitch.tv/foo", "pre": 999, "threshold": "0.4",
                         "discord": "https://discord.com/api/webhooks/1/2"})
    assert c.name == "chan_1" and c.pre == 120 and c.threshold == 0.4
    assert c.public()["discord"] is True and "discord.com" not in json.dumps(c.public())
    for bad in ({"name": "", "url": "https://twitch.tv/x"}, {"name": "a b", "url": "https://twitch.tv/x"},
                {"name": "a", "url": "http://evil.example/x"}, {"name": "a", "url": "file:///etc/passwd"},
                {"name": "a", "url": "https://twitch.tv/x", "profile": "nope"},
                {"name": "a", "url": "https://twitch.tv/x", "webhook": "ftp://x"}):
        with pytest.raises(ConfigError):
            config.validate(bad)


def test_config_save_load_remove():
    c = config.validate({"name": "savetest", "url": "https://www.twitch.tv/foo"})
    config.save(c)
    assert config.get("savetest").url == c.url
    assert config.remove("savetest") and config.get("savetest") is None and not config.remove("savetest")


# ---------- chat ----------

def test_parse_irc_and_youtube():
    line = "@badge-info=;color=#fff :nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :PogChamp that was insane"
    assert chat.parse_privmsg(line) == ("nick", "PogChamp that was insane")
    assert chat.parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None
    assert chat.twitch_channel("https://www.twitch.tv/SomeOne/videos") == "someone"
    resp = {"continuationContents": {"liveChatContinuation": {
        "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": "wow "}, {"emoji": {"shortcuts": [":fire:"]}}]},
            "authorName": {"simpleText": "bob"}}}}}, {"other": 1}],
        "continuations": [{"timedContinuationData": {"continuation": "NEXT", "timeoutMs": 4000}}]}}}
    msgs, nxt, wait = chat.parse_actions(resp)
    assert msgs == [("bob", "wow :fire:")] and nxt == "NEXT" and wait == 4.0
    assert chat.parse_actions({}) == ([], None, 3.0)


def test_twitch_listener_reads_irc():
    seen = []

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            self.rfile.readline()
            self.wfile.write(b"PING :tmi.twitch.tv\r\n")
            self.wfile.write(b":a!a@a.tmi.twitch.tv PRIVMSG #chan :first LUL\r\n")
            self.wfile.write(b":b!b@b.tmi.twitch.tv PRIVMSG #chan :second\r\n")
            self.wfile.flush()
            time.sleep(0.5)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tc = chat.TwitchChat("chan", lambda u, t: seen.append((u, t)), host="127.0.0.1", port=srv.server_address[1], tls=False)
    tc.start()
    deadline = time.time() + 5
    while len(seen) < 2 and time.time() < deadline:
        time.sleep(0.05)
    tc.stop()
    srv.shutdown()
    assert seen[:2] == [("a", "first LUL"), ("b", "second")]


def test_listener_for_platforms():
    assert isinstance(chat.listener_for("https://www.twitch.tv/foo", lambda *a: None)[0], chat.TwitchChat)
    assert isinstance(chat.listener_for("https://www.youtube.com/@foo/live", lambda *a: None)[0], chat.YouTubeChat)
    lc, why = chat.listener_for("https://kick.com/foo", lambda *a: None)
    assert lc is None and "audio only" in why


# ---------- notifications ----------

def test_notify_in_app_webhook_and_discord():
    Catcher.got.clear()
    srv = serve(Catcher)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    cfg = config.validate({"name": "notif", "url": "https://twitch.tv/x", "webhook": base + "/hook", "discord": base + "/discord"})
    notify.send(cfg, "spike", title="s", text="spike text")
    assert Catcher.got == []                                         # spikes stay in-app
    ev = notify.send(cfg, "clip_ready", title="Clip ready", text="@everyone big play", job="abc1234567")
    srv.shutdown()
    paths = dict(Catcher.got)
    assert paths["/hook"]["kind"] == "clip_ready" and paths["/hook"]["job"] == "abc1234567"
    d = paths["/discord"]
    assert d["allowed_mentions"] == {"parse": []} and d["embeds"][0]["description"].startswith("@everyone")
    kinds = [e["kind"] for e in events.list_events(channel="notif")]
    assert kinds == ["spike", "clip_ready"] and ev["id"]


def test_notify_survives_a_dead_webhook():
    cfg = config.validate({"name": "dead", "url": "https://twitch.tv/x", "webhook": "http://127.0.0.1:9/nope"})
    notify.send(cfg, "clip_ready", title="t", text="x")              # must not raise
    assert events.list_events(channel="dead")


# ---------- buffer ----------

def make_segments(folder, n=3, seconds=10):
    os.makedirs(folder, exist_ok=True)
    for i in range(n):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=160x90:r=10:d={seconds}",
                        "-f", "lavfi", "-i", f"sine=f={300 + 300 * i}:d={seconds}", "-c:v", "libx264", "-g", "5",
                        "-c:a", "aac", "-f", "mpegts", os.path.join(folder, f"seg_{i:06d}.ts")], check=True)


def test_buffer_lengths_cut_pcm_and_prune(tmp_path):
    folder = str(tmp_path / "buf")
    make_segments(folder)
    b = Buffer(folder)
    b.finished = True
    segs = b.refresh()
    assert len(segs) == 3 and 29 < b.length < 31 and b.next_index() == 3
    out = b.cut(8, 22, str(tmp_path / "c.mp4"))                       # spans all three segments
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                             capture_output=True, text=True).stdout)
    assert 13.0 < d < 15.0
    x = b.pcm(15, 2)                                                  # inside the 600 Hz segment
    assert len(x) == 2 * RATE and np.abs(x).max() > 1000
    assert b.prune(segs[1].start + 1) == 1 and abs(b.oldest - segs[1].start) < 0.5
    assert not os.path.exists(segs[0].path)
    with pytest.raises(RuntimeError):
        Buffer(str(tmp_path / "empty")).cut(0, 5, str(tmp_path / "x.mp4"))


def _tone(path, start, dur=1.0):
    """The loudest frequency (Hz) of [start, start+dur) of a file."""
    from recaply.ingest.audio import decode_pcm
    x = decode_pcm(path, start, dur, RATE)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * RATE / len(x))


def test_cut_jumps_into_a_long_segment_and_lands_on_time(tmp_path):
    folder = str(tmp_path / "buf")
    make_segments(folder, n=2, seconds=30)          # 300 Hz for 0-30, 600 Hz for 30-60
    b = Buffer(folder)
    b.finished = True
    b.refresh()
    assert all(s.pts0 > 0 for s in b._segs)         # the segment's own first timestamp is known
    out = b.cut(22, 38, str(tmp_path / "c.mp4"))     # starts 22 s into the first segment: a keyframe jump
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                             capture_output=True, text=True).stdout)
    assert 15.5 < d < 16.5
    assert abs(_tone(out, 0.5) - 300) < 30 and abs(_tone(out, 6.5) - 300) < 30     # 22..30 s: the first tone
    assert abs(_tone(out, 8.6) - 600) < 30 and abs(_tone(out, 14.5) - 600) < 30    # 30..38 s: the second


# ---------- the channel loop ----------

def run_worker(cfg, submit, tmp_path, timeout=120):
    w = ChannelWorker(cfg, root=str(tmp_path / "live"), submit=submit, tick=1)
    w.start()
    w.join(timeout)
    assert not w.is_alive()
    return w


def test_channel_catches_a_moment_cuts_it_and_stops_when_the_stream_ends(tmp_path):
    calls = []
    w = run_worker(make_cfg(name="e2e"), lambda c, p, i: calls.append((p, dict(i))) or "job1", tmp_path)
    assert w.state == "ended" and "ended" in w.note
    assert len(calls) == 1
    path, info = calls[0]
    assert 58 <= info["t"] <= 66 and info["score"] > 0.5 and info["signals"]["audio_energy"] > 0.5
    assert os.path.getsize(path) > 10_000 and 14 < info["end"] - info["start"] < 16.5    # pre 10 + post 5
    kinds = [e["kind"] for e in events.list_events(channel="e2e")]
    assert kinds == ["waiting", "recording", "spike", "clip_captured", "stream_ended"]
    assert not os.path.exists(w.buffer_dir)                                              # buffer cleaned up at the end


def test_a_frozen_connection_is_dropped_and_reconnected(tmp_path, monkeypatch):
    from recaply.live import channel as ch
    monkeypatch.setattr(ch, "STALL_SEC", 4.0)
    # 20 s at a real stream's bitrate (a tiny feed would sit in ffmpeg's start-up probe), then the link freezes
    feed = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
            "-f", "lavfi", "-i", "sine=f=440:d=20", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M",
            "-c:a", "aac", "-f", "mpegts", "pipe:1"]
    hang =[sys.executable, "-c", "import subprocess, sys, time; subprocess.call(sys.argv[1:]); time.sleep(600)", *feed]
    cfg = make_cfg(name="frozen", source_cmd=hang, end_grace=600)
    w = ChannelWorker(cfg, root=str(tmp_path / "live"), submit=lambda *a: "x", tick=1)
    w.start()
    end = time.time() + 60
    while time.time() < end and not (w.stalls and w.reconnects):
        time.sleep(0.5)
    w.stop()
    w.join(30)
    assert w.stalls >= 1 and w.reconnects >= 1 and not w.is_alive()


def test_channel_that_is_not_live_ends_when_waiting_is_off(tmp_path):
    cfg = make_cfg(name="offline", source_cmd=["python3", "-c", "import sys; sys.exit(1)"], wait_live=False)
    w = run_worker(cfg, lambda *a: "x", tmp_path, 60)
    assert w.state == "ended" and "isn't live" in w.note and w.clips == []


def test_a_broadcast_that_has_ended_is_not_recorded(monkeypatch):
    from recaply.live import links
    import live_routes as lr
    status = {"https://www.youtube.com/live/OIeLbmyraRQ": "ended", "https://www.youtube.com/live/QjbepgVChIA": "live",
              "https://www.youtube.com/watch?v=soonsoonsoo": "upcoming", "https://youtu.be/dQw4w9WgXcQ": "video"}
    monkeypatch.setattr(links, "video_status", status.get)
    assert not lr.is_live_link("https://www.youtube.com/live/OIeLbmyraRQ")          # was live: a video now
    assert lr.is_live_link("https://www.youtube.com/live/QjbepgVChIA")
    assert not lr.is_live_link("https://www.youtube.com/watch?v=soonsoonsoo")       # watch links: clipped as videos, no lookup
    assert not lr.is_live_link("https://youtu.be/dQw4w9WgXcQ")
    assert lr.is_live_link("https://www.youtube.com/@VALORANT/live")                # channels: no lookup needed
    assert links.ended("https://www.youtube.com/live/OIeLbmyraRQ") and not links.ended("https://www.twitch.tv/somebody")


def test_live_checks_are_cached_per_video(monkeypatch):
    from recaply.live import links
    calls = []
    monkeypatch.setattr(links, "_lookup", lambda url: calls.append(url) or "ended")
    links.forget()
    assert links.video_status("https://www.youtube.com/live/OIeLbmyraRQ") == "ended"
    assert links.video_status("https://youtu.be/OIeLbmyraRQ") == "ended"            # same video, other link shape
    assert links.ended("https://www.youtube.com/watch?v=OIeLbmyraRQ&t=10")
    assert len(calls) == 1
    monkeypatch.setattr(links, "_lookup", lambda url: calls.append(url) or None)    # unknown answers aren't kept
    links.video_status("https://youtu.be/abcdefghijk")
    links.video_status("https://youtu.be/abcdefghijk")
    assert len(calls) == 3
    calls.clear()
    assert not links.should_watch("https://www.youtube.com/watch?v=zzzzzzzzzzz") and calls == []
    links.forget()


def test_a_saved_channel_for_an_ended_broadcast_stops_instead_of_waiting(monkeypatch, tmp_path):
    from recaply.live import links
    monkeypatch.setattr(links, "video_status", lambda url: "ended")
    cfg = make_cfg(name="oldstream", url="https://www.youtube.com/live/OIeLbmyraRQ",
                   source_cmd=["python3", "-c", "import sys; sys.exit(1)"], poll_sec=60)
    w = run_worker(cfg, lambda *a: "x", tmp_path, 15)
    assert w.state == "ended" and "has ended" in w.note and w.clips == []


def test_waiting_channel_can_be_stopped(tmp_path):
    cfg = make_cfg(name="waiter", source_cmd=["python3", "-c", "import sys; sys.exit(1)"], poll_sec=60)
    w = ChannelWorker(cfg, root=str(tmp_path / "live"), submit=lambda *a: "x", tick=1)
    w.start()
    time.sleep(2.5)
    assert w.state == "waiting"
    w.stop()
    w.join(15)
    assert not w.is_alive() and w.state == "stopped"


def test_a_moment_still_waiting_for_its_after_part_is_cut_when_the_stream_ends(tmp_path):
    calls = []
    cfg = make_cfg(name="midstop", post=40, source_cmd=ts_stream_cmd(duration=75, burst=(60, 64)))
    w = run_worker(cfg, lambda c, p, i: calls.append(dict(i)) or "j", tmp_path)
    assert w.state == "ended" and len(calls) == 1
    assert calls[0]["end"] <= 75.5 and calls[0]["end"] - calls[0]["t"] < 20            # cut what was recorded, not the full 40 s


# ---------- the clip pipeline ----------

def test_follow_sends_clip_ready_when_the_job_finishes(monkeypatch):
    from recaply.jobs import store
    from recaply.live import pipeline
    monkeypatch.setattr(pipeline, "POLL", 0.05)
    Catcher.got.clear()
    srv = serve(Catcher)
    cfg = config.validate({"name": "follow", "url": "https://twitch.tv/x", "discord": f"http://127.0.0.1:{srv.server_address[1]}/d"})
    store.init()
    jid = store.create("/tmp/x.mp4", {}, title="t")
    threading.Timer(0.3, lambda: store.update(jid, state="ready", task="analyze", result={"clips": [
        {"id": 0, "title": "Insane clutch", "score": 0.91, "duration": 28.0}]})).start()
    pipeline.follow(cfg, jid, {"t": 61, "clock": "0:01:01", "file": "/x/clip_000061.mp4", "signals": {"audio_energy": 1.0}})
    srv.shutdown()
    ev = events.list_events(channel="follow")[-1]
    assert ev["kind"] == "clip_ready" and ev["clip_title"] == "Insane clutch" and ev["job"] == jid
    assert "Insane clutch" in Catcher.got[0][1]["embeds"][0]["description"]


def test_follow_reports_a_failed_job(monkeypatch):
    from recaply.jobs import store
    from recaply.live import pipeline
    monkeypatch.setattr(pipeline, "POLL", 0.05)
    cfg = config.validate({"name": "followfail", "url": "https://twitch.tv/x"})
    store.init()
    jid = store.create("/tmp/x.mp4", {})
    store.update(jid, state="failed", task="analyze", error="boom")
    pipeline.follow(cfg, jid, {"t": 5, "clock": "0:00:05", "file": "/x/c.mp4"})
    ev = events.list_events(channel="followfail")[-1]
    assert ev["kind"] == "error" and "boom" in ev["text"]


def test_submit_clip_creates_a_job_for_the_clip(monkeypatch, tmp_path):
    from recaply.jobs import store
    from recaply.live import pipeline
    queued = []

    class Q:
        def submit(self, jid, task="analyze", **kw):
            queued.append((jid, task))

    monkeypatch.setattr(pipeline, "get_queue", lambda: Q())
    monkeypatch.setattr(pipeline, "follow", lambda *a: None)
    cfg = config.validate({"name": "sub", "url": "https://twitch.tv/x", "profile": "fps", "count": 2, "pre": 30, "post": 15})
    jid = pipeline.submit_clip(cfg, str(tmp_path / "c.mp4"), {"clock": "0:10:00"})
    job = store.get(jid)
    assert queued == [(jid, "analyze")] and job["input"].endswith("c.mp4")
    assert job["options"]["profile"] == "fps" and job["options"]["count"] == 2 and job["options"]["max_len"] == 45.0
    assert "0:10:00" in job["title"]


# ---------- routes ----------

class FakeMgr:
    def __init__(self):
        self.started, self.stopped = [], []

    def start(self, cfg):
        self.started.append(cfg.name)

    def start_saved(self, name):
        if config.get(name) is None:
            raise ConfigError("No channel called " + name)
        self.started.append(name)

    def stop(self, name, wait=0):
        self.stopped.append(name)
        return True

    def status(self):
        return [{"name": n, "state": "idle", "clips": []} for n in config.load_all()]


def test_live_routes(monkeypatch):
    from flask import Flask
    import live_routes
    mgr = FakeMgr()
    monkeypatch.setattr(live_routes, "DESKTOP", True)
    monkeypatch.setattr(live_routes, "get_manager", lambda: mgr)
    monkeypatch.setattr(live_routes, "auth", type("A", (), {"uid": staticmethod(lambda: 1)}))
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(live_routes.__file__), "templates"))
    app.jinja_env.globals.update(csrf_token=lambda: "t", user=None, ent=None, plans={})
    app.register_blueprint(live_routes.bp)
    c = app.test_client()
    r = c.post("/api/live/channels", json={"name": "rt", "url": "https://www.twitch.tv/foo", "discord": "https://discord.com/api/webhooks/1/2", "start": True})
    assert r.status_code == 200 and mgr.started == ["rt"] and r.get_json()["channel"]["discord"] is True
    r = c.post("/api/live/channels", json={"name": "rt", "url": "https://www.twitch.tv/foo", "pre": 20})   # blank webhook keeps the saved one
    assert config.get("rt").discord.endswith("/1/2") and config.get("rt").pre == 20
    assert c.post("/api/live/channels", json={"name": "bad name", "url": "x"}).status_code == 400
    s = c.get("/api/live").get_json()
    assert [x["name"] for x in s["channels"]] == ["rt"] and "discord.com" not in json.dumps(s)
    assert c.post("/api/live/channels/rt/stop").get_json()["ok"] and mgr.stopped == ["rt"]
    assert c.post("/api/live/channels/ghost/start").status_code == 400
    assert c.post("/api/live/channels/BAD NAME/start").status_code == 404
    assert c.delete("/api/live/channels/rt").get_json()["ok"] and config.get("rt") is None
    monkeypatch.setattr(live_routes, "LIVE_OK", False)
    assert c.get("/api/live", base_url="http://example.com").status_code == 404      # not offered on a shared online server
    assert c.get("/api/live", base_url="http://localhost:5000").status_code == 200   # but yes on this computer, whatever HOST says
    monkeypatch.setenv("RECAPLY_LIVE", "0")
    assert c.get("/api/live", base_url="http://localhost:5000").status_code == 404


def _live_client(monkeypatch, uid=1):
    from flask import Flask
    import live_routes
    mgr = FakeMgr()
    mgr.running = lambda n: n in mgr.started
    monkeypatch.setattr(live_routes, "LIVE_OK", True)
    monkeypatch.setattr(live_routes, "get_manager", lambda: mgr)
    monkeypatch.setattr(live_routes, "auth", type("A", (), {"uid": staticmethod(lambda: uid)}))
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(live_routes.__file__), "templates"))
    app.jinja_env.globals.update(csrf_token=lambda: "t", user=None, ent=None, plans={})
    app.register_blueprint(live_routes.bp)
    return live_routes, mgr, app.test_client()


def test_live_links_are_recognised_and_watched(monkeypatch):
    from recaply.live import links
    lr, mgr, c = _live_client(monkeypatch)
    monkeypatch.setattr(links, "video_status", lambda url: "live")
    for u in ("https://www.youtube.com/live/QjbepgVChIA", "https://youtube.com/@VALORANT/live",
              "https://www.twitch.tv/somechannel", "https://kick.com/some-channel"):
        assert lr.is_live_link(u), u
    for u in ("https://www.twitch.tv/videos/123", "https://www.twitch.tv/somechannel/clip/x", "https://example.com/live/x", ""):
        assert not lr.is_live_link(u), u
    url = "https://www.youtube.com/live/QjbepgVChIA"
    name = lr.watch_link(url, "auto")
    assert config.NAME.match(name) and name == lr.watch_link(url, "auto")        # stable name
    cfg = config.get(name)
    assert cfg.owner == 1 and cfg.profile == "gaming" and mgr.started == [name]
    config.remove(name)


def test_live_channels_belong_to_their_owner(monkeypatch):
    lr, mgr, c = _live_client(monkeypatch, uid=1)
    assert c.post("/api/live/channels", json={"name": "mine", "url": "https://www.twitch.tv/foo"}).status_code == 200
    assert config.get("mine").owner == 1
    lr2, mgr2, other = _live_client(monkeypatch, uid=2)
    assert [x["name"] for x in other.get("/api/live").get_json()["channels"]] == []
    assert other.post("/api/live/channels/mine/start").status_code == 404
    assert other.delete("/api/live/channels/mine").status_code == 404
    assert other.post("/api/live/channels", json={"name": "mine", "url": "https://www.twitch.tv/bar"}).status_code == 400
    assert config.get("mine").url.endswith("/foo")
    config.remove("mine")


def test_live_clip_jobs_carry_the_owner(monkeypatch):
    from recaply.live import pipeline
    from recaply.jobs import store
    store.init()
    monkeypatch.setattr(pipeline.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda s: None})())
    monkeypatch.setattr(pipeline, "get_queue", lambda: type("Q", (), {"submit": lambda s, *a, **k: None})())
    cfg = config.validate({"name": "own", "url": "https://www.twitch.tv/foo", "owner": 7})
    jid = pipeline.submit_clip(cfg, "/tmp/x.mp4", {"clock": "0:10"})
    assert store.get(jid)["owner"] == 7
