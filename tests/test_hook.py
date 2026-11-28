"""Hook check: rating the first 3 s, dropping weak openers, and the stakes line for clips that start mid-action."""

import cv2
import numpy as np

from recaply.render import subtitles
from recaply.render.shorts import ShortOptions
from recaply.score import hook


def clip(id_, score, start=10.0, **kw):
    return {"id": id_, "score": score, "start": start, "end": start + 30, "duration": 30.0, "reason": "a payoff",
            "signals": {}, **kw}


class FakeLLM:
    """Answers from a table keyed by the clip's opening words; records the prompts."""
    provider = "fake"
    supports_schema = True

    def __init__(self, answers):
        self.answers = answers
        self.prompts = []

    def complete_json(self, system, prompt, max_tokens=8000, schema=None):
        from recaply.providers.llm import Usage
        self.prompts.append(prompt)
        for key, ans in self.answers.items():
            if key in prompt:
                return ans, Usage("fake", "m", 10, 5)
        return {"score": 5, "reason": "meh", "mid_action": False, "stakes": ""}, Usage("fake", "m", 10, 5)


def words_at(start, text):
    return [{"w": w, "s": start + i * 0.4, "e": start + i * 0.4 + 0.3} for i, w in enumerate(text.split())]


def test_opening_is_only_the_first_three_seconds():
    w = words_at(10, "one two three four five six seven eight nine ten")
    assert hook.opening_words(w, 10) == "one two three four five six seven eight"
    assert hook.opening_words(w, 50) == ""


def test_parse_clamps_and_cleans():
    assert hook.parse({"score": "11"}) is None
    got = hook.parse({"score": 7.4, "reason": " ok ", "mid_action": "true", "stakes": "  Cops right behind him. "})
    assert got == {"score": 7, "reason": "ok", "mid_action": True, "stakes": "Cops right behind him"}
    assert hook.parse({"score": 6, "mid_action": True, "stakes": "none"})["stakes"] == ""
    # the video title or the clip's payoff are never used as the stakes
    assert hook.parse({"score": 6, "mid_action": True, "stakes": "My first day in Vice City"},
                      title="MY FIRST DAY IN VICE CITY | GTA 6")["stakes"] == ""
    assert hook.parse({"score": 6, "mid_action": True, "stakes": "Police chase ends in a crash"},
                      payoff="police chase ends in a crash")["stakes"] == ""
    long = hook.parse({"score": 6, "mid_action": True, "stakes": "one two three four five six seven eight"})
    assert len(long["stakes"].split()) <= 6


def test_weak_openers_are_dropped_and_the_hook_counts_in_the_rank():
    clips = [clip(1, 80), clip(2, 70, start=100), clip(3, 60, start=200)]
    words = words_at(10, "so um yeah") + words_at(100, "the cops are right behind us go go") + words_at(200, "watch this jump")
    llm = FakeLLM({"so um yeah": {"score": 2, "reason": "filler", "mid_action": False, "stakes": ""},
                   "cops are right": {"score": 9, "reason": "chase", "mid_action": True, "stakes": "Cops right behind"},
                   "watch this": {"score": 6, "reason": "promise", "mid_action": False, "stakes": "A huge jump"}})
    assert hook.check_hooks(clips, words, {}, llm, "Title", "Gaming") == 3
    kept, note = hook.apply(clips, 6)
    assert [c["id"] for c in kept] == [2, 3]
    assert "1 of 3" in note
    c2 = next(c for c in kept if c["id"] == 2)
    assert c2["score"] == round(0.75 * 70 + 0.25 * 90, 1) and c2["context"] == "Cops right behind"
    c3 = next(c for c in kept if c["id"] == 3)
    assert c3["context"] == ""                      # not mid-action: no stakes line on screen


def test_fast_motion_at_the_start_counts_as_mid_action():
    c = clip(1, 50, opening_signals={"motion": 0.9})
    llm = FakeLLM({"": {"score": 7, "reason": "r", "mid_action": False, "stakes": "Last two alive"}})
    hook.check_hooks([c], [], {}, llm, "T", "Gaming")
    assert c["mid_action"] and c["context"] == "Last two alive"
    assert "motion 0.90" in llm.prompts[0]           # the opening's own signals are what the model sees


def test_nothing_passes_keeps_the_best_openers():
    clips = [clip(i, 50 + i, start=i * 50.0) for i in range(5)]
    for c, s in zip(clips, (1, 4, 3, 5, 2)):
        c["hook_score"] = s
    kept, note = hook.apply(clips, 6)
    assert sorted(c["hook_score"] for c in kept) == [3, 4, 5] and "strongest openers" in note


def test_unrated_clips_are_left_alone():
    clips = [clip(1, 50)]
    kept, note = hook.apply(clips, 6)
    assert kept == clips and note == ""


def test_plain_frame_description_without_a_vision_model():
    img = np.full((252, 448, 3), 200, np.uint8)
    cv2.putText(img, "GAME", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 8)
    ok, jpg = cv2.imencode(".jpg", img)
    text = hook.describe_plain(jpg.tobytes())
    assert "no faces" in text and "bright scene" in text


def test_stakes_layer_comes_first_and_the_hook_follows():
    L = subtitles.context_layer("cops right behind him", "9:16", 1080, 1920)
    assert L["start"] == 0 and L["end"] == subtitles.CONTEXT_SEC and L["text"] == "COPS RIGHT BEHIND HIM"
    hk = subtitles.hook_layer("Watch this", "9:16", 1080, 1920, start=subtitles.CONTEXT_SEC)
    assert hk["start"] == 1.0 and hk["end"] == 4.0
    assert subtitles.context_layer("", "9:16", 1080, 1920) is None


def test_stakes_options():
    o = ShortOptions.from_dict({"context": False, "context_text": "  a   very long line that goes on and on past forty chars "})
    assert o.context is False and len(o.context_text) <= 40 and "  " not in o.context_text
    assert ShortOptions.from_dict({}).context is True
