"""
Phase 2: shorts. Unit tests for tighten / captions / reframe / brand / copy
generation, plus renders of the fixture videos and a job run through the
store, with no network and no API keys.
"""

import json
import os
import subprocess

import numpy as np
import pytest

from conftest import FIXTURES
from recaply.providers.llm import LLM, Usage
from recaply.render import brand, presets, reframe, subtitles, tighten
from recaply.score.rerank import build_prompt, clean_tags, clean_words, heuristic, rerank
from recaply.score.windows import Window

SPEECH = os.path.join(FIXTURES, "speech.mp4")
BURSTS = os.path.join(FIXTURES, "bursts.mp4")


def W(text, s, e):
    return {"w": text, "s": s, "e": e}


def talk(*pairs, start=0.0, step=0.4, dur=0.3):
    out, t = [], start
    for p in pairs:
        out.append(W(p, round(t, 2), round(t + dur, 2)))
        t += step
    return out


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 path], capture_output=True, text=True, check=True).stdout)


# ---------- tighten ----------

def test_tighten_cuts_fillers_and_retimes_words():
    words = talk("so", "um", "this", "is", "uh", "great")            # 0 .. 2.3 s, 6 words
    segs, kept = tighten.keep_segments(words, 2.4)
    assert [w["w"] for w in kept] == ["so", "this", "is", "great"]
    assert tighten.removed_seconds(segs, 2.4) > 0.5
    assert all(a < b for a, b in segs)
    # re-timed words stay in order, inside the shortened clip
    total = sum(b - a for a, b in segs)
    assert all(0 <= w["s"] < w["e"] <= total + 1e-6 for w in kept)
    assert [w["s"] for w in kept] == sorted(w["s"] for w in kept)


def test_tighten_shortens_long_pauses_but_keeps_short_ones():
    words = talk("hello", "there", start=0.0) + talk("again", start=5.0)        # a 4 s pause in the middle
    segs, kept = tighten.keep_segments(words, 5.5, max_gap=0.6, keep_gap=0.2)
    assert tighten.removed_seconds(segs, 5.5) == pytest.approx(3.8, abs=0.35)
    assert len(kept) == 3
    # no pause longer than max_gap is left between words
    for a, b in zip(kept, kept[1:]):
        assert b["s"] - a["e"] <= 0.6 + 1e-6


def test_tighten_leaves_clean_speech_alone():
    words = talk("a", "b", "c", "d", step=0.35, dur=0.3)
    segs, kept = tighten.keep_segments(words, 1.4)
    assert segs == [(0.0, 1.4)] and len(kept) == 4


def test_tighten_without_words_keeps_everything():
    assert tighten.keep_segments([], 10.0) == ([(0.0, 10.0)], [])


def test_tighten_apply_writes_a_shorter_file(tmp_path):
    dst = str(tmp_path / "t.mp4")
    tighten.apply(SPEECH, dst, [(0.0, 5.0), (10.0, 14.0)])
    assert abs(duration(dst) - 9.0) < 0.5


# ---------- presets ----------

def test_export_presets():
    assert presets.export_preset("tiktok")["format"] == "9:16"
    assert presets.export_preset("nope")["key"] == presets.DEFAULT_EXPORT
    assert presets.ratio("9:16") == pytest.approx(0.5625)
    assert presets.length_note(presets.export_preset("shorts"), 200)
    assert presets.length_note(presets.export_preset("shorts"), 59) is None
    assert presets.length_note(presets.export_preset("landscape"), 9999) is None


# ---------- captions ----------

def test_keywords_numbers_and_emphasis_are_marked():
    marked = subtitles.mark_keywords(talk("that", "was", "insane", "I", "paid", "$500", "for", "it"),
                                     keywords=("insane",), emphasis=("paid",))
    k = {w["w"]: w.get("k") for w in marked}
    assert k["insane"] == 1 and k["paid"] == 1 and k["$500"] == 1 and k["that"] is None


