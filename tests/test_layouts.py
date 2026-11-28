"""Facecam layouts (cam on top / below / in a box, drawn cam box, cam size) and the fixes that came with them."""

import pytest

from recaply.edit import transcript as tx
from recaply.render import reframe, tighten
from recaply.render.shorts import ShortOptions
from recaply.render.subtitles import safe_band

META = {"w": 1280, "h": 720, "duration": 30.0, "fps": 30, "audio": "aac", "video": "h264"}
W, H = 1080, 1920
# a corner facecam, as the detector reports it on a real GTA stream
DET = {"found": True, "cam": [0.8281, 0.0933, 0.1719, 0.2313], "face": [0.8672, 0.1639, 0.0447, 0.1023],
       "game": [0.0052, 0.056, 0.9896, 0.8899]}


def layer(layers, id_):
    return next(L for L in layers if L["id"] == id_)


@pytest.mark.parametrize("kind,cam_top", [("split", True), ("split_bottom", False)])
def test_split_layouts_stack_cam_and_gameplay(kind, cam_top):
    plan = reframe.Plan(kind, "", {"det": DET, "ratio": 0.35})
    ls = reframe.video_layers(plan, META, W, H)
    cam, game = layer(ls, "cam"), layer(ls, "game")
    assert cam["h"] == round(H * 0.35) and cam["h"] + game["h"] == H
    assert (cam["y"] == 0) is cam_top and (game["y"] == 0) is not cam_top
    cx, cy, cw, ch = cam["crop"]                     # inside the detected box (a little in from its frame)
    assert cx >= 0.8281 and cy >= 0.0933 and cx + cw <= 0.8281 + 0.1719 + 1e-6
    for L in (cam, game):                            # no stretching
        assert L["crop"][2] * META["w"] / (L["crop"][3] * META["h"]) == pytest.approx(L["w"] / L["h"], rel=0.01)


def test_pip_puts_the_cam_box_over_full_gameplay():
    plan = reframe.Plan("pip", "", {"det": DET, "ratio": 0.42, "cx": 0.5})
    ls = reframe.video_layers(plan, META, W, H)
    game, cam = layer(ls, "game"), layer(ls, "cam")
    assert (game["x"], game["y"], game["w"], game["h"]) == (0, 0, W, H)
    assert cam["y"] < H * 0.2 and cam["w"] < W and abs(cam["x"] + cam["w"] / 2 - W / 2) < 1


def test_a_drawn_cam_box_is_used_exactly_and_bad_ones_are_dropped():
    o = ShortOptions.from_dict({"layout": "split", "cam_box": [0.1, 0.5, 0.3, 0.4], "split_ratio": 0.9})
    assert o.cam_box == [0.1, 0.5, 0.3, 0.4] and o.split_ratio == 0.6        # ratio clamped
    assert ShortOptions.from_dict({"cam_box": ["x", 1]}).cam_box is None
    det = reframe.manual_cam([0.1, 0.5, 0.3, 0.4])
    assert reframe.cam_rect(det) == [0.1, 0.5, 0.3, 0.4]                      # drawn: no inset
    assert reframe.cam_rect(DET) != DET["cam"]                                 # detected: trimmed in


def test_new_layouts_are_offered_and_auto_comes_first():
    assert reframe.STRATEGIES[0] == "auto"
    assert {"split", "split_bottom", "pip"} <= set(reframe.STRATEGIES)
    assert ShortOptions.from_dict({"layout": "pip"}).layout == "pip"


def test_captions_follow_the_seam():
    assert safe_band("9:16", "split", W, H, 0.3)[0] == pytest.approx(H * 0.3)
    assert safe_band("9:16", "split_bottom", W, H, 0.3)[0] < H * 0.7


def words(start, n=6, step=0.5):
    return [{"w": f"w{i}", "s": start + i * step, "e": start + i * step + 0.4} for i in range(n)]


