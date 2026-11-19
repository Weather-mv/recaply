"""Phase 5 editing: transcript cuts, translation, B-roll, and the edit task end to end."""

import http.server
import json
import os
import shutil
import subprocess
import threading

import pytest

from conftest import FIXTURES
from recaply.edit import broll, translate, transcript as tx
from recaply.providers.http import ProviderError
from recaply.providers.llm import Usage

SPEECH = os.path.join(FIXTURES, "speech.mp4")
DUR = 34.7


def fake_words(n=40, dur=DUR, start=0.5):
    """n words, 0.5 s each with a small gap, spread over the clip."""
    step = (dur - start - 1) / n
    return [{"w": f"word{i}", "s": round(start + i * step, 3), "e": round(start + i * step + step * 0.7, 3)} for i in range(n)]


def vdur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return float(out)


# ---------- transcript editing ----------

def test_nothing_deleted_keeps_everything():
    assert tx.keep_segments(fake_words(), set(), DUR) == [(0.0, DUR)]


def test_middle_run_is_cut_and_neighbours_stay_joined():
    w = fake_words()
    segs = tx.keep_segments(w, {10, 11, 12}, DUR)
    assert len(segs) == 2 and segs[0][0] == 0.0 and segs[1][1] == DUR
    assert w[9]["e"] <= segs[0][1] <= w[10]["s"] and w[12]["e"] <= segs[1][0] <= w[13]["s"]
    assert abs(tx.edited_duration(segs) - (DUR - (segs[1][0] - segs[0][1]))) < 0.01


def test_start_and_end_deletions_trim_the_clip():
    w = fake_words()
    segs = tx.keep_segments(w, {0, 1, 2, 38, 39}, DUR)
    assert len(segs) == 1 and segs[0][0] >= w[2]["e"] - 0.001 and segs[0][1] <= w[38]["s"] + 0.001


def test_everything_deleted_gives_no_segments_and_render_refuses(tmp_path):
    w = fake_words(5)
    assert tx.keep_segments(w, set(range(5)), DUR) == []
    with pytest.raises(ValueError):
        tx.render(SPEECH, str(tmp_path / "x.mp4"), [])


def test_separate_deletions_make_separate_cuts_and_bad_ids_are_ignored():
    w = fake_words()
    segs = tx.keep_segments(w, {5, 20, 999, -3}, DUR)
    assert len(segs) == 3
    assert all(a < b for a, b in segs) and all(segs[i][1] <= segs[i + 1][0] for i in range(2))


def test_retime_matches_the_edited_clip():
    w = fake_words()
    dele = {4, 5, 6, 20}
    segs = tx.keep_segments(w, dele, DUR)
    new = tx.retime(w, dele, segs)
    assert [x["w"] for x in new] == [x["w"] for i, x in enumerate(w) if i not in dele]
    assert all(a["s"] <= b["s"] for a, b in zip(new, new[1:]))
    assert new[-1]["e"] <= tx.edited_duration(segs) + 0.01 and new[0]["s"] >= 0
    gap_before = new[4]["s"] - new[3]["e"]                     # words 3 and 7 are neighbours now: the cut leaves no hole
    assert gap_before < 0.35


def test_filler_ids():
    w = [{"w": "so", "s": 0, "e": 1}, {"w": "um,", "s": 1, "e": 2}, {"w": "Uh", "s": 2, "e": 3}, {"w": "yes", "s": 3, "e": 4}]
    assert tx.filler_ids(w) == [1, 2]


def test_render_cuts_the_video_to_the_planned_length(tmp_path):
    w = fake_words()
    dele = set(range(8, 16))
    segs = tx.keep_segments(w, dele, DUR)
    dst = str(tmp_path / "e.mp4")
    tx.render(SPEECH, dst, segs)
    assert abs(vdur(dst) - tx.edited_duration(segs)) < 0.4
    assert vdur(dst) < DUR - 3


# ---------- translation ----------

class FakeLLM:
    provider = "fake"

    def __init__(self, reply=None):
        self.calls, self.reply = [], reply

    def complete_json(self, system, prompt, max_tokens=8000):
        self.calls.append(prompt)
        lines = [l.split(". ", 1)[1] for l in prompt.splitlines() if l[:1].isdigit() and ". " in l]
        out = self.reply(lines) if self.reply else [f"ES:{l}" for l in lines]
        return {"lines": out}, Usage("fake", "m", 100, 20)


