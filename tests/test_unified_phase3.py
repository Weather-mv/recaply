"""Unified plan, Phase 3: caption presets and motion, the .ass export, the cinematic teaser and ducked background music."""

import json
import os
import subprocess

import numpy as np
import pytest
from flask import Flask
from PIL import Image

import editor
from conftest import FIXTURES
from recaply.render import ass, bgm, subtitles, teaser
from recaply.render.shorts import ShortOptions, render_short

SPEECH = os.path.join(FIXTURES, "speech.mp4")


def words(n=8, start=0.5, step=0.5):
    return [{"w": f"word{i}", "s": round(start + i * step, 2), "e": round(start + i * step + 0.4, 2)} for i in range(n)]


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True, check=True).stdout)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True, capture_output=True)


# ---------- presets and caption motion

def test_the_three_named_presets():
    P = subtitles.PRESETS
    assert P["bold_yellow"]["label"] == "Bold & Yellow" and P["bold_yellow"]["hi"] == "#ffe500" and P["bold_yellow"]["motion"] == "bounce"
    assert P["minimal_white"]["label"] == "Minimal White" and P["minimal_white"]["motion"] == "none" and not P["minimal_white"]["upper"]
    assert P["neon"]["label"] == "Neon" and P["neon"]["motion"] == "stagger"
    L = subtitles.captions_layer(words(), "bold_yellow", "9:16", "center", 1080, 1920)
    assert L["motion"] == "bounce" and L["preset"] == "bold_yellow"
    assert subtitles.captions_layer(words(), "bold", "9:16", "center", 1080, 1920)["motion"] == "none"   # old presets unchanged
    assert editor.clean_doc is not None


def _layer(motion):
    L = subtitles.captions_layer(words(), "bold_yellow", "9:16", "center", 1080, 1920)
    L["motion"] = motion
    return L


def test_caption_state_carries_the_word_phase():
    L = _layer("bounce")
    start = L["chunks"][0]["words"][0]["s"]
    assert editor.caption_state(L, start, 0, 1)[4] == 0.0
    assert 0 < editor.caption_state(L, start + 0.11, 0, 1)[4] < 1
    assert editor.caption_state(L, start + 0.3, 0, 1)[4] == 1.0
    assert editor.caption_state(_layer("none"), start + 0.05, 0, 1)[4] == 1.0          # no motion: always settled


def _draw(L, state):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    editor._draw_caption(img, L, state, lambda x, y: (x, y), 1)
    return np.asarray(img)


def test_bounce_and_stagger_change_what_is_drawn():
    L0, Lb, Ls = _layer("none"), _layer("bounce"), _layer("stagger")
    j = 1                                                                  # second word of the first chunk is the active one
    flat = _draw(L0, (0, j, 1.0, 1.0, 1.0))
    peak = _draw(Lb, (0, j, 1.0, 1.0, 0.5))                                # mid-bounce: the word is bigger and lifted
    settled = _draw(Lb, (0, j, 1.0, 1.0, 1.0))
    assert not np.array_equal(flat, peak) and np.array_equal(flat, settled)   # settled bounce == the plain caption
    # stagger: words not spoken yet are not drawn, so fewer pixels than the whole chunk
    first_only = _draw(Ls, (0, 0, 1.0, 1.0, 1.0))
    all_words = _draw(Ls, (0, len(L0["chunks"][0]["words"]) - 1, 1.0, 1.0, 1.0))
    assert (first_only[..., 3] > 0).sum() < (all_words[..., 3] > 0).sum()


def test_clean_doc_keeps_a_valid_motion_only(tmp_path):
    meta = {"w": 1280, "h": 720, "fps": 30, "duration": 5, "audio": "aac", "video": "h264"}
    doc = {"format": "9:16", "size": "1080", "trim": [0, 5], "layers": [_layer("bounce")]}
    assert editor.clean_doc(doc, meta, str(tmp_path))["layers"][0]["motion"] == "bounce"
    doc["layers"] = [{**_layer("bounce"), "motion": "evil"}]
    assert editor.clean_doc(doc, meta, str(tmp_path))["layers"][0]["motion"] == "none"


# ---------- .ass export

