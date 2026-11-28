from recaply.edit import transcript as tx
from recaply.render.shorts import ShortOptions, apply_caption_edits, clean_caption_edits


def words():
    return [{"w": "yeh", "s": 0.5, "e": 0.8}, {"w": "kya", "s": 0.9, "e": 1.2}, {"w": "bhai", "s": 1.3, "e": 1.6}]


def test_edits_are_cleaned():
    raw = [{"t": "100.5", "w": "  what   is "}, {"t": -1, "w": "x"}, {"t": "nope"}, "junk", {"t": 3, "w": "a" * 99}]
    assert clean_caption_edits(raw) == [{"t": 100.5, "w": "what is"}, {"t": 3.0, "w": "a" * 40}]
    assert clean_caption_edits("x") == []
    assert ShortOptions.from_dict({"caption_edits": [{"t": 1, "w": "hi"}]}).caption_edits == [{"t": 1.0, "w": "hi"}]
    assert ShortOptions.from_dict({}).caption_edits == []


def test_edits_replace_and_hide_words_by_source_time():
    out = apply_caption_edits(words(), [{"t": 100.5, "w": "WHAT"}, {"t": 100.92, "w": ""}], clip_start=100.0)
    assert [w["w"] for w in out] == ["WHAT", "bhai"]
    assert out[0]["s"] == 0.5 and out[0]["e"] == 0.8          # timing never changes
    assert apply_caption_edits(words(), [], 100.0) == words()
    assert [w["w"] for w in apply_caption_edits(words(), [{"t": 5.0, "w": "x"}], 100.0)] == ["yeh", "kya", "bhai"]


def test_edits_find_words_after_a_transcript_cut():
    w = [{**x, "o": round(100.0 + x["s"], 3)} for x in words()]
    segs = [(0.0, 0.85), (1.25, 2.0)]                       # "kya" cut out
    kept = tx.retime(w, {1}, segs)
    assert [x["o"] for x in kept] == [100.5, 101.3]
    out = apply_caption_edits(kept, [{"t": 101.3, "w": "bro"}], clip_start=100.0)
    assert [x["w"] for x in out] == ["yeh", "bro"]


# ---------- the Captions panel's style ----------

import editor  # noqa: E402
from recaply.render import subtitles  # noqa: E402


def test_style_is_cleaned():
    st = subtitles.clean_style({"color": "#FF0000", "hi": "red", "size": 999, "per": "2", "stroke_w": -3, "bg_op": 0.5,
                                "y": 0.01, "upper": False, "hi_mode": "box", "enter": "fade", "motion": "zoom",
                                "font": "impact", "evil": 1, "size2": 3})
    assert st == {"color": "#ff0000", "size": 160, "per": 2, "stroke_w": 0, "bg_op": 0.5, "y": 0.08, "upper": False,
                  "hi_mode": "box", "enter": "fade", "motion": "zoom", "font": "impact"}
    assert subtitles.clean_style({"font": "../x", "motion": "spin", "upper": "yes"}) == {}
    assert subtitles.clean_style(None) == {}
    assert ShortOptions.from_dict({"caption_style": {"motion": "bounce"}}).caption_style == {"motion": "bounce"}


def test_style_overrides_the_preset_in_the_layer():
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in enumerate("one two three four five six".split())]
    L = subtitles.captions_layer(words, "bold", "9:16", "center", 1080, 1920,
                                 style={"color": "#123456", "per": 2, "size": 100, "y": 0.3, "enter": "fade",
                                        "motion": "zoom", "upper": False, "bg_op": 0.4})
    assert L["color"] == "#123456" and L["per"] == 2 and L["size"] == 100 and L["upper"] is False
    assert L["enter"] == "fade" and L["pop"] is False and L["motion"] == "zoom" and L["bg_op"] == 0.4
    assert all(len(c["words"]) <= 2 for c in L["chunks"])
    assert abs((L["y"] + L["h"] / 2) - 1920 * 0.3) < 1
    clean = editor.clean_layer(L, 1080, 1920, ".", set())
    assert clean["enter"] == "fade" and clean["pop"] is False and clean["motion"] == "zoom"


def test_fade_and_zoom_in_the_renderer():
    L = {"chunks": [{"words": [{"w": "a", "s": 1.0, "e": 1.5}, {"w": "b", "s": 1.5, "e": 2.0}]}], "pop": False,
         "enter": "fade", "motion": "zoom"}
    i, j, sc, op, wp = editor.caption_state(L, 1.1, 0, 1)
    assert sc == 1.0 and 0.4 < op < 0.6
    L["enter"] = "pop"
    _, _, sc, op, _ = editor.caption_state(L, 1.05, 0, 1)
    assert sc < 1 and op < 1
    assert "zoom" in editor.CAP_MOTIONS and "fade" in editor.CAP_ENTERS


def test_caption_box_moves_left_right_and_stays_on_screen():
    words = [{"w": "hi", "s": 0, "e": 0.3}]
    L = subtitles.captions_layer(words, "bold", "9:16", "center", 1080, 1920, style={})
    assert (L["x"], L["w"]) == (round(1080 * 0.08, 2), round(1080 * 0.84, 2))          # default unchanged
    L = subtitles.captions_layer(words, "bold", "9:16", "center", 1080, 1920, style={"x": 0.3, "w": 0.5})
    assert abs(L["x"] + L["w"] / 2 - 324) < 1 and abs(L["w"] - 540) < 1
    L = subtitles.captions_layer(words, "bold", "9:16", "center", 1080, 1920, style={"x": 0.2, "w": 0.9})
    assert L["x"] >= -0.01 and abs(L["x"] + L["w"] / 2 - 216) < 1                     # narrowed to fit
    assert subtitles.clean_style({"x": 2, "w": 0})["x"] == 0.85
