import numpy as np
import pytest

from recaply.cache import StageCache, params_hash, safe_name
from recaply.log import redact
from recaply.profiles import all_profiles, detect_profile, get_profile
from recaply.providers.http import ProviderError
from recaply.providers.llm import LLM, Usage, parse_json
from recaply.score.rerank import rerank
from recaply.score.windows import Window
from recaply.telemetry import Telemetry


@pytest.mark.parametrize("meta,expected", [
    ({"title": "GTA 6 RP chaos with the boys", "categories": ["Gaming"]}, "open_world"),
    ({"title": "Radiant VALORANT ranked grind"}, "valorant"),
    ({"title": "CS2 FACEIT level 10"}, "fps"),
    ({"title": "The Diary Of A CEO | Ep. 212 with ..."}, "talk"),
    ({"title": "Minecraft hardcore episode 4", "categories": ["Gaming"]}, "gaming"),
    ({"title": "IRL walking around Tokyo"}, "irl"),
    ({"title": "my video"}, "irl"),
    ({"title": "late night stream", "extractor": "twitch:vod"}, "gaming"),
])
def test_detect_profile(meta, expected):
    assert detect_profile(meta).profile == expected


def test_profiles_valid_and_lengths():
    keys = {p.key for p in all_profiles()}
    assert {"talk", "gaming", "fps", "open_world", "irl", "valorant", "valorant_vct"} <= keys
    p = get_profile("gaming").with_lengths(60, 90)
    assert (p.clip_min, p.clip_max) == (60, 90) and 60 <= p.clip_target <= 90
    p = get_profile("gaming").with_lengths(200, 1)
    assert p.clip_min <= p.clip_max <= 180


def test_cache_roundtrip(tmp_path):
    c = StageCache("youtube-abc", root=str(tmp_path))
    assert c.get_json("x", {"a": 1}) is None
    assert c.json("x", {"a": 1}, lambda: {"v": 1}) == {"v": 1}
    assert c.json("x", {"a": 1}, lambda: {"v": 2}) == {"v": 1}          # cached
    assert c.json("x", {"a": 2}, lambda: {"v": 3}) == {"v": 3}          # other params
    arr = c.array("rms", None, lambda: np.arange(5, dtype=np.float32))
    assert c.get_array("rms").tolist() == arr.tolist()
    assert params_hash({"b": 1, "a": 2}) == params_hash({"a": 2, "b": 1})
    assert "/" not in safe_name("../../etc/passwd") and ".." not in safe_name("../x")


def test_redact_hides_keys():
    s = redact("calling with key=AIzaSyA1234567890abcdef and Bearer gsk_abcdefghijkl")
    assert "AIzaSy" not in s.replace("AIza***", "") and "abcdefghijkl" not in s


def test_parse_json_tolerant():
    assert parse_json('```json\n{"clips": []}\n```') == {"clips": []}
    assert parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ProviderError):
        parse_json("no json here")


class FakeLLM(LLM):
    provider = "fake"

    def __init__(self, fail=False):
        super().__init__("fake-1")
        self.fail = fail

    def complete_json(self, system, prompt, max_tokens=8000):
        if self.fail:
            raise ProviderError("rate limited", 429, True)
        assert 'id="0"' in prompt and "[105.0] what a play." in prompt
        return {"clips": [{"id": 0, "score": 90, "start": 104, "end": 130, "reason": "great",
                           "title": "T", "hook": "H"},
                          {"id": 1, "score": "bad"}]}, Usage("fake", "fake-1", 1000, 200)


def _windows():
    w0 = Window(100, 160, 120, 0.5, words=[{"w": "what", "s": 105.0, "e": 105.3},
                                           {"w": "a", "s": 105.4, "e": 105.5},
                                           {"w": "play.", "s": 105.6, "e": 106.0}])
    w1 = Window(500, 560, 520, 0.8)
    return [w0, w1]


def test_rerank_with_llm_blends_and_falls_back_per_item():
    prof = get_profile("gaming")
    tel = Telemetry()
    out, used = rerank(_windows(), prof, "title", FakeLLM(), tel)
    assert used == "llm:fake"
    assert out[0]["llm_score"] == 90 and 50 < out[0]["score"] < 90      # blended with the signal score
    assert (out[0]["start"], out[0]["end"], out[0]["hook"]) == (104, 130, "H")
    assert out[1]["llm_score"] is None                                    # malformed item -> heuristic
    assert tel.summary()["tokens"]["fake"]["in"] == 1000


def test_rerank_survives_llm_failure():
    out, used = rerank(_windows(), get_profile("gaming"), "t", FakeLLM(fail=True))
    assert used == "heuristic" and all(o["llm_score"] is None for o in out)
    assert out[1]["score"] > out[0]["score"]                              # louder window ranks higher
