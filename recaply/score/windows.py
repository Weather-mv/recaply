"""
Candidate windows: the shortlist that gets the expensive treatment
(transcription + LLM). Two ways to build it:

- `peak_windows`: around the peaks of the fused signal curve (gaming, IRL).
- `sliding_windows` + `text_score`: over a full transcript (talk content,
  where loudness says little about what's worth clipping).

Words are dicts {"w": text, "s": start sec, "e": end sec, "p": prob}, the
same format the editor's captions use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from recaply.profiles.base import Profile
from recaply.signals.base import smooth

BLOCK_SEC = 30.0
LAUGH = re.compile(r"\b(ha(ha)+|lol|lmao|laugh\w*)\b|\[laugh", re.IGNORECASE)


@dataclass
class Window:
    start: float
    end: float
    peak: float
    signal_score: float                       # 0..1 from the fused curve
    text_score: float = 0.0                   # 0..1 from the transcript (0 until transcribed)
    words: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)   # per-signal peak values, for reasons
    probe: tuple | None = None                # speech-heavy funnel: the short span transcribed first

    @property
    def length(self) -> float:
        return self.end - self.start

    def score(self, text_weight: float) -> float:
        """Shortlist rank: signal and text blended by the profile's transcript weight."""
        return (1 - text_weight) * self.signal_score + text_weight * self.text_score


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Seconds shared by [a0,a1] and [b0,b1]."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def window_signal_score(fused: np.ndarray, start: float, end: float) -> float:
    """Peak and sustained level inside a window: 0.35 * max + 0.65 * mean of the top 30%
    (a window with one loud second ranks below one that stays intense)."""
    seg = fused[int(start): max(int(start) + 1, int(end))]
    if len(seg) == 0:
        return 0.0
    top = np.sort(seg)[-max(1, int(len(seg) * 0.3)):]
    return float(0.35 * seg.max() + 0.65 * top.mean())


def peak_windows(fused: np.ndarray, profile: Profile, k: int, duration: float,
                 signals: dict[str, np.ndarray] | None = None) -> list[Window]:
    """Up to k windows centred on the strongest separate peaks of `fused`."""
    n = len(fused)
    if n == 0:
        return []
    curve = smooth(fused, 5)
    from scipy.signal import find_peaks
    dist = max(10, int(profile.clip_target * 0.8))
    idx, _ = find_peaks(curve, distance=dist)
    if len(idx) == 0:
        idx = np.array([int(np.argmax(curve))])
    idx = sorted(idx, key=lambda i: curve[i], reverse=True)
    out: list[Window] = []
    for i in idx:
        if curve[i] <= 0.01 and out:
            break
        s = max(0.0, i - profile.window_pre)
        e = min(duration or n, i + profile.window_post)
        if any(overlap(s, e, w.start, w.end) > 0.5 * min(e - s, w.length) for w in out):
            continue
        w = Window(start=float(s), end=float(e), peak=float(i),
                   signal_score=window_signal_score(fused, s, e))
        if signals:
            w.signals = {name: round(float(v[int(s):int(e) + 1].max()), 3)
                         for name, v in signals.items() if len(v[int(s):int(e) + 1])}
        out.append(w)
        if len(out) >= k:
            break
    return out


def sliding_windows(fused: np.ndarray, profile: Profile, duration: float) -> list[Window]:
    """Overlapping windows covering the whole video (for full-transcript profiles)."""
    length = profile.clip_max + 15
    step = max(10.0, profile.clip_target / 2)
    out: list[Window] = []
    t = 0.0
    total = duration or len(fused)
    while t < total:
        e = min(total, t + length)
        out.append(Window(start=t, end=e, peak=(t + e) / 2,
                          signal_score=window_signal_score(fused, t, e) if len(fused) else 0.0))
        if e >= total:
            break
        t += step
    return out


def probe_windows(fused: np.ndarray, profile: Profile, duration: float, every: float, length: float) -> list[Window]:
    """Cheap even sampling for speech-heavy videos where loudness says little: a short probe every
    `every` seconds. Each Window spans a full clip window around the probe; `probe` is the part that
    gets transcribed first."""
    out: list[Window] = []
    total = duration or len(fused)
    c = every / 2
    while c < total:
        p0 = float(int(c // BLOCK_SEC) * BLOCK_SEC)          # snap to one 30 s transcription block: one block per probe
        p1 = min(total, p0 + max(length, BLOCK_SEC))
        s, e = max(0.0, c - profile.window_pre), min(total, c + profile.window_post)
        w = Window(start=float(s), end=float(e), peak=float(c),
                   signal_score=window_signal_score(fused, s, e) if len(fused) else 0.0)
        w.probe = (p0, p1)
        out.append(w)
        c += every
    return out


def words_between(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["s"] >= start and w["e"] <= end + 0.5]


def text_score(words: list[dict], profile: Profile, start: float, end: float) -> float:
    """
    Cheap 0..1 score of how "clippable" the speech in [start, end] looks:
    speech density, profile keywords, questions/exclamations and laughter.
    Used to pre-filter before the LLM, and as the fallback when no LLM is set.
    """
    ws = words_between(words, start, end)
    span = max(1.0, end - start)
    if not ws:
        return 0.0
    text = " ".join(w["w"] for w in ws).lower()
    density = min(1.0, len(ws) / span / 3.0)
    hits = sum(text.count(k) for k in profile.keywords) if profile.keywords else 0
    kw = min(1.0, hits * 0.25)
    punct = min(1.0, (text.count("?") + text.count("!")) * 0.15)
    laugh = min(1.0, len(LAUGH.findall(text)) * 0.3)
    return round(0.35 * density + 0.35 * kw + 0.15 * punct + 0.15 * laugh, 4)


def top_windows(windows: list[Window], k: int, text_weight: float) -> list[Window]:
    """Best k windows by blended score, skipping ones that mostly overlap a better one."""
    ranked = sorted(windows, key=lambda w: w.score(text_weight), reverse=True)
    out: list[Window] = []
    for w in ranked:
        if any(overlap(w.start, w.end, o.start, o.end) > 0.5 * min(w.length, o.length) for o in out):
            continue
        out.append(w)
        if len(out) >= k:
            break
    return out
