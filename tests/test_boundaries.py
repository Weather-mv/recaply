import numpy as np

from recaply.score.boundaries import sentences, snap


def words_from(text_with_times):
    """[(word, start, end)] -> word dicts."""
    return [{"w": w, "s": s, "e": e, "p": 1.0} for w, s, e in text_with_times]


def speech(start, n_sentences=12, words_per=6, wdur=0.3, gap=0.1, pause=0.9):
    """Regular speech: sentences of `words_per` words ending with a period."""
    out, t = [], start
    for i in range(n_sentences):
        for j in range(words_per):
            w = f"w{i}_{j}" + ("." if j == words_per - 1 else "")
            out.append((w, round(t, 2), round(t + wdur, 2)))
            t += wdur + gap
        t += pause
    return words_from(out)


def inside_any_word(words, t):
    return any(w["s"] < t < w["e"] for w in words)


def test_sentences_split_on_punctuation_and_pauses():
    ws = words_from([("Hi", 0, .3), ("there.", .4, .7), ("So", .8, 1.0), ("um", 2.5, 2.7), ("yes", 2.8, 3.0)])
    assert sentences(ws) == [(0, .7), (.8, 1.0), (2.5, 3.0)]


def test_never_starts_or_ends_mid_word():
    ws = speech(100)
    for start in np.arange(100, 130, 0.37):
        s, e = snap(ws, float(start), float(start) + 20, 10, 30, 1000)
        assert not inside_any_word(ws, s), (start, s)
        assert not inside_any_word(ws, e), (start, e)
        assert 10 <= e - s <= 30


def test_snaps_to_sentence_start_and_end():
    ws = speech(100)
    starts = {round(a, 2) for a, _ in sentences(ws)}
    ends = {round(b, 2) for _, b in sentences(ws)}
    s, e = snap(ws, 106.0, 121.0, 10, 30, 1000)
    assert any(abs(s + 0.15 - x) < 0.02 for x in starts)       # sentence start minus the lead-in pad
    assert any(abs(e - 0.35 - x) < 0.02 for x in ends)         # sentence end plus the tail pad


def test_length_limits_enforced():
    ws = speech(0, n_sentences=40)
    s, e = snap(ws, 10, 200, 15, 45, 1000)
    assert e - s <= 45
    s, e = snap(ws, 10, 12, 15, 45, 1000)
    assert e - s >= 15


def test_clamped_to_video():
    s, e = snap([], 590, 640, 15, 60, 600.0)
    assert 0 <= s < e <= 600
    assert e - s >= 15
    s, e = snap([], -5, 10, 15, 60, 600.0)
    assert s == 0 and e >= 15


def test_quiet_point_used_without_speech():
    energy = np.full(300, 50.0)
    energy[98] = 0.0                      # a quiet beat near the requested start
    s, _ = snap([], 100, 130, 15, 60, 300, energy=energy)
    assert s == 98
