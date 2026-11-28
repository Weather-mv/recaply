"""
Phase 3: multi-signal scoring. Audio features (shout / laughter / speech),
motion, chat replay (parsers and the chat-rate signal), signal groups in the
fusion, measured content-type detection and the funnel accounting. Synthetic
audio and video only, no network and no API keys.
"""

import json
import os
import subprocess
import wave

import numpy as np
import pytest

from conftest import FIXTURES
from recaply.ingest import chat as chat_ingest
from recaply.ingest.chat import ChatUnavailable, clean_messages, files, kick, twitch
from recaply.profiles import cues
from recaply.profiles.base import Detection
from recaply.score.fusion import fuse
from recaply.signals import audio_features as af
from recaply.signals import chat_rate, motion, shout_laugh

SR = 16000
SPEECH = os.path.join(FIXTURES, "speech.mp4")
BURSTS = os.path.join(FIXTURES, "bursts.mp4")


def write_wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """240 s of room noise with: a shout (100-103), laughter (150-154) and a low rumble (190-194)."""
    rng = np.random.default_rng(1)
    t = np.arange(240 * SR) / SR
    x = 0.02 * rng.standard_normal(len(t))
    voice = lambda f0: sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12))  # noqa: E731
    m = (t >= 100) & (t < 103)
    x[m] += (0.5 * voice(420) * np.minimum(1, (t - 100) * 20))[m] + 0.1 * rng.standard_normal(m.sum())
    env = np.clip(np.sin(2 * np.pi * 5.5 * t), 0, 1) ** 1.5
    m = (t >= 150) & (t < 154)
    x[m] += (0.25 * env * (voice(260) * 0.5 + rng.standard_normal(len(t)) * 0.5))[m]
    m = (t >= 190) & (t < 194)
    x[m] += (0.35 * np.sin(2 * np.pi * 60 * t) + 0.1 * np.sin(2 * np.pi * 90 * t))[m]
    path = tmp_path_factory.mktemp("audio") / "synth.wav"
    write_wav(path, x)
    return af.extract(str(path), 240, workers=2)


@pytest.fixture(scope="module")
def twenty_minutes(tmp_path_factory):
    """bursts.mp4 looped 10 times (loud at 40-44 s and 85-88 s of every 120 s): a 20-minute file."""
    p = str(tmp_path_factory.mktemp("long") / "twenty.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "9", "-i", BURSTS, "-c", "copy", p], check=True)
    return p


# ---------- audio features: shout, laughter, speech ----------

def test_feature_matrix_shape(synth):
    assert synth.shape == (240, len(af.COLS))
    assert np.isfinite(synth).all()


def test_laughter_is_detected(synth):
    laugh = shout_laugh.laugh_score(synth)
    assert laugh[150:154].max() > 0.8
    assert laugh[5:90].max() < 0.1 and laugh[200:235].max() < 0.1


def test_bright_shout_beats_a_low_rumble(synth):
    shout = shout_laugh.shout_score(synth)
    assert shout[100:103].max() > 0.9
    assert shout[190:194].max() < shout[100:103].max() - 0.15        # a rumble is loud but not bright
    assert shout[5:90].max() < 0.1


def test_combined_score_is_quiet_elsewhere_and_bounded(synth):
    s = shout_laugh.score(synth)
    assert s.shape == (240,) and 0 <= s.min() and s.max() <= 1
    assert s[100:103].max() > 0.8 and s[150:154].max() > 0.8
    assert s[200:235].max() < 0.1
    assert shout_laugh.score(np.zeros((0, 5), np.float32)).shape == (0,)


def test_speech_is_flagged_and_a_tone_is_not():
    speech = af.extract(SPEECH, 0, workers=2)
    assert speech[:30, 4].mean() > 0.6
    assert af.extract(BURSTS, 0, workers=2)[:, 4].mean() == 0.0
    assert cues.speech_ratio(speech) > 0.7


def test_chunk_boundaries_do_not_change_the_features(tmp_path, monkeypatch):
    """The same audio decoded as one chunk or as several gives (nearly) the same per-second features."""
    whole = af.extract(SPEECH, 0, workers=1)
    monkeypatch.setattr(af, "CHUNK_SEC", 10)
    parts = af.extract(SPEECH, 0, workers=2)
    assert whole.shape == parts.shape
    assert np.abs(whole[:, 0] - parts[:, 0])[:30].max() < 1.0          # loudness in dB
    assert np.abs(whole[:, 2] - parts[:, 2])[:30].mean() < 0.1         # modulation share


# ---------- motion ----------

def test_parse_scene_scores():
    text = ("frame:0    pts:0       pts_time:0\nlavfi.scene_score=0.000000\n"
            "frame:1    pts:1       pts_time:0.5\nlavfi.scene_score=0.250000\nnoise line\n"
            "frame:2    pts:2       pts_time:1.5\nlavfi.scene_score=0.100000\n")
    assert motion.parse_scene_scores(text) == [(0.0, 0.0), (0.5, 0.25), (1.5, 0.1)]


def test_seconds_hold_only_between_keyframes():
    out = motion.scores_to_seconds([(0.2, 0.1), (8.5, 0.4)], 12, hold=3)
    assert list(out[:5, 0]) == pytest.approx([0.1, 0.1, 0.1, 0.1, 0.0])      # the sample, held 3 s, then nothing
    assert out[8, 0] == pytest.approx(0.4) and out[9, 0] == pytest.approx(0.4)
    assert motion.scores_to_seconds([(0.2, 0.1)], 4)[1, 0] == 0.0               # no hold by default


def test_motion_scores_a_cut_and_fast_action_above_a_still_picture(tmp_path):
    still_then_busy = str(tmp_path / "m.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=10:duration=30",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=30", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-g", "20", "-preset",
                    "veryfast", still_then_busy], check=True)
    raw = motion.measure([(still_then_busy, {})], 60, workers=2)
    assert raw.shape == (60, 2)
    s = motion.score(raw)
    assert s[:28].max() == 0.0                          # a still picture scores nothing
    assert s[28:33].max() >= 0.4                        # the cut into moving content


