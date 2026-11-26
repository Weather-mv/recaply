"""
Filler-word and long-silence removal ("tighten").

From the clip's word timings: filler words (um, uh, erm...) are cut out and
pauses longer than `max_gap` are shortened to `keep_gap`. The result is a
list of kept segments of the clip plus the words re-timed to the tightened
clip; `apply()` renders it with one ffmpeg pass (short audio fades at each
join so there are no clicks).
"""

from __future__ import annotations

import os
import re
import subprocess

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mhm"}
MIN_SEGMENT = 0.25       # shorter kept pieces are merged away (they'd only flash)
FADE = 0.012


def is_filler(word: str) -> bool:
    return re.sub(r"[^a-z]", "", word.lower()) in FILLERS


def keep_segments(words: list[dict], duration: float, max_gap: float = 0.6, keep_gap: float = 0.2,
                  fillers: bool = True, silences: bool = True,
                  content_gap: float | None = None) -> tuple[list[tuple[float, float]], list[dict]]:
    """
    Segments of [0, duration] to keep, and the kept words re-timed to the
    output. With no words (or nothing to remove) the whole clip is kept.
    `content_gap`: a stretch without speech longer than this is content
    (gameplay between callouts), not dead air, and is kept. None = cut all.
    """
    words = sorted((w for w in words if 0 <= w["s"] < duration), key=lambda w: w["s"])
    if not words or not (fillers or silences):
        return [(0.0, duration)], [dict(w) for w in words]
    cuts: list[tuple[float, float]] = []
    prev_end = 0.0
    for i, w in enumerate(words):
        gap = w["s"] - prev_end
        if silences and gap > max_gap and not (content_gap and gap > content_gap):
            # keep keep_gap/2 after the previous word and before this one
            cuts.append((prev_end + keep_gap / 2, w["s"] - keep_gap / 2))
        if fillers and is_filler(w["w"]):
            nxt = words[i + 1]["s"] if i + 1 < len(words) else duration
            cuts.append((w["s"], min(nxt, w["e"] + 0.05)))
            prev_end = max(prev_end, w["s"])          # the next gap is measured from the filler's start
            continue
        prev_end = max(prev_end, w["e"])
    if silences and duration - prev_end > max_gap and not (content_gap and duration - prev_end > content_gap):
        cuts.append((prev_end + keep_gap / 2, duration))
    # merge overlapping cuts
    cuts.sort()
    merged: list[list[float]] = []
    for a, b in cuts:
        if b - a <= 0.02:
            continue
        if merged and a <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    segs, t = [], 0.0
    for a, b in merged:
        if a - t > 0:
            segs.append((t, a))
        t = b
    if duration - t > 0:
        segs.append((t, duration))
    segs = [s for s in segs if s[1] - s[0] >= MIN_SEGMENT] or [(0.0, duration)]
    # re-time the words that survive
    out, offset_in = [], 0.0
    for a, b in segs:
        for w in words:
            if a <= w["s"] < b and not (fillers and is_filler(w["w"])):
                x = dict(w)
                x["s"] = round(offset_in + (w["s"] - a), 3)
                x["e"] = round(offset_in + (min(w["e"], b) - a), 3)
                out.append(x)
        offset_in += b - a
    return [(round(a, 3), round(b, 3)) for a, b in segs], out


def removed_seconds(segs: list[tuple[float, float]], duration: float) -> float:
    return round(duration - sum(b - a for a, b in segs), 2)


def apply(src: str, dst: str, segs: list[tuple[float, float]], has_audio: bool = True) -> str:
    """Write the kept segments of `src` back to back into `dst` (H.264 + AAC). Returns the encoder."""
    from pipeline import ENCODER_ARGS, h264_encoder
    parts, labels = [], []
    for i, (a, b) in enumerate(segs):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            d = b - a
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, d - FADE):.3f}:d={FADE}[a{i}]")
            labels.append(f"[v{i}][a{i}]")
        else:
            labels.append(f"[v{i}]")
    n = len(segs)
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}" + ("[v][a]" if has_audio else "[v]"))
    graph = ";".join(parts)
    encoders = [h264_encoder()]
    if encoders[0] != "libx264":
        encoders.append("libx264")
    tmp = dst + ".part.mp4"
    err = ""
    for enc in encoders:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", src, "-filter_complex", graph, "-map", "[v]"]
        if has_audio:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", enc, *ENCODER_ARGS[enc], "-movflags", "+faststart", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True)
        err = r.stderr
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dst)
            return enc
        if os.path.exists(tmp):
            os.remove(tmp)
    raise RuntimeError(f"Removing pauses failed: {err.strip()[-300:]}")
