import numpy as np

from recaply.profiles import get_profile
from recaply.providers.transcribe.base import blocks_for
from recaply.score.select import dedupe, select
from recaply.score.windows import peak_windows, sliding_windows, text_score, top_windows


def clip(i, s, e, score):
    return {"id": i, "start": s, "end": e, "score": score}


def test_dedupe_drops_overlapping_lower_scores():
    clips = [clip(1, 0, 30, 50), clip(2, 10, 40, 80), clip(3, 100, 130, 60), clip(4, 125, 150, 10)]
    kept = [c["id"] for c in dedupe(clips)]
    assert kept == [2, 3, 4]          # 1 overlaps 2 by 20s (>30%); 4 overlaps 3 by 5s of 25s (20%): kept


def test_select_ranks_and_limits():
    clips = [clip(i, i * 100, i * 100 + 30, s) for i, s in enumerate([10, 90, 50, 70])]
    out = select(clips, 2)
    assert [c["id"] for c in out] == [1, 3] and [c["rank"] for c in out] == [1, 2]
    assert select(clips, 5, min_score=60) == [c for c in out]


def test_peak_windows_find_separate_peaks():
    prof = get_profile("gaming")
    fused = np.zeros(3600, np.float32)
    fused[600:605] = 1.0
    fused[2000:2004] = 0.8
    fused[2010:2012] = 0.7                  # same moment as 2000: one window
    ws = peak_windows(fused, prof, k=10, duration=3600)
    peaks = sorted(round(w.peak) for w in ws[:2])
    assert abs(peaks[0] - 602) <= 3 and abs(peaks[1] - 2002) <= 6
    assert all(w.start <= w.peak <= w.end for w in ws)
    assert ws[0].signal_score >= ws[1].signal_score


def test_sliding_windows_cover_video():
    prof = get_profile("talk")
    ws = sliding_windows(np.zeros(600, np.float32), prof, 600)
    assert ws[0].start == 0 and ws[-1].end == 600
    assert all(b.start < a.end for a, b in zip(ws, ws[1:]))   # overlapping


def test_text_score_prefers_lively_speech():
    prof = get_profile("gaming")
    flat = [{"w": "okay", "s": i * 2.0, "e": i * 2.0 + .3} for i in range(10)]
    hype = [{"w": w, "s": i * .35, "e": i * .35 + .3}
            for i, w in enumerate("no way bro that was insane let's go haha what!".split())]
    assert text_score(hype, prof, 0, 20) > text_score(flat, prof, 0, 20)
    assert text_score([], prof, 0, 20) == 0


def test_top_windows_skip_overlaps():
    prof = get_profile("talk")
    ws = sliding_windows(np.zeros(600, np.float32), prof, 600)
    for i, w in enumerate(ws):
        w.text_score = 1.0 - i * 0.01
    top = top_windows(ws, 3, 1.0)
    assert len(top) == 3
    for a in top:
        for b in top:
            if a is not b:
                assert min(a.end, b.end) - max(a.start, b.start) <= 0.5 * min(a.length, b.length)


def test_transcription_block_grid():
    # 30-second blocks
    assert blocks_for([(10, 25)], 3600) == [0]
    assert blocks_for([(50, 130)], 3600) == [1, 2, 3, 4]
    assert blocks_for([(0, 60)], 3600) == [0, 1]               # end exactly on a boundary
    assert blocks_for([(3590, 3700)], 3600) == [119]           # never past the end
    assert blocks_for([(0, 20), (10, 40)], 3600) == [0, 1]     # shared blocks counted once