def test_keyframe_gap_is_measured(tmp_path):
    p = str(tmp_path / "g.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=40",
                    "-c:v", "libx264", "-g", "20", "-keyint_min", "20", "-sc_threshold", "0", p], check=True)
    assert motion.keyframe_gap([(p, {})]) == pytest.approx(2.0, abs=0.1)
    assert motion.keyframe_gap([("/nonexistent.mp4", {})]) == float("inf")


# ---------- chat replay: parsers ----------

TWITCH_PAGE = {"data": {"video": {"comments": {"edges": [
    {"cursor": "c1", "node": {"contentOffsetSeconds": 12, "commenter": {"displayName": "Ann"},
                              "message": {"fragments": [{"text": "LETS "}, {"text": "GOOO"}]}}},
    {"cursor": "c2", "node": {"contentOffsetSeconds": 13, "commenter": None,
                              "message": {"fragments": [{"text": "kekw"}]}}},
    {"cursor": "c3", "node": {"contentOffsetSeconds": None, "message": {"fragments": [{"text": "x"}]}}},
], "pageInfo": {"hasNextPage": True}}}}}


def test_twitch_page_parsing():
    msgs, cursor, more = twitch.parse_page(TWITCH_PAGE)
    assert [m["text"] for m in msgs] == ["LETS GOOO", "kekw"] and msgs[0]["t"] == 12.0 and msgs[0]["user"] == "Ann"
    assert cursor == "c3" and more is True
    assert twitch.parse_page({"data": {"video": None}}) == ([], None, False)
    assert twitch.parse_page({"errors": [{"message": "x"}]}) == ([], None, False)


def test_twitch_query_shape(monkeypatch):
    sent = []
    monkeypatch.setattr(twitch, "post_json", lambda url, body, headers=None, **k: sent.append((url, body, headers)) or [{"ok": 1}])
    assert twitch._query("42", offset=90.7) == {"ok": 1}
    twitch._query("42", cursor="abc")
    (_, first, headers), (_, second, _) = sent
    assert headers["Client-ID"] and first[0]["variables"] == {"videoID": "42", "contentOffsetSeconds": 90}
    assert second[0]["variables"] == {"videoID": "42", "cursor": "abc"}      # a cursor replaces the offset


def test_twitch_fetch_pages_in_parallel_ranges(monkeypatch):
    calls = []

    def fake_query(video_id, offset=None, cursor=None):
        calls.append((video_id, offset, cursor))
        start = int(offset or 0)
        edges = [{"cursor": f"{start}-{i}", "node": {"contentOffsetSeconds": start + i, "commenter": {"displayName": f"u{i}"},
                                                    "message": {"fragments": [{"text": f"m{start + i}"}]}}} for i in range(5)]
        return {"data": {"video": {"comments": {"edges": edges, "pageInfo": {"hasNextPage": False}}}}}

    monkeypatch.setattr(twitch, "_query", fake_query)

    class Src:
        video_id, duration = "v123456", 800.0

    msgs = twitch.fetch(Src(), None)
    assert len(calls) == twitch.RANGES and all(c[0] == "123456" for c in calls)
    assert len(msgs) == 5 * twitch.RANGES and {m["t"] for m in msgs} >= {0.0, 100.0, 700.0}

    class Bad:
        video_id, duration = "oops", 10.0

    with pytest.raises(ChatUnavailable):
        twitch.fetch(Bad(), None)