def test_emoji_are_sparse_and_optional():
    words = talk(*"this is insane and this is funny haha and that is insane again".split(), step=0.3)
    off = subtitles.build_chunks(words, 3, emoji=False)
    on = subtitles.build_chunks(words, 3, emoji=True)
    assert not any("emoji" in c for c in off)
    assert any("emoji" in c for c in on)
    times = [c["words"][0]["s"] for c in on if "emoji" in c]
    assert all(b - a >= subtitles.EMOJI_GAP for a, b in zip(times, times[1:]))


def test_caption_layer_stays_in_the_safe_zone_and_all_presets_work():
    words = talk(*"one two three four five six seven eight".split())
    for key in subtitles.PRESETS:
        L = subtitles.captions_layer(words, key, "9:16", "center", 1080, 1920)
        assert L and L["chunks"] and L["preset"] == key
        assert L["y"] >= 1920 * 0.5                                # lower half...
        assert L["y"] + L["h"] <= 1920 * 0.77                       # ...above the bottom ~25% the platform UI covers
        assert L["x"] >= 1080 * 0.05 and L["x"] + L["w"] <= 1080 * 0.95
    split = subtitles.captions_layer(words, "bold", "9:16", "split", 1080, 1920)
    assert split["y"] < 1920 * 0.5                                 # on the seam between cam and gameplay
    assert subtitles.captions_layer([], "bold", "9:16", "center", 1080, 1920) is None


def test_brand_colours_override_the_preset():
    words = talk("a", "b")
    L = subtitles.captions_layer(words, "bold", "9:16", "center", 1080, 1920,
                                 colors={"text": "#112233", "highlight": "#445566", "keyword": "#778899"})
    assert (L["color"], L["hi"], L["kw"]) == ("#112233", "#445566", "#778899")
    box = subtitles.captions_layer(words, "punch", "9:16", "center", 1080, 1920, colors={"highlight": "#445566"})
    assert box["hi_bg"] == "#445566"


def test_hook_layer():
    h = subtitles.hook_layer("You will not believe what happens next in this clip", "9:16", 1080, 1920)
    assert h and h["end"] == 3.0 and h["y"] < 1920 * 0.25 and h["w"] <= 1080
    assert subtitles.hook_layer("   ", "9:16", 1080, 1920) is None


# ---------- reframe ----------

def test_camera_holds_still_inside_the_dead_zone():
    times = np.arange(20) / 2.0
    keys = reframe.camera_path([0.5 + 0.02 * ((-1) ** i) for i in range(20)], times)
    assert len(keys) == 1


def test_camera_follows_when_the_speaker_moves():
    times = np.arange(30) / 2.0
    xs = [0.3] * 15 + [0.7] * 15
    keys = reframe.camera_path(xs, times)
    assert keys[0][1] == pytest.approx(0.3, abs=0.01) and keys[-1][1] == pytest.approx(0.7, abs=0.01)
    assert all(b[0] > a[0] for a, b in zip(keys, keys[1:]))        # strictly increasing times
    assert reframe.camera_path([None, None], np.arange(2) / 2.0) == [(0.0, 0.5)]


def test_main_face_does_not_flicker_between_two_people():
    big, small = (0.3, 0.5, 0.3, 0.4, 0.9), (0.75, 0.5, 0.2, 0.3, 0.9)
    frames = [[big, small]] * 6
    xs, seen = reframe.main_face_track(frames)
    assert seen == 1.0 and all(abs(x - 0.3) < 0.01 for x in xs)
    assert reframe.main_face_track([[], []]) == ([None, None], 0.0)


def test_face_layers_keep_the_video_covering_the_canvas():
    meta = {"w": 1920, "h": 1080, "duration": 10.0, "fps": 30}
    plan = reframe.Plan("face", "t", {"keys": [(0.0, 0.1), (3.0, 0.1), (3.6, 0.9)]})
    (L,) = reframe.video_layers(plan, meta, 1080, 1920)
    lw = 1920 * 1920 / 1080
    for _t, x, _y, _e in L["kf"]["pos"]:
        assert -(lw - 1080) - 1e-6 <= x <= 1e-6                    # never shows empty canvas
    for strategy in ("center", "action"):
        (V,) = reframe.video_layers(reframe.Plan(strategy, "t", {"cx": 0.95}), meta, 1080, 1920)
        x, _, cw, _ = V["crop"]
        assert 0 <= x and x + cw <= 1 + 1e-6


