"""
Measured content-type cues and the rule that turns them into a profile.

Metadata (title, tags, game) decides most videos. When it doesn't (no clear
match, a generic title), the first minutes are measured instead:

    speech_ratio   share of seconds that look like speech (audio features)
    chat_per_min   chat messages per minute (None when there is no chat replay)
    motion         mean scene-change score of the picture (None without video)
    face_area      median share of the frame the biggest face covers, over frames
                   that have one; a talking head is large, a streamer's facecam small
                   (None without video or without any face)

The rules are deliberately simple and explained in the reason shown to the
user, who can always pick another profile.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass

import numpy as np

from recaply.profiles.base import Detection

HEAD_SEC = 600               # the first ten minutes
TALK_FACE_AREA = 0.03
LOW_MOTION = 0.012
BUSY_CHAT = 6.0              # messages per minute
FACE_SAMPLES = 10


@dataclass
class Cues:
    speech_ratio: float = 0.0
    chat_per_min: float | None = None
    motion: float | None = None
    face_area: float | None = None

    def describe(self) -> str:
        bits = [f"speech {self.speech_ratio:.0%}"]
        if self.chat_per_min is not None:
            bits.append(f"chat {self.chat_per_min:.0f}/min")
        if self.motion is not None:
            bits.append("busy picture" if self.motion > LOW_MOTION else "still picture")
        if self.face_area is not None:
            bits.append("big face" if self.face_area >= TALK_FACE_AREA else "small face")
        return ", ".join(bits)

    def public(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def speech_ratio(features: np.ndarray, seconds: int = HEAD_SEC) -> float:
    """Share of seconds flagged as speech, ignoring the leading/trailing silence of the first `seconds`."""
    head = features[:seconds, 4]
    if len(head) == 0:
        return 0.0
    active = features[:seconds, 0] > (np.percentile(features[:, 0], 10) + 6.0)
    return float(head[active].mean()) if active.any() else 0.0


def face_area(path: str, duration: float, samples: int = FACE_SAMPLES) -> float | None:
    """Median area share of the biggest face over frames sampled from the first minutes of a local video."""
    import cv2

    import editor
    span = min(duration, HEAD_SEC)
    if span <= 0:
        return None
    w = 480
    fps = samples / span
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-t", f"{span:.1f}", "-i", path, "-an", "-vf",
           f"fps={fps:.5f},scale={w}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    try:
        sw, sh = int(probe[0]), int(probe[1])
    except (ValueError, IndexError):
        return None
    h = int(round(w * sh / sw / 2) * 2)
    raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    n = len(raw) // (w * h * 3)
    if n == 0:
        return None
    frames = np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    det = cv2.FaceDetectorYN.create(editor.FACE_MODEL, "", (w, h), 0.7)
    areas = []
    for f in frames:
        r = det.detect(np.ascontiguousarray(f))[1]
        if r is not None and len(r):
            areas.append(max(float(row[2]) * float(row[3]) for row in r) / (w * h))
    return float(np.median(areas)) if areas else None


def classify(c: Cues) -> Detection:
    """A profile from the measured cues (see module docstring). Always returns something."""
    talkish = 0.0
    gamey = 0.0
    why = []
    if c.speech_ratio >= 0.55:
        talkish += 1.5
        why.append("mostly speech")
    elif c.speech_ratio <= 0.3:
        gamey += 1.0
        why.append("little speech")
    if c.face_area is not None:
        if c.face_area >= TALK_FACE_AREA:
            talkish += 1.5
            why.append("a large face fills the frame")
        else:
            gamey += 0.5
    if c.motion is not None:
        if c.motion <= LOW_MOTION:
            talkish += 1.0
            why.append("a still picture")
        else:
            gamey += 1.0
            why.append("a busy picture")
    if c.chat_per_min is not None:
        if c.chat_per_min >= BUSY_CHAT:
            gamey += 1.0
            why.append("an active chat")
        else:
            talkish += 0.5
    scores = {"talk": talkish, "gaming": gamey}
    if talkish >= 2.5 and talkish > gamey + 1.0:
        return Detection("talk", "measured: " + ", ".join(why), scores)
    if gamey >= 2.0 and gamey > talkish + 0.5 and (c.speech_ratio < 0.5 or c.motion and c.motion > LOW_MOTION):
        return Detection("gaming", "measured: " + ", ".join(why), scores)
    return Detection("irl", "measured: " + (", ".join(why) or "nothing decisive") + "; balanced profile", scores)


def is_weak(det: Detection) -> bool:
    """True when metadata detection had little to go on (no or a single weak match)."""
    return not det.scores or max(det.scores.values()) < 3.0