def test_twitch_refusal_becomes_chat_unavailable(monkeypatch):
    from recaply.providers.http import ProviderError

    def boom(*a, **k):
        raise ProviderError("HTTP 403", 403)

    monkeypatch.setattr(twitch, "_query", boom)

    class Src:
        video_id, duration = "123", 100.0

    with pytest.raises(ChatUnavailable) as e:
        twitch.fetch(Src(), None)
    assert "refused" in str(e.value)


def test_youtube_live_chat_lines():
    line = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "65500", "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": "no way "}, {"emoji": {"shortcuts": [":fire:"]}}]},
            "authorName": {"simpleText": "Bo"}}}}},
        {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {}}}}]}})
    out = files.parse_live_chat_lines([line, "", "not json", json.dumps({"other": 1})])
    assert out == [{"t": 65.5, "text": "no way :fire:", "user": "Bo"}]


def test_kick_history_parsing():
    start = kick.parse_time("2025-03-01 18:00:00")
    assert start.tzinfo is not None and kick.parse_time("2025-03-01T18:00:00Z") == start
    payload = {"data": {"messages": [
        {"created_at": "2025-03-01T18:01:05.000Z", "content": "WOW", "sender": {"username": "x"}},
        {"created_at": "garbage", "content": "bad"}, {"created_at": "2025-03-01T18:00:10+00:00", "content": ""}]}}
    assert kick.parse_messages(payload, start) == [{"t": 65.0, "text": "WOW", "user": "x"}]
    assert kick.parse_messages({}, start) == []