def test_segments_split_on_pause_punctuation_and_length():
    w = [{"w": "hello", "s": 0, "e": 0.4}, {"w": "there.", "s": 0.5, "e": 0.9}, {"w": "next", "s": 1.0, "e": 1.3},
         {"w": "one", "s": 3.0, "e": 3.3}]
    segs = translate.segments(w)
    assert [translate.seg_text(s) for s in segs] == ["hello there.", "next", "one"]
    many = [{"w": f"w{i}", "s": i * 0.3, "e": i * 0.3 + 0.2} for i in range(30)]
    assert [len(s) for s in translate.segments(many)] == [12, 12, 6]


def test_translated_words_keep_the_segment_timing():
    from recaply.telemetry import Telemetry
    w = [{"w": "hello", "s": 1.0, "e": 1.4}, {"w": "there", "s": 1.5, "e": 1.9}, {"w": "friend.", "s": 2.0, "e": 2.6},
         {"w": "bye", "s": 5.0, "e": 5.4}]
    tel, llm = Telemetry(), FakeLLM(lambda l: ["hola a todos amigos", "adios"])
    out = translate.translate_words(w, "es", llm, tel)
    assert [x["w"] for x in out] == ["hola", "a", "todos", "amigos", "adios"]
    assert out[0]["s"] == 1.0 and out[3]["e"] <= 2.6 and 5.0 <= out[4]["s"] < 5.5
    assert all(a["s"] < a["e"] for a in out) and tel.tokens["fake"]["in"] == 100 and len(llm.calls) == 1


def test_translation_survives_a_wrong_line_count():
    w = [{"w": "one.", "s": 0, "e": 1}, {"w": "two.", "s": 2, "e": 3}, {"w": "three.", "s": 4, "e": 5}]
    out = translate.translate_lines(FakeLLM(lambda l: ["uno"]), [translate.seg_text(s) for s in translate.segments(w)], "es")
    assert out == ["uno", "two.", "three."]


def test_translation_errors_are_clear():
    w = [{"w": "hi", "s": 0, "e": 1}]
    with pytest.raises(ProviderError, match="AI key"):
        translate.translate_words(w, "es", None)
    with pytest.raises(ProviderError, match="Unknown language"):
        translate.translate_words(w, "xx", FakeLLM())
    assert translate.translate_words([], "es", None) == []


def test_no_space_scripts_spread_characters():
    out = translate.spread("こんにちは世界", 0, 3.5, "ja")
    assert len(out) == 4 and out[0]["s"] == 0 and "".join(x["w"] for x in out) == "こんにちは世界"


def test_srt_and_vtt():
    w = [{"w": "hello", "s": 0.0, "e": 0.5}, {"w": "world.", "s": 0.6, "e": 1.234}, {"w": "again", "s": 4.0, "e": 4.5}]
    srt, vtt = translate.to_srt(w), translate.to_vtt(w)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,234\nhello world.\n\n2\n00:00:04,000 --> 00:00:04,500\nagain")
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.234\nhello world.")
    long = [{"w": "word" + str(i), "s": i * 0.2, "e": i * 0.2 + 0.15} for i in range(11)]
    assert "\n" in translate.cues(long)[0][2]


# ---------- B-roll ----------

def test_heuristic_moments_are_spaced_and_avoid_the_edges():
    text = ("so yesterday we went to the mountain together and honestly the weather turned terrible quickly "
            "then the helicopter arrived above everyone while thunderstorm clouds gathered around ").split()
    w = [{"w": t + ("." if i % 7 == 6 else ""), "s": 1.0 + i * 0.5, "e": 1.4 + i * 0.5} for i, t in enumerate(text)]
    spots = broll.heuristic_moments(w, 25.0)
    assert 1 <= len(spots) <= 3
    assert all(s["t"] >= 2.0 and s["t"] + broll.INSERT_SEC <= 23.5 for s in spots)
    assert all(b["t"] - a["t"] >= broll.MIN_GAP for a, b in zip(spots, spots[1:]))
    assert all(len(s["query"]) >= 6 for s in spots)


def test_llm_moments_are_used_and_failures_fall_back():
    w = [{"w": f"w{i}", "s": 1.0 + i * 0.5, "e": 1.4 + i * 0.5} for i in range(40)]

    class L:
        def complete_json(self, s, p, max_tokens=0):
            return {"moments": [{"t": 3.0, "query": "mountain"}, {"t": 4.0, "query": "too close"}, {"t": 12.0, "query": "city"}]}, Usage("g", "m", 5, 5)

    assert [m["query"] for m in broll.moments(w, 25.0, L())] == ["mountain", "city"]

    class Boom:
        def complete_json(self, *a, **k):
            raise ProviderError("down")

    assert broll.moments(w, 25.0, Boom()) == broll.heuristic_moments(w, 25.0)
    assert broll.moments([], 25.0, L()) == []


