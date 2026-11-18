import numpy as np

from recaply.score.fusion import DEAD_AIR_FACTOR, edge_mask, fuse


def test_missing_signals_are_renormalised():
    a = np.full(1000, 0.4, np.float32)
    # chat has weight but isn't available: audio alone should give its own value back
    out = fuse({"audio_energy": a}, {"audio_energy": 0.3, "chat_rate": 0.7}, 1000, edges=False)
    assert np.allclose(out, 0.4)


def test_weighted_average():
    a = np.full(100, 1.0, np.float32)
    b = np.zeros(100, np.float32)
    out = fuse({"a": a, "b": b}, {"a": 0.25, "b": 0.75}, 100, edges=False)
    assert np.allclose(out, 0.25)


def test_overlap_boost_when_signals_agree():
    a = np.full(100, 0.6, np.float32)
    b = np.full(100, 0.6, np.float32)
    c = np.zeros(100, np.float32)
    both = fuse({"a": a, "b": b}, {"a": 1, "b": 1}, 100, edges=False)
    one = fuse({"a": a, "c": c}, {"a": 1, "c": 1}, 100, edges=False)
    assert both[50] > 0.6                 # boosted above the plain average
    assert np.isclose(one[50], 0.3)       # no agreement, no boost


def test_dead_air_and_edges_penalised():
    a = np.full(10_000, 0.5, np.float32)
    dead = np.zeros(10_000, bool)
    dead[5000:5010] = True
    out = fuse({"a": a}, {"a": 1}, 10_000, dead=dead)
    assert np.isclose(out[5005], 0.5 * DEAD_AIR_FACTOR)
    assert out[0] < out[3000]             # intro
    assert out[-1] < out[3000]            # outro
    assert np.isclose(out[3000], 0.5)


def test_edge_mask_bounds():
    m = edge_mask(3600)                    # intro min(90 s, 3%), outro min(120 s, 3%) = 108 s
    assert m[:90].all() and not m[90:3492].any() and m[3492:].all()
    m = edge_mask(36_000)
    assert m[:90].all() and not m[90:35_880].any() and m[35_880:].all()
    assert edge_mask(0).size == 0


def test_lengths_and_clipping():
    out = fuse({"a": np.full(50, 5.0)}, {"a": 1}, 80, edges=False)
    assert len(out) == 80 and out.max() <= 1.0 and out[60] == 0
    assert fuse({}, {"a": 1}, 10).tolist() == [0.0] * 10