def test_chat_files(tmp_path):
    td = tmp_path / "td.json"
    td.write_text(json.dumps({"comments": [
        {"content_offset_seconds": 5.5, "commenter": {"display_name": "A"}, "message": {"body": "LUL"}},
        {"content_offset_seconds": 7, "message": {"fragments": [{"text": "he"}, {"text": "y"}]}}]}))
    assert [(m["t"], m["text"]) for m in chat_ingest.load_file(str(td))] == [(5.5, "LUL"), (7.0, "hey")]
    plain = tmp_path / "p.json"
    plain.write_text(json.dumps([{"t": 3, "text": "a", "user": "u"}, {"time": 1, "message": "b"}]))
    assert [m["text"] for m in chat_ingest.load_file(str(plain))] == ["b", "a"]            # sorted
    yt = tmp_path / "yt.json"
    yt.write_text(json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "2000", "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "hi"}]}}}}}]}}) + "\n")
    assert chat_ingest.load_file(str(yt))[0]["t"] == 2.0


def test_clean_messages_dedupes_clips_and_sorts():
    raw = [{"t": 9, "text": "b"}, {"t": 1, "text": " a  "}, {"t": 1, "text": "a"}, {"t": -2, "text": "neg"},
           {"t": 500, "text": "late"}, {"t": "x", "text": "bad"}, {"t": 3, "text": ""}]
    assert [m["text"] for m in clean_messages(raw, duration=100)] == ["a", "b"]


class FakeSrc:
    def __init__(self, extractor="twitch", kind="url", key="t-1", live=False):
        self.extractor, self.kind, self.key, self.is_live, self.duration, self.video_id = extractor, kind, key, live, 100, "1"


def test_chat_fetch_degrades_gracefully(tmp_path, monkeypatch):
    from recaply.cache import StageCache
    notes = []
    cache = StageCache("chat-test-" + tmp_path.name)

    def unavailable(src, cache, progress=None):
        raise ChatUnavailable("this VOD has no chat replay")

    monkeypatch.setattr(twitch, "fetch", unavailable)
    assert chat_ingest.fetch(FakeSrc(), cache, note=notes.append) is None
    assert notes and "no chat replay" in notes[0]
    assert cache.get_json("chat", {"v": chat_ingest.VERSION}) == []         # remembered: not asked again
    monkeypatch.setattr(twitch, "fetch", lambda *a, **k: pytest.fail("asked again"))
    assert chat_ingest.fetch(FakeSrc(), cache) is None

    c2 = StageCache("chat-test2-" + tmp_path.name)
    monkeypatch.setattr(twitch, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert chat_ingest.fetch(FakeSrc(), c2, note=notes.append) is None      # unexpected errors never fail a job
    assert c2.get_json("chat", {"v": chat_ingest.VERSION}) is None            # ...and aren't cached
    monkeypatch.setattr(twitch, "fetch", lambda *a, **k: [{"t": 3, "text": "hi", "user": "u"}])
    assert chat_ingest.fetch(FakeSrc(), c2)[0]["text"] == "hi"

    assert chat_ingest.fetch(FakeSrc(kind="file"), c2) is None
    assert chat_ingest.fetch(FakeSrc(extractor="vimeo", key="v"), StageCache("v-" + tmp_path.name)) is None
    assert chat_ingest.fetch(FakeSrc(live=True, key="l"), StageCache("l-" + tmp_path.name)) is None
    assert chat_ingest.fetch(FakeSrc(key="d"), StageCache("d-" + tmp_path.name), enabled=False) is None


# ---------- chat-rate signal ----------

def chat_with_spike(n=600, at=300, quiet=0.4, burst=12, burst_len=6, hype=True, users=30):
    rng = np.random.default_rng(3)
    msgs = []
    for s in range(n):
        for _ in range(rng.poisson(quiet)):
            msgs.append({"t": s + rng.random(), "text": "random chatter about stuff", "user": f"u{rng.integers(0, 200)}"})
    for s in range(at, at + burst_len):
        for i in range(burst):
            msgs.append({"t": s + i / burst, "text": "LETS GOOO" if hype else "ok", "user": f"b{i % users}"})
    return msgs


def test_hype_detection():
    assert all(chat_rate.is_hype(t) for t in ["KEKW", "LETS GOOOO", "NO WAY!!", "pog pog pog pog", "💀💀", "omg wtf"])
    assert not any(chat_rate.is_hype(t) for t in ["i think the build is bad here", "what time does it start", "",
                                                  "no, i don't think so", "yes that works"])


def test_chat_spike_is_found_and_shifted_for_reaction_time():
    msgs = chat_with_spike(at=300)
    s = chat_rate.score(msgs, 600)
    assert s.shape == (600,) and 0 <= s.min() and s.max() <= 1
    peak = int(np.argmax(s))
    # chat typed during 300-306 describes what happened ~LAG seconds earlier
    assert 300 - chat_rate.LAG - 3 <= peak <= 306 - chat_rate.LAG + 3
    assert s[peak] > 0.8
    assert s[:250].max() < 0.35 and s[400:].max() < 0.35


def test_one_spammer_is_not_a_crowd():
    crowd = chat_rate.score(chat_with_spike(burst=5, users=40, hype=False), 600).max()
    spam = chat_rate.score(chat_with_spike(burst=5, users=1, hype=False), 600).max()
    assert spam < crowd


def test_quiet_chat_has_no_spikes_and_empty_chat_is_zero():
    assert chat_rate.score(chat_with_spike(burst=0), 600).max() < 0.3
    assert not chat_rate.score([], 100).any() and chat_rate.score([], 0).shape == (0,)


# ---------- fusion: correlated signals count once ----------

def test_audio_signals_do_not_boost_each_other():
    a = np.full(100, 0.7, np.float32)
    z = np.zeros(100, np.float32)
    same_mic = fuse({"audio_energy": a, "shout_laugh": a}, {"audio_energy": 1, "shout_laugh": 1}, 100, edges=False)
    assert np.allclose(same_mic, 0.7)                                       # no agreement bonus
    independent = fuse({"audio_energy": a, "chat_rate": a}, {"audio_energy": 1, "chat_rate": 1}, 100, edges=False)
    assert independent[50] > 0.7                                            # chat + audio agreeing is a real bonus
    mixed = fuse({"audio_energy": a, "shout_laugh": a, "chat_rate": z}, {"audio_energy": 1, "shout_laugh": 1, "chat_rate": 1},
                 100, edges=False)
    assert np.isclose(mixed[50], 1.4 / 3)


# ---------- content-type cues ----------

def test_weak_metadata_is_detected():
    assert cues.is_weak(Detection("irl", "none", {}))
    assert cues.is_weak(Detection("gaming", "x", {"gaming": 0.5}))
    assert not cues.is_weak(Detection("fps", "x", {"fps": 3.0}))


def test_cues_pick_a_profile_and_say_why():
    talk = cues.classify(cues.Cues(speech_ratio=0.85, chat_per_min=1.0, motion=0.004, face_area=0.08))
    assert talk.profile == "talk" and "mostly speech" in talk.reason
    game = cues.classify(cues.Cues(speech_ratio=0.2, chat_per_min=30.0, motion=0.05, face_area=0.01))
    assert game.profile == "gaming" and "active chat" in game.reason
    assert cues.classify(cues.Cues(speech_ratio=0.45)).profile == "irl"      # nothing decisive: the balanced profile
    assert cues.classify(cues.Cues(speech_ratio=0.8, chat_per_min=40, motion=0.05)).profile != "talk"


def test_auto_detection_uses_the_audio_and_picture(tmp_path):
    from recaply.engine import Options, analyze
    talk = analyze(SPEECH, Options(profile="auto", count=2, transcriber="none", llm="none"))
    assert talk["profile"]["key"] == "talk" and "measured" in talk["profile"]["reason"] and talk["cues"]["speech_ratio"] > 0.7
    game = analyze(BURSTS, Options(profile="auto", count=2, transcriber="none", llm="none"))
    assert game["profile"]["key"] == "gaming" and game["cues"]["motion"] is not None
    named = analyze(BURSTS, Options(profile="fps", count=2, transcriber="none", llm="none"))
    assert named["profile"]["reason"] == "chosen" and named["cues"] is None      # no measuring when the user chose


# ---------- the whole funnel ----------

def test_chat_file_joins_the_scoring(tmp_path, twenty_minutes):
    from recaply.engine import Options, analyze
    chat = tmp_path / "chat.json"
    msgs = chat_with_spike(n=1200, at=730, quiet=0.2, burst=10, burst_len=5)      # chat goes wild at 730-735 s, audio quiet
    chat.write_text(json.dumps([{"t": m["t"], "text": m["text"], "user": m["user"]} for m in msgs]))
    opts = dict(profile="gaming", count=30, shortlist=30, min_len=12, max_len=20, transcriber="none", llm="none")
    res = analyze(twenty_minutes, Options(**opts, chat_file=str(chat)))
    assert "chat_rate" in res["signals_used"] and res["chat_messages"] == len(msgs)
    assert not res["options"]["chat_file"]                                            # the local path isn't stored
    spans = [(c["start"], c["end"]) for c in res["clips"]]
    assert any(a <= 726 and b >= 730 for a, b in spans), spans                      # the chat-only moment is found
    assert any(c["signals"].get("chat_rate", 0) > 0.5 for c in res["clips"])
    covering = [c for c in res["clips"] if c["start"] <= 726 and c["end"] >= 730]
    assert covering and covering[0]["signals"].get("chat_rate", 0) > 0.5
    without = analyze(twenty_minutes, Options(**opts))
    plain = [c for c in without["clips"] if c["start"] <= 726 and c["end"] >= 730]
    assert not plain or plain[0]["score"] < covering[0]["score"]                    # chat made the moment rank higher


def test_missing_chat_is_noted_and_the_job_still_runs():
    from recaply.engine import Options, analyze
    res = analyze(BURSTS, Options(profile="gaming", count=3, transcriber="none", llm="none"))
    assert "chat_rate" not in res["signals_used"] and len(res["clips"]) >= 1
    assert any("chat_rate" in n for n in res["summary"]["notes"])


def test_signal_failure_falls_back_to_the_rest(monkeypatch):
    from recaply.engine import Options, analyze
    monkeypatch.setattr(shout_laugh.ShoutLaugh, "version", 99)                      # a fresh cache key: it really runs
    monkeypatch.setattr(shout_laugh.ShoutLaugh, "compute", lambda self, ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    res = analyze(BURSTS, Options(profile="gaming", count=3, transcriber="none", llm="none"))
    assert "shout_laugh" not in res["signals_used"] and "audio_energy" in res["signals_used"]
    assert any("shout_laugh skipped" in n for n in res["summary"]["notes"])


def test_funnel_transcribes_only_the_shortlist(monkeypatch, twenty_minutes):
    from recaply import engine
    from recaply.providers.transcribe.base import Transcriber

    class Fake(Transcriber):
        name = "fake"

        def transcribe_spans(self, audio_path, spans, vocab="", language=None, progress=None):
            return [[{"w": "wow", "s": a + 1, "e": a + 1.4}] for a, b in spans]

    monkeypatch.setattr(engine, "get_transcriber", lambda name=None, note=None: Fake())
    res = engine.analyze(twenty_minutes, engine.Options(profile="gaming", count=3, shortlist=3, min_len=12, max_len=20,
                                                         llm="none"))
    f = res["funnel"]
    assert f["audio_sec"] == pytest.approx(1200.0, abs=1.0) and f["windows"] == res["shortlist"] == 3
    assert 0 < f["transcribed_sec"] <= 3 * 120 and f["share"] < 0.35                 # 3 windows, in 30 s blocks
    talk = engine.analyze(twenty_minutes, engine.Options(profile="talk", count=2, llm="none"))
    assert 0 < talk["funnel"]["share"] < 0.6                 # talk content is probed, never transcribed in full