@pytest.fixture(scope="module")
def media_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("srv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(d / "red.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=200x200:d=1", "-frames:v", "1", str(d / "g.png")], check=True)
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path.startswith("/videos/search"):
                body = json.dumps({"videos": [{"user": {"name": "Ann"}, "video_files": [
                    {"file_type": "video/mp4", "width": 1280, "link": base + "/red.mp4"}]}]}).encode()
            elif self.path.startswith("/v1/images/"):
                body = json.dumps({"results": [{"url": base + "/g.png", "title": "A park", "license": "cc0"}]}).encode()
            else:
                try:
                    body = (d / self.path.lstrip("/")).read_bytes()
                except OSError:
                    self.send_response(404)
                    self.end_headers()
                    return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


def test_pexels_and_openverse_find_and_download(media_server, tmp_path):
    base, hits = media_server
    a = broll.Pexels("KEY", base).find("mountain", str(tmp_path), "b0")
    assert a.kind == "video" and os.path.getsize(a.path) > 1000 and "Ann" in a.credit
    assert any(auth == "KEY" for _, auth in hits)
    o = broll.Openverse(base).find("park", str(tmp_path), "b1")
    assert o.kind == "image" and "CC0" in o.credit
    assert "license=cc0%2Cpdm" in [p for p, _ in hits if p.startswith("/v1/images/")][0]


def test_provider_choice_and_download_limits(monkeypatch, tmp_path):
    monkeypatch.setenv("RECAPLY_PEXELS_KEY", "k")
    assert broll.provider().name == "pexels"
    monkeypatch.delenv("RECAPLY_PEXELS_KEY")
    assert broll.provider().name == "openverse"
    assert not broll._download("file:///etc/passwd", str(tmp_path / "x"), 5)
    assert not broll._download("http://127.0.0.1:9/none", str(tmp_path / "y"), 5)


def test_overlay_covers_the_chosen_seconds_and_keeps_the_sound(media_server, tmp_path):
    base, _ = media_server
    a = broll.Pexels("K", base).find("x", str(tmp_path), "b0")
    out = str(tmp_path / "o.mp4")
    broll.overlay(SPEECH, out, [({"t": 5.0, "query": "x"}, a)], (160, 90), True, sec=3.0)
    assert abs(vdur(out) - DUR) < 0.4

    def red_at(path, t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
                              "-f", "rawvideo", "pipe:1"], capture_output=True).stdout
        return raw[0] > 200 and raw[1] < 60                       # a red pixel
    assert red_at(out, 6.0) and not red_at(out, 10.0) and not red_at(out, 2.0)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams


def test_image_asset_overlay(media_server, tmp_path):
    base, _ = media_server
    a = broll.Openverse(base).find("x", str(tmp_path), "b0")
    out = str(tmp_path / "i.mp4")
    broll.overlay(SPEECH, out, [({"t": 4.0, "query": "x"}, a)], (160, 90), True, sec=2.0)
    assert abs(vdur(out) - DUR) < 0.4


def test_add_reports_when_nothing_is_found(tmp_path):
    class Empty:
        def find(self, *a):
            return None

    w = [{"w": "mountain" + str(i), "s": 2 + i * 0.6, "e": 2.4 + i * 0.6} for i in range(30)]
    r = broll.add(SPEECH, str(tmp_path / "x.mp4"), w, DUR, (160, 90), True, str(tmp_path / "b"), None, None, prov=Empty())
    assert r["file"] is None and "no B-roll" in r["note"]
    assert broll.add(SPEECH, str(tmp_path / "x.mp4"), [], DUR, (160, 90), True, str(tmp_path / "b"))["file"] is None


# ---------- the edit task ----------

def make_result(cids=(0,)):
    return {"source": {"key": "k", "kind": "file", "duration": 100.0, "title": "t", "location": "x"},
            "profile": {"key": "gaming"}, "options": {},
            "clips": [{"id": c, "start": 10.0, "end": 10.0 + DUR, "duration": DUR, "title": "Clip " + str(c), "rank": c + 1} for c in cids]}