def test_ass_has_karaoke_lines_with_per_word_tags():
    L = _layer("bounce")
    text = ass.build(L, 1080, 1920)
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text and "Style: Default," in text
    first = next(line for line in text.splitlines() if line.startswith("Dialogue:"))
    assert first.count("\\1c") >= 3 * len(L["chunks"][0]["words"])         # base colour + highlight on + off, per word
    assert "&H0000E5FF" in first.upper() or "&H0000E5FF&" in first.upper()   # #ffe500 as BGR
    assert "\\fscx122" in first and "WORD0" in first                          # bounce + upper case
    assert "0:00:00.50" in first                                              # starts when the first word does
    shifted = ass.build(L, 1080, 1920, offset=3.0)
    assert "0:00:03.50" in next(line for line in shifted.splitlines() if line.startswith("Dialogue:"))
    stag = ass.build(_layer("stagger"), 1080, 1920)
    assert "\\alpha&HFF&" in stag and "\\fscx55" in stag
    plain = ass.build(_layer("none"), 1080, 1920)
    assert "\\fscx122" not in plain and "{\\an5\\pos(540," in plain


def test_ass_escapes_override_braces(tmp_path):
    L = _layer("none")
    L["chunks"][0]["words"][0]["w"] = "{\\evil}"
    path = ass.write(L, str(tmp_path / "c.ass"), 1080, 1920)
    raw = open(path, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf") and b"{\\evil}" not in raw


# ---------- teaser

@pytest.fixture(scope="module")
def clip169(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    out = str(d / "test.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=270x480:rate=25:duration=6", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out)
    return out


def test_pick_time_stays_inside_the_clip_and_handles_junk(clip169, tmp_path):
    t = teaser.pick_time(clip169, 6.0)
    assert 0.4 <= t <= 5.6
    assert teaser.pick_time(str(tmp_path / "missing.mp4"), 10.0) == 5.0


def test_teaser_build_adds_three_seconds_and_keeps_the_size(clip169, tmp_path):
    t = teaser.pick_time(clip169, 6.0)
    still = str(tmp_path / "s.png")
    ffmpeg("-ss", f"{t:.2f}", "-i", clip169, "-frames:v", "1", still)
    ov = teaser.overlay_png(str(tmp_path / "o.png"), 270, 480, "You will not believe what happens next")
    pic = np.asarray(Image.open(ov))
    assert pic[0, 0, 3] == 255 and pic[-1, 0, 3] == 255 and pic[240, 0, 3] == 0      # bars top and bottom, clear in the middle
    out = str(tmp_path / "t.mp4")
    teaser.build(still, ov, clip169, t, clip169, out, 270, 480, 25)
    info = editor.probe(out)
    assert (info["w"], info["h"]) == (270, 480) and info["audio"]
    assert abs(duration(out) - 9.0) < 0.2
    frames = []
    for sec in (1.0, 2.8):
        f = str(tmp_path / f"f{sec}.png")
        ffmpeg("-ss", str(sec), "-i", out, "-frames:v", "1", f)
        frames.append(np.asarray(Image.open(f).convert("RGB")).astype(int))
    assert frames[0][:30].mean() < 8                                                 # the top bar is black in the teaser
    assert np.abs(frames[1] - frames[0]).mean() > 5                                  # the glitch changes the picture


# ---------- background music

def test_bgm_listing_and_picking(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAPLY_BGM_DIR", str(tmp_path))
    assert bgm.tracks() == [] and bgm.pick("auto") is None
    for n in ("b.mp3", "a.wav", "notes.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert [t["name"] for t in bgm.tracks()] == ["a.wav", "b.mp3"]
    assert bgm.pick("auto", 0).endswith("a.wav") and bgm.pick("auto", 1).endswith("b.mp3") and bgm.pick("auto", 2).endswith("a.wav")
    assert bgm.pick("b.mp3").endswith("b.mp3")
    assert bgm.pick("../a.wav") is None and bgm.pick("missing.mp3") is None and bgm.pick("") is None and bgm.pick("none") is None


def _tone_energy(path, hz, t0, t1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ss", str(t0), "-t", str(t1 - t0), "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x)
    freqs = np.fft.rfftfreq(len(x), 1 / 16000)
    return float(spec[(freqs > hz - 15) & (freqs < hz + 15)].sum())


def test_music_ducks_under_speech_and_returns_in_the_pauses(tmp_path):
    video = str(tmp_path / "v.mp4")
    # "speech": a loud 1 kHz tone for 3 s, then 3 s of silence
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=6",
           "-f", "lavfi", "-i", "sine=frequency=1000:duration=3,volume=0.8,apad=whole_dur=6",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
    track = str(tmp_path / "m.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=2", track)       # shorter than the video: it must loop
    out = str(tmp_path / "mix.mp4")
    bgm.mix(video, out, track, level=0.5)
    assert abs(duration(out) - 6.0) < 0.2
    assert editor.probe(out)["video"] == "h264"                                  # the picture is copied, not re-encoded
    talking = _tone_energy(out, 220, 0.8, 2.6)
    pause = _tone_energy(out, 220, 3.8, 5.2)
    assert pause > 3 * talking > 0, (talking, pause)                           # the music is clearly lower while the voice is on
    assert _tone_energy(out, 1000, 0.8, 2.6) > 0.05                              # and the voice is still there


def test_music_alone_when_the_video_has_no_sound(tmp_path):
    video = str(tmp_path / "silent.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
    track = str(tmp_path / "m.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=3", track)
    out = str(tmp_path / "o.mp4")
    bgm.mix(video, out, track, has_audio=False)
    assert editor.probe(out)["audio"] and _tone_energy(out, 220, 0.5, 2.0) > 0.01


# ---------- options and the whole short

def test_short_options_teaser_and_music(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAPLY_BGM_DIR", str(tmp_path))
    (tmp_path / "song.mp3").write_bytes(b"x")
    o = ShortOptions.from_dict({"teaser": 1, "bgm": "song.mp3", "bgm_level": "0.5"})
    assert o.teaser is True and o.bgm == "song.mp3" and o.bgm_level == 0.5
    assert ShortOptions.from_dict({"bgm": "auto", "bgm_level": 9}).bgm_level == 0.8
    assert ShortOptions.from_dict({"bgm": "../etc/passwd"}).bgm == ""
    assert ShortOptions.from_dict({"bgm": "none"}).bgm == "" and ShortOptions().teaser is False


def test_a_whole_short_with_teaser_music_and_ass(tmp_path, monkeypatch):
    music = tmp_path / "bgm"
    music.mkdir()
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=4", str(music / "calm.wav"))
    monkeypatch.setenv("RECAPLY_BGM_DIR", str(music))
    clip = {"id": 4, "title": "t", "hook": "You won't believe this", "emphasis": []}
    w = [{"w": t, "s": 0.5 + i * 0.5, "e": 0.9 + i * 0.5} for i, t in enumerate("so this is the craziest thing I have seen".split())]
    base = render_short(clip, SPEECH, w, str(tmp_path / "plain"),
                        ShortOptions(export="draft", captions="bold_yellow", hook=False, use_brand=False))
    out_dir = str(tmp_path / "out")
    info = render_short(clip, SPEECH, w, out_dir, ShortOptions(export="draft", captions="bold_yellow", hook=True, use_brand=False,
                                                              teaser=True, bgm="auto"))
    assert info["teaser"] is True and info["music"] == "calm.wav" and not [n for n in info["notes"] if "skipped" in n]
    plain_len = duration(os.path.join(tmp_path, "plain", base["file"]))
    final = os.path.join(out_dir, info["file"])
    assert duration(final) == pytest.approx(info["duration"], abs=0.2)
    assert duration(final) == pytest.approx(plain_len + teaser.TEASER_SEC, abs=0.4)
    sub = open(os.path.join(out_dir, info["ass"]), encoding="utf-8-sig").read()
    first = next(line for line in sub.splitlines() if line.startswith("Dialogue:"))
    assert "0:00:03.50" in first                                                # captions are shifted by the teaser's 3 s
    assert base["teaser"] is False and base["music"] is None and base["ass"].endswith(".ass")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("RECAPLY_BGM_DIR", str(empty))                           # no music files: the short is made without, with a note
    nomusic = render_short(clip, SPEECH, w, str(tmp_path / "nm"), ShortOptions(export="draft", use_brand=False, bgm="auto"))
    assert nomusic["music"] is None and any("assets/bgm" in n for n in nomusic["notes"])


# ---------- API: music list, captions list, trim, serving the .ass

class _Auth:
    me = 7

    uid = classmethod(lambda cls: cls.me)
    ent = staticmethod(lambda: {"can_create": True, "priority": 1, "name": "x", "left": 9, "limit": 30, "reason": "", "watermark": False})
    csrf_token = staticmethod(lambda: "t")
    can_access = classmethod(lambda cls, owner: owner == cls.me)
    refunder = staticmethod(lambda: (lambda keys: None))
    charge = staticmethod(lambda keys: (True, 1))


@pytest.fixture
def client(monkeypatch):
    import clip_routes
    import studio_app
    from recaply.jobs import store
    store.init()
    for mod in (clip_routes, studio_app):
        monkeypatch.setattr(mod, "auth", _Auth)
    monkeypatch.setattr(studio_app, "DESKTOP", True)
    submitted = []
    monkeypatch.setattr(clip_routes, "get_queue", lambda: type("Q", (), {"submit": lambda s, *a, **k: submitted.append(a)})())
    app = Flask(__name__)
    app.register_blueprint(clip_routes.bp)
    app.register_blueprint(studio_app.bp)
    c = app.test_client()
    c.submitted = submitted
    return c


def _job():
    from recaply.jobs import store
    jid = store.create("https://youtu.be/x", {}, owner=_Auth.me)
    result = {"source": {"kind": "url", "key": "k", "title": "T", "duration": 600.0}, "profile": {"key": "gaming", "label": "G"},
              "clips": [{"id": 1, "start": 100.0, "end": 140.0, "duration": 40.0, "title": "A", "hook": "H"}]}
    store.update(jid, state="ready", result=result)
    return jid


def test_capabilities_list_the_new_captions_and_music(client, tmp_path, monkeypatch):
    monkeypatch.setenv("RECAPLY_BGM_DIR", str(tmp_path))
    (tmp_path / "calm.mp3").write_bytes(b"x")
    caps = client.get("/api/studio/capabilities").get_json()
    keys = {c["key"] for c in caps["captions"]}
    assert {"bold_yellow", "minimal_white", "neon"} <= keys
    assert caps["music"] == [{"name": "calm.mp3", "size_mb": 0.0}]


def test_shorts_route_validates_and_stores_trim(client):
    from recaply.jobs import store
    jid = _job()
    ok = client.post(f"/api/clipjob/{jid}/shorts", json={"ids": [1], "options": {"teaser": True}, "trim": {"start": 95, "end": 150}})
    assert ok.status_code == 200
    args = store.get(jid)["task_args"]
    assert args["trim"] == {"id": 1, "start": 95.0, "end": 150.0} and args["options"]["teaser"] is True
    for bad in ({"start": 150, "end": 95}, {"start": 10, "end": 11}, {"start": 0, "end": 9999}, {"start": "x", "end": 5}, {"start": 1}):
        assert client.post(f"/api/clipjob/{jid}/shorts", json={"ids": [1], "trim": bad}).status_code == 400
    again = client.post(f"/api/clipjob/{jid}/shorts", json={"ids": [1]})
    assert again.status_code == 200 and store.get(jid)["task_args"]["trim"] is None


def test_the_ass_file_is_served_only_when_a_short_made_it(client, tmp_path, monkeypatch):
    from recaply.jobs import runner, store
    jid = _job()
    d = runner.job_dir(jid)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "short_01_9x16.ass"), "w").write("[Script Info]\n")
    assert client.get(f"/clipjob/{jid}/file/short_01_9x16.ass").status_code == 404          # not in the job's records yet
    store.set_cut(jid, "s1", {"state": "done", "file": "short_01_9x16.mp4", "ass": "short_01_9x16.ass"})
    r = client.get(f"/clipjob/{jid}/file/short_01_9x16.ass")
    assert r.status_code == 200 and b"Script Info" in r.data
    assert client.get(f"/clipjob/{jid}/file/..%2Fx.ass").status_code == 404
