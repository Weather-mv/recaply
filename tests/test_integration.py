"""
End to end on the short fixture videos in tests/fixtures (made by
tests/fixtures/make_fixtures.py): ingest -> signals -> windows -> score ->
select -> cut, with no network and no API keys.

The speech test runs faster-whisper locally; it's marked `slow` (the first
run downloads the model). Skip it with `pytest -m "not slow"`.
"""

import os
import subprocess

import pytest

from conftest import FIXTURES
from recaply.engine import Options, analyze, cut_clips
from recaply.ingest.sources import InputError

BURSTS = os.path.join(FIXTURES, "bursts.mp4")      # 120 s, loud at 40-44 s and 85-88 s
SPEECH = os.path.join(FIXTURES, "speech.mp4")      # ~40 s of synthesized speech


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def test_finds_the_loud_moments_and_cuts_them(tmp_path):
    res = analyze(BURSTS, Options(profile="gaming", count=5, min_len=12, max_len=20,
                                  transcriber="none", llm="none"))
    clips = res["clips"]
    assert len(clips) == 2
    covered = sorted((c["start"], c["end"]) for c in clips)
    assert covered[0][0] <= 40 and covered[0][1] >= 44
    assert covered[1][0] <= 85 and covered[1][1] >= 88
    for c in clips:
        assert 12 <= c["end"] - c["start"] <= 20 and 0 <= c["score"] <= 100 and c["reason"]
    assert res["scorer"] == "heuristic"
    assert "loudness" in res["summary"]["stages"]

    out = cut_clips(res, [c["id"] for c in clips], str(tmp_path))
    assert all("file" in v for v in out.values()), out
    for c in clips:
        d = duration(os.path.join(tmp_path, out[c["id"]]["file"]))
        assert abs(d - (c["end"] - c["start"])) < 0.6       # frame-accurate cut


def test_second_run_is_cached():
    opts = Options(profile="gaming", count=5, transcriber="none", llm="none")
    analyze(BURSTS, opts)
    res = analyze(BURSTS, Options(profile="fps", count=3, transcriber="none", llm="none"))
    assert res["summary"]["stages"]["loudness"]["cached"]


def test_bad_inputs_are_reported():
    with pytest.raises(InputError):
        analyze("C:/definitely/not/here.mp4", Options())
    with pytest.raises(InputError):
        analyze("https://example.com/video", Options())
    with pytest.raises(InputError):
        analyze(BURSTS, Options(profile="nope"))


@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(SPEECH), reason="speech fixture not generated")
def test_talk_profile_transcribes_and_snaps_to_sentences():
    res = analyze(SPEECH, Options(profile="talk", count=2, min_len=10, max_len=25,
                                  transcriber="faster-whisper", llm="none"))
    assert res["transcriber"] and res["clips"]
    c = res["clips"][0]
    assert c["transcript"]
    assert 10 <= c["end"] - c["start"] <= 25