def test_wide_output_passes_through():
    meta = {"w": 1920, "h": 1080, "duration": 5.0}
    assert reframe.choose(BURSTS, meta, "face", 16 / 9).strategy == "fit"


# ---------- brand kit ----------

def test_brand_kit_is_cleaned_and_round_trips():
    kit = brand.BrandKit(accent="red", logo_size=9, logo_pos="middle", caption_style="nope", font="wingdings")
    c = kit.clean()
    assert c.accent == "#0865ff" and c.logo_size == 0.4 and c.logo_pos == "tr"
    assert c.caption_style == "bold" and c.font == "default"
    saved = brand.save("tester", brand.BrandKit(accent="#ff0000", caption_style="karaoke", logo_pos="bl"))
    again = brand.load("tester")
    assert again.accent == "#ff0000" and again.caption_style == "karaoke" and again.logo_pos == "bl"
    assert saved.logo is False and brand.load("someone-else").accent == "#0865ff"


def test_brand_logo_and_stage(tmp_path):
    from PIL import Image
    src = tmp_path / "logo.png"
    Image.new("RGBA", (400, 200), (255, 0, 0, 255)).save(src)
    brand.save_logo("logoer", str(src))
    kit = brand.load("logoer")
    assert kit.logo
    assets = str(tmp_path / "assets")
    staged = brand.stage_assets("logoer", kit, assets)
    L = brand.logo_layer(kit, staged["logo"], assets, 1080, 1920)
    assert L["w"] == pytest.approx(1080 * kit.logo_size) and L["h"] == pytest.approx(L["w"] / 2, abs=0.1)
    assert L["y"] + L["h"] < 1920 * 0.8                              # clear of the platform buttons
    brand.remove("logoer", "logo")
    assert not brand.load("logoer").logo


# ---------- copy: title, description, hashtags ----------

def test_clean_tags_and_words():
    assert clean_tags(["#Gaming", "gaming", "no way!", "123", "", "a-b"], ("shorts",)) == ["#Gaming", "#noway", "#ab", "#shorts"]
    assert clean_tags("#a, #b #c #d #e #f") == ["#a", "#b", "#c", "#d", "#e"]
    assert clean_tags(None) == []
    assert clean_words(["Wow", "wow", "two words", "x" * 3, "e", "f", "g"]) == ["Wow", "twowords", "xxx", "e"]


class FakeLLM(LLM):
    provider = "fake"

    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, system, prompt, max_tokens=8000):
        self.prompt = prompt
        return self.reply, Usage("fake", 10, 5)


def _window(profile):
    w = Window(start=100.0, end=150.0, peak=120.0, signal_score=0.8)
    w.words = talk("this", "is", "the", "craziest", "clutch", "ever", start=105.0)
    w.signals = {"audio_energy": 0.9}
    w.text_score = 0.5
    return w


def test_llm_copy_is_parsed_and_sanitised():
    from recaply.profiles import get_profile
    prof = get_profile("gaming")
    reply = {"clips": [{"id": 0, "score": 88, "start": 105, "end": 130, "reason": "great clutch",
                        "title": "The craziest clutch", "hook": "Wait for it", "description": "He 1v4s.  Unreal.",
                        "hashtags": "valorant, #clutch !!", "emphasis": ["craziest", "clutch"]}]}
    llm = FakeLLM(reply)
    out, used = rerank([_window(prof)], prof, "Some stream", llm)
    s = out[0]
    assert used == "llm:fake" and s["llm_score"] == 88
    assert s["description"] == "He 1v4s. Unreal."
    assert s["hashtags"] == ["#valorant", "#clutch", "#shorts"] and s["emphasis"] == ["craziest", "clutch"]
    assert '"description"' in llm.prompt and '"hashtags"' in llm.prompt and '"emphasis"' in llm.prompt