def test_edit_clip_end_to_end(monkeypatch, tmp_path):
    from recaply import engine
    out = str(tmp_path)
    shutil.copy(SPEECH, os.path.join(out, "cut0.mp4"))
    words = fake_words()
    monkeypatch.setattr(engine, "clip_words", lambda result, clip, tel=None: [dict(w) for w in words])
    r = engine.edit_clip(make_result(), 0, [3, 4, 5, 6, 7, 99999], out, cut_file="cut0.mp4")
    assert r["file"] == "edit_0.mp4" and r["deleted"] == [3, 4, 5, 6, 7] and r["removed_sec"] > 2
    assert abs(vdur(os.path.join(out, "edit_0.mp4")) - r["duration"]) < 0.05
    assert [w["w"] for w in r["words"]][:4] == ["word0", "word1", "word2", "word8"]
    assert r["words"][-1]["e"] <= r["duration"] + 0.05


def test_edit_clip_with_broll_and_error_paths(monkeypatch, tmp_path, media_server):
    from recaply import engine
    from recaply.ingest.sources import InputError
    base, _ = media_server
    out = str(tmp_path)
    shutil.copy(SPEECH, os.path.join(out, "cut0.mp4"))
    words = [{"w": "mountain" + str(i), "s": 1 + i * 0.7, "e": 1.5 + i * 0.7} for i in range(45)]
    monkeypatch.setattr(engine, "clip_words", lambda result, clip, tel=None: [dict(w) for w in words])
    monkeypatch.setattr(broll, "provider", lambda: broll.Pexels("K", base))
    r = engine.edit_clip(make_result(), 0, [0, 1], out, cut_file="cut0.mp4", broll=True)
    assert r["broll"] and r["broll"][0]["source"] == "Pexels" and abs(vdur(os.path.join(out, "edit_0.mp4")) - r["duration"]) < 0.05
    with pytest.raises(InputError):
        engine.edit_clip(make_result(), 7, [], out, cut_file="cut0.mp4")
    monkeypatch.setattr(engine, "clip_words", lambda *a, **k: [])
    with pytest.raises(InputError, match="no transcript"):
        engine.edit_clip(make_result(), 0, [], out, cut_file="cut0.mp4")


def test_runner_edit_task_records_state_and_shorts_use_the_edit(monkeypatch, tmp_path):
    from recaply import engine
    from recaply.jobs import runner, store
    store.init()
    jid = store.create("x", {}, title="job")
    os.makedirs(runner.job_dir(jid), exist_ok=True)
    shutil.copy(SPEECH, os.path.join(runner.job_dir(jid), "cut0.mp4"))
    store.update(jid, state="ready", result=make_result(), task_args={"clip": 0, "deleted": [2, 3], "broll": False})
    store.set_cut(jid, 0, {"state": "done", "file": "cut0.mp4", "quality": "1080"})
    words = fake_words()
    monkeypatch.setattr(engine, "clip_words", lambda result, clip, tel=None: [dict(w) for w in words])
    runner._edit(store.get(jid))
    j = store.get(jid)
    e = j["cuts"]["e0"]
    assert e["state"] == "done" and e["file"] == "edit_0.mp4" and e["deleted"] == [2, 3] and j["state"] == "ready"
    assert os.path.exists(runner.edit_words_path(jid, 0))

    seen = {}

    def fake_render(result, ids, out_dir, opts, **kw):
        seen.update(kw, ids=ids)
        return {i: {"file": "short_00.mp4", "duration": 10, "format": "9:16", "cut_file": kw["cut_files"][i]} for i in ids}

    monkeypatch.setattr(runner, "render_shorts", fake_render)
    store.update(jid, task_args={"ids": [0], "options": {}})
    runner._shorts(store.get(jid))
    assert seen["cut_files"][0] == "edit_0.mp4" and [w["w"] for w in seen["words_for"][0]][:3] == ["word0", "word1", "word4"]
    assert store.get(jid)["cuts"]["0"]["file"] == "cut0.mp4"            # the edited file wasn't recorded as the plain cut

    # a failed edit is recorded, not raised
    store.update(jid, task_args={"clip": 0, "deleted": list(range(40)), "broll": False})
    runner._edit(store.get(jid))
    assert store.get(jid)["cuts"]["e0"]["state"] == "failed" and "Everything" in store.get(jid)["cuts"]["e0"]["error"]


def test_short_options_accept_broll_and_translate_only_when_valid():
    from recaply.render.shorts import ShortOptions
    o = ShortOptions.from_dict({"broll": True, "translate": "es"})
    assert o.broll is True and o.translate == "es"
    o = ShortOptions.from_dict({"translate": "klingon", "broll": "yes"})
    assert o.translate == "" and o.broll is True