def test_deleting_the_first_words_keeps_gameplay_before_anyone_talks():
    w = words(21.0)                                       # 21 s of gameplay, then speech
    segs = tx.keep_segments(w, {0, 1}, 30.0)
    removed = 30.0 - sum(b - a for a, b in segs)
    assert segs[0][0] == 0.0 and segs[0][1] > 20.5      # the gameplay stays
    assert removed < 1.5


def test_deleting_the_first_words_still_trims_a_short_lead_in():
    w = words(0.4)
    segs = tx.keep_segments(w, {0, 1}, 10.0)
    assert segs[0][0] >= w[1]["e"] - 0.001


def test_tighten_keeps_gameplay_stretches_on_gaming_clips():
    w = words(21.0)
    talk, _ = tighten.keep_segments(w, 30.0)
    game, _ = tighten.keep_segments(w, 30.0, content_gap=2.5)
    kept = lambda segs: sum(b - a for a, b in segs)      # noqa: E731
    assert kept(talk) < 10 and kept(game) > 28


def test_an_api_key_in_the_provider_order_box_is_refused_and_ignored():
    from recaply import user_settings as us
    assert not us.valid_order("cd0236898aad4467951f.nK8cR-fake")
    assert us.valid_order("ollama, groq")
    from recaply.providers import llm
    old = llm.settings.llm_order
    object.__setattr__(llm.settings, "llm_order", "not-a-provider-123")     # Settings is frozen
    try:
        assert llm._order("auto") == [*llm.CLOUD_ORDER, "ollama"]
    finally:
        object.__setattr__(llm.settings, "llm_order", old)


# ---------- a strong opening (score/boundaries.anchor_start) ----------

import numpy as np  # noqa: E402

from recaply.score.boundaries import anchor_start  # noqa: E402


def quiet_then_action(n=120, action_at=60):
    curve = np.zeros(n, np.float32)
    curve[action_at:action_at + 15] = 0.7
    return curve


def test_a_silent_lead_in_is_cut_and_the_end_keeps_the_clip_long_enough():
    curve = quiet_then_action()
    s, e, info = anchor_start([], 40.0, 75.0, peak=65.0, min_len=20, curve=curve, max_len=60, duration=120,
                              energy=curve)
    assert 58.0 <= s <= 60.0 and info["why"] == "the action starts"      # a second before the action
    assert e - s >= 19.5 and e > 75.0 and info["end_moved"] > 0
    assert s <= 65.0 - 2.0                                                  # the peak stays in


def test_a_strong_opening_is_left_alone():
    curve = quiet_then_action(action_at=40)
    s, e, info = anchor_start([], 40.0, 75.0, peak=50.0, min_len=20, curve=curve, max_len=60, duration=120)
    assert (s, e) == (40.0, 75.0) and info["moved"] == 0


def test_the_start_moves_to_a_line_not_into_a_word():
    words = [{"w": w, "s": 52.0 + i * 0.4, "e": 52.3 + i * 0.4} for i, w in enumerate("the cops are right behind us now".split())]
    words[-1]["w"] = "now."
    s, e, info = anchor_start(words, 40.0, 80.0, peak=70.0, min_len=20, curve=np.zeros(120, np.float32),
                              max_len=60, duration=120)
    assert info["why"] == "a line starts" and abs(s - 51.85) < 0.01
    assert not any(w["s"] < s < w["e"] for w in words)


def test_the_hook_beat_is_a_candidate_and_short_videos_are_safe():
    curve = np.zeros(60, np.float32)
    s, e, info = anchor_start([], 10.0, 40.0, peak=35.0, min_len=20, curve=curve, hook_starts=[18.0],
                              max_len=60, duration=60)
    assert s == 18.0 and info["why"] == "the hook beat"
    s, e, info = anchor_start([], 10.0, 30.0, peak=25.0, min_len=20, curve=curve, hook_starts=[18.0],
                              max_len=30, duration=30)
    assert (s, e) == (10.0, 30.0)                                           # it would end past the video