def test_copy_without_an_llm_still_has_tags():
    from recaply.profiles import get_profile
    prof = get_profile("talk")
    h = heuristic(_window(prof), prof)
    assert "#podcast" in h["hashtags"] and "#shorts" in h["hashtags"] and h["emphasis"] == []
    assert build_prompt([(0, _window(prof))], prof, "T").count("<candidate") == 1


# ---------- rendering ----------

def _words():
    return talk(*"so um this is the craziest thing that I have ever seen uh and nobody believed me".split(),
                start=0.5, step=0.5)


def test_render_a_vertical_short(tmp_path):
    from recaply.render.shorts import ShortOptions, render_short
    clip = {"id": 3, "title": "t", "hook": "You won't believe this", "emphasis": ["craziest"]}
    info = render_short(clip, SPEECH, _words(), str(tmp_path), ShortOptions(emoji=True, use_brand=False),
                        keywords=("crazy",), owner="nobody")
    out = os.path.join(tmp_path, info["file"])
    assert info["format"] == "9:16" and os.path.getsize(out) > 10_000
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    assert size == "1080,1920"
    assert abs(duration(out) - info["duration"]) < 0.2
    doc = json.load(open(os.path.join(tmp_path, info["edit_dir"], "doc.json"), encoding="utf-8"))
    types = [L["type"] for L in doc["doc"]["layers"]]
    assert "captions" in types and "text" in types                    # captions and the hook are layers
    assert os.path.exists(os.path.join(tmp_path, info["edit_dir"], "source.mp4"))   # the editor can reopen it


def test_tighten_makes_the_short_shorter(tmp_path):
    from recaply.render.shorts import ShortOptions, render_short
    words = _words()
    plain = render_short({"id": 1}, SPEECH, words, str(tmp_path / "a"), ShortOptions(hook=False, use_brand=False))
    tight = render_short({"id": 1}, SPEECH, words, str(tmp_path / "b"),
                         ShortOptions(hook=False, tighten=True, use_brand=False))
    assert tight["removed_sec"] > 0 and tight["duration"] < plain["duration"] - 0.3


def test_other_export_shapes(tmp_path):
    from recaply.render.shorts import ShortOptions, render_short
    info = render_short({"id": 2}, SPEECH, _words(), str(tmp_path), ShortOptions(export="square", captions="none",
                                                                                 hook=False, use_brand=False))
    out = os.path.join(tmp_path, info["file"])
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    assert size == "1080,1080"


def test_short_options_ignore_unknown_values():
    from recaply.render.shorts import ShortOptions
    o = ShortOptions.from_dict({"export": "myspace", "layout": "spin", "captions": "comic", "hook": 0, "tighten": 1})
    assert (o.export, o.layout, o.captions, o.hook, o.tighten) == ("shorts", "auto", "bold", False, True)


# ---------- a whole job: analyze -> shorts task -> store ----------

def test_shorts_task_runs_through_the_job_store():
    from recaply.engine import Options, analyze
    from recaply.jobs import runner, store
    store.init()
    res = analyze(BURSTS, Options(profile="gaming", count=2, min_len=12, max_len=20, transcriber="none", llm="none"))
    jid = store.create(BURSTS, {}, owner=None, title="bursts")
    store.update(jid, state="ready", result=res)
    ids = [c["id"] for c in res["clips"]]
    store.update(jid, task_args={"ids": ids, "options": {"export": "draft", "captions": "none", "use_brand": False}})
    runner.run_task(jid, "shorts")
    job = store.get(jid)
    assert job["state"] == "ready" and job["progress"] == 1.0, job
    for i in ids:
        s = job["cuts"][runner.short_key(i)]
        assert s["state"] == "done" and s["file"].startswith("short_") and s["format"] == "9:16", s
        assert os.path.exists(os.path.join(runner.job_dir(jid), s["file"]))
        assert job["cuts"][str(i)]["state"] == "done"                  # the plain cut is kept, too
    assert "render" in job["summary"]["shorts"]
