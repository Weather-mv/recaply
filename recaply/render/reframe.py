"""
Reframing a 16:9 clip for 9:16 / 1:1 / 4:5 (16:9 passes through).

Strategies (each becomes the video layers of an editor document):

- face    follow the speaker: faces are found at 2 fps (OpenCV YuNet), the
          main face is tracked, and a virtual camera pans to keep it centred.
          The camera has a dead zone and eases between positions, so it
          moves like an operator, not like a jittery tracker.
- split   streamer cam on top, gameplay below (the editor's reaction
          detector finds the cam box and the gameplay area).
- split_bottom  the same, gameplay on top and the cam below.
- pip     gameplay fills the frame, the cam sits in a box near the top.
- action  gameplay: a fixed crop centred on where the motion is.
- center  a fixed centre crop.
- fit     the whole frame, full width, over a blurred copy of itself.

`auto` looks for a streamer cam first (a face in a corner box means a
facecam stream, so cam + gameplay), then follows the profile's preference,
and falls back to `fit` when nothing reliable is found.

The cam box can also be given by hand (`cam_box`, fractions of the frame),
and the cam pane's share of the height by `split_ratio`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

import editor

STRATEGIES = ("auto", "split", "split_bottom", "pip", "face", "action", "center", "fit")
CAM_LAYOUTS = ("split", "split_bottom", "pip")
SPLIT_RATIO = 0.42       # default share of the height the cam pane gets
MAX_CORNER_CAM = 0.5     # a cam box bigger than this share of the frame is a talking head, not a facecam
SAMPLE_FPS = 2.0
SAMPLE_W = 640
DEADZONE = 0.07          # face may drift this far (share of frame width) before the camera follows
MOVE_SEC = 0.6           # how long a camera move takes
MIN_FACE_SHARE = 0.45    # face seen in this share of samples -> face tracking is reliable


@dataclass
class FrameSamples:
    times: np.ndarray                 # seconds
    frames: list                      # BGR uint8 arrays (SAMPLE_W wide)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.frames[0].shape[:2]
        return w, h


@dataclass
class Plan:
    strategy: str
    reason: str
    data: dict = field(default_factory=dict)


def sample_frames(path: str, duration: float, fps: float = SAMPLE_FPS, width: int = SAMPLE_W) -> FrameSamples:
    """Frames at `fps` from one sequential decode (much faster than seeking per frame)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-an",
           "-vf", f"fps={fps},scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=600).stdout
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        sw, sh = (int(v) for v in probe.strip().split(",")[:2])
    except ValueError:
        sw, sh = 16, 9
    h = int(round(width * sh / sw / 2) * 2)
    n = len(raw) // (width * h * 3)
    if n == 0:
        raise RuntimeError("Couldn't read frames from the clip.")
    arr = np.frombuffer(raw[: n * width * h * 3], np.uint8).reshape(n, h, width, 3)
    return FrameSamples(times=np.arange(n) / fps, frames=list(arr))


# ---------- faces ----------

def detect_faces(samples: FrameSamples) -> list[list[tuple[float, float, float, float, float]]]:
    """Per frame: faces as (cx, cy, w, h, score), fractions of the frame."""
    import cv2
    W, H = samples.size
    det = cv2.FaceDetectorYN.create(editor.FACE_MODEL, "", (W, H), 0.7)
    out = []
    for f in samples.frames:
        r = det.detect(np.ascontiguousarray(f))[1]
        faces = []
        for row in (r if r is not None else []):
            x, y, w, h, score = float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[-1])
            if w >= 8 and h >= 8:
                faces.append(((x + w / 2) / W, (y + h / 2) / H, w / W, h / H, score))
        out.append(faces)
    return out


def main_face_track(faces: list[list[tuple]]) -> tuple[list[float | None], float]:
    """
    The main subject's x centre per sample (None where not seen) and the
    share of samples it was seen in. The main subject is the biggest face,
    kept with hysteresis: we only switch to another face if it is clearly
    bigger for a while (avoids flicking between two people).
    """
    xs: list[float | None] = []
    cur = None
    challenger, streak = None, 0
    for fs in faces:
        if not fs:
            xs.append(None)
            continue
        big = max(fs, key=lambda f: f[2] * f[3] * f[4])
        if cur is None:
            cur = big
        else:
            near = min(fs, key=lambda f: abs(f[0] - cur[0]))
            if abs(near[0] - cur[0]) < 0.15:
                cur = near
            if big is not near and big[2] * big[3] > 1.6 * near[2] * near[3]:
                streak = streak + 1 if challenger is not None and abs(challenger[0] - big[0]) < 0.1 else 1
                challenger = big
                if streak >= 3:
                    cur, streak = big, 0
            else:
                streak = 0
        xs.append(cur[0])
    seen = sum(1 for x in xs if x is not None) / max(1, len(xs))
    return xs, seen


def camera_path(xs: list[float | None], times: np.ndarray, deadzone: float = DEADZONE,
                move_sec: float = MOVE_SEC) -> list[tuple[float, float]]:
    """
    Virtual camera centre over time as keyframes [(t, x)]: holds still while
    the subject stays within the dead zone, eases to it when it leaves.
    Missing detections hold the last position.
    """
    known = [x for x in xs if x is not None]
    if not known:
        return [(0.0, 0.5)]
    # fill gaps and remove single-sample outliers
    filled, last = [], known[0]
    for x in xs:
        last = x if x is not None else last
        filled.append(last)
    arr = np.array(filled, dtype=float)
    if len(arr) >= 5:
        from scipy.ndimage import median_filter
        arr = median_filter(arr, size=5, mode="nearest")
    cam = float(arr[0])
    keys = [(0.0, round(cam, 4))]
    busy_until = -1.0
    for t, x in zip(times, arr):
        if t < busy_until:
            continue
        if abs(x - cam) > deadzone:
            keys.append((round(float(t), 3), round(cam, 4)))           # hold until now...
            cam = float(x)
            keys.append((round(float(t) + move_sec, 3), round(cam, 4)))  # ...then ease over
            busy_until = float(t) + move_sec
    # drop duplicate holds
    out = [keys[0]]
    for k in keys[1:]:
        if k[0] > out[-1][0] + 1e-3:
            out.append(k)
    return out


# ---------- action / gameplay ----------

def action_center(samples: FrameSamples, crop_share: float) -> float:
    """x centre (fraction) of the fixed crop that holds the most motion. crop_share = crop width / frame width."""
    import cv2
    if len(samples.frames) < 2 or crop_share >= 1:
        return 0.5
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.int16) for f in samples.frames]
    energy = np.zeros(g[0].shape[1], np.float64)
    for a, b in zip(g, g[1:]):
        energy += np.abs(b - a).mean(axis=0)
    W = len(energy)
    cw = max(1, int(W * crop_share))
    win = np.convolve(energy, np.ones(cw), mode="valid")          # sum over each crop position
    # prefer the centre slightly: gameplay HUD edges move a lot but aren't the action
    pos = np.arange(len(win))
    centre_bias = 1.0 - 0.15 * np.abs((pos + cw / 2) / W - 0.5) * 2
    best = int(np.argmax(win * centre_bias))
    return (best + cw / 2) / W


def content_rows(samples: FrameSamples, dark: float = 16.0) -> tuple[float, float]:
    """
    (top, bottom) of the picture as fractions of the height, without the black
    letterbox bars some videos have burned in. A row is a bar row when it stays
    dark in every sample. Bars under 2% are ignored.
    """
    import cv2
    if not samples.frames:
        return 0.0, 1.0
    rows = np.max([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean(axis=1) for f in samples.frames], axis=0)
    lit = np.nonzero(rows > dark)[0]
    if not len(lit):
        return 0.0, 1.0
    n = len(rows)
    top, bottom = lit[0] / n, (lit[-1] + 1) / n
    top = top if top >= 0.02 else 0.0
    bottom = bottom if bottom <= 0.98 else 1.0
    if bottom - top < 0.5:                       # a mostly dark video, not bars
        return 0.0, 1.0
    return round(float(top), 4), round(float(bottom), 4)


def inset(rect, share: float = 0.04):
    """`rect` shrunk by `share` of its size on each side: drops the frame a streamer draws around the cam."""
    x, y, w, h = rect
    return [round(x + w * share, 5), round(y + h * share, 5), round(w * (1 - 2 * share), 5), round(h * (1 - 2 * share), 5)]


def cam_rect(det: dict):
    """The cam box to crop from: exactly as drawn by hand, a little inside the detected one."""
    return list(det["cam"]) if det.get("manual") else inset(det["cam"])


# ---------- choosing ----------

def find_cam(path: str, meta: dict) -> dict | None:
    """The reaction detector's result when it found a corner facecam, else None."""
    try:
        det = editor.detect_reaction(path, meta)
    except Exception:  # noqa: BLE001
        return None
    cam = det.get("cam")
    if det.get("found") and cam and cam[2] * cam[3] < MAX_CORNER_CAM:
        return det
    return None


def manual_cam(cam_box) -> dict | None:
    """A cam box the user drew ([x, y, w, h] fractions) as a detector result, or None when it isn't usable."""
    try:
        x, y, w, h = (float(v) for v in cam_box)
    except (TypeError, ValueError):
        return None
    x, y = min(max(x, 0.0), 0.98), min(max(y, 0.0), 0.98)
    w, h = min(max(w, 0.02), 1 - x), min(max(h, 0.02), 1 - y)
    return {"found": True, "cam": [round(v, 5) for v in (x, y, w, h)], "face": None, "game": [0, 0, 1, 1],
            "manual": True}


CAM_NAMES = {"split": "cam on top, gameplay below", "split_bottom": "gameplay on top, cam below",
             "pip": "gameplay with the cam in a box"}


def choose(path: str, meta: dict, preferred: str, target_ratio: float, fallback: str = "face",
           cam_box=None, split_ratio: float | None = None) -> Plan:
    """
    Pick and measure a strategy for this clip. target_ratio = output w / h.
    `fallback` is the profile's preference, used by "auto" when there's no facecam.
    """
    src_ratio = meta["w"] / meta["h"]
    if target_ratio >= src_ratio * 0.98:
        return Plan("fit", "the output is as wide as the source")
    if preferred not in STRATEGIES:
        preferred = "auto"
    ratio_ = min(0.6, max(0.25, float(split_ratio))) if split_ratio else SPLIT_RATIO
    manual = manual_cam(cam_box) if cam_box else None
    cache: list = []

    def samples() -> FrameSamples:
        if not cache:
            cache.append(sample_frames(path, meta["duration"]))
            cache.append(content_rows(cache[0]))
        return cache[0]

    def rows() -> tuple[float, float]:
        samples()
        return cache[1]

    def share() -> float:
        """crop width / frame width for a full-height crop of the picture (letterbox bars left out)"""
        top, bottom = rows()
        return target_ratio / (src_ratio / (bottom - top))

    def face_plan(reason):
        s_ = samples()
        xs, seen = main_face_track(detect_faces(s_))
        if seen >= MIN_FACE_SHARE:
            return Plan("face", f"{reason}; face in {int(seen * 100)}% of frames",
                        {"keys": camera_path(xs, s_.times), "seen": round(seen, 2), "rows": rows()})
        return None

    def action_plan(reason):
        return Plan("action", reason, {"cx": action_center(samples(), share()), "rows": rows()})

    def cam_plan(kind, det):
        who = "your cam box" if det.get("manual") else "streamer cam found"
        data = {"det": det, "ratio": ratio_}
        if kind == "pip":
            data["cx"] = action_center(samples(), target_ratio / src_ratio)
        return Plan(kind, f"{who}; {CAM_NAMES[kind]}", data)

    if preferred == "auto":
        det = manual or find_cam(path, meta)
        if det:
            return cam_plan("split", det)
        if fallback in CAM_LAYOUTS:            # a gaming profile with no cam on screen: follow the action
            return action_plan("no facecam found; crop follows the action")
        preferred = fallback if fallback in STRATEGIES and fallback != "auto" else "face"
    if preferred in CAM_LAYOUTS:
        det = manual or find_cam(path, meta)
        if det:
            return cam_plan(preferred, det)
        plan = face_plan("no separate cam")
        if plan and plan.data["seen"] >= 0.7:
            return plan
        return action_plan("no facecam found (draw the cam box to set it by hand); crop follows the action")
    if preferred == "face":
        plan = face_plan("talking content")
        if plan:
            return plan
        return Plan("fit", "no steady face found; showing the whole frame")
    if preferred == "action":
        return action_plan("crop centred on the action")
    if preferred == "center":
        return Plan("center", "centre crop", {"cx": 0.5, "rows": rows()})
    return Plan("fit", "whole frame over a blurred background")


def analyse(path: str, meta: dict, target_ratio: float = 9 / 16) -> dict:
    """
    What the studio's live preview needs to draw every layout as the export
    will: the facecam box (if any), the gameplay area, the face camera path
    and the action crop centre. Rects are [x, y, w, h] fractions of the frame.
    """
    det = find_cam(path, meta)
    s_ = sample_frames(path, meta["duration"])
    top, bottom = content_rows(s_)
    crop_share = min(1.0, target_ratio / (meta["w"] / meta["h"] / (bottom - top)))
    xs, seen = main_face_track(detect_faces(s_))
    return {
        "cam": cam_rect(det) if det else None,
        "rows": [top, bottom],
        "cam_face": det.get("face") if det else None,
        "game": (det.get("game") if det else None) or [0, 0, 1, 1],
        "face_keys": camera_path(xs, s_.times) if seen >= MIN_FACE_SHARE else [],
        "face_seen": round(seen, 2),
        "action_cx": round(action_center(s_, crop_share), 4),
    }


# ---------- document layers ----------

def _base(id_, name):
    return {"id": id_, "type": "video", "name": name, "flip": False, "opacity": 1, "blur": 0, "bright": 1,
            "hidden": False, "locked": False, "start": 0, "end": None, "kf": {},
            "anim_in": {"type": "none", "dur": 0.5}, "anim_out": {"type": "none", "dur": 0.5}}


def video_layers(plan: Plan, meta: dict, W: int, H: int) -> list[dict]:
    """The editor layers (bottom to top) that show the video for `plan` on a W x H canvas."""
    backdrop = editor.backdrop_layer(meta, W, H)
    sw, sh = meta["w"], meta["h"]
    if plan.strategy == "fit":
        L = {**_base("video", "Video"), **editor.fit_video(meta, W, H, "fit")}
        return [backdrop, L]
    top, bottom = plan.data.get("rows") or (0.0, 1.0)          # the picture without letterbox bars
    rh = max(0.05, bottom - top)
    if plan.strategy in ("center", "action"):
        lw = H * sw / (sh * rh)                                  # full-height picture, cropped by the canvas
        crop_share = min(1.0, W / lw)
        cx = float(plan.data.get("cx", 0.5))
        x0 = min(max(cx - crop_share / 2, 0.0), 1 - crop_share)
        L = {**_base("video", "Video"), "crop": [round(x0, 5), round(top, 5), round(crop_share, 5), round(rh, 5)],
             "x": 0, "y": 0, "w": W, "h": H}
        return [L]
    if plan.strategy == "face":
        lw = H * sw / (sh * rh)
        L = {**_base("video", "Speaker"), "crop": [0, round(top, 5), 1, round(rh, 5)], "x": 0, "y": 0,
             "w": round(lw, 2), "h": H}
        keys = []
        for t, cx in plan.data["keys"]:
            x = min(0.0, max(W - lw, W / 2 - cx * lw))
            keys.append([round(t, 3), round(x, 2), 0.0, "ease"])
        if len(keys) == 1:
            L["x"] = keys[0][1]
        else:
            L["x"] = keys[0][1]
            L["kf"] = {"pos": keys}
        return [L]
    if plan.strategy in ("split", "split_bottom"):
        det = plan.data["det"]
        cam, game, face = cam_rect(det), det.get("game") or [0, 0, 1, 1], det.get("face")
        ch = round(H * float(plan.data.get("ratio") or SPLIT_RATIO))
        gh = H - ch
        cam_crop = _crop_inside(cam, W / ch, face, sw, sh)
        game_crop = _crop_avoiding(game, W / gh, cam, sw, sh)
        cam_y, game_y = (0, ch) if plan.strategy == "split" else (gh, 0)
        g = {**_base("game", "Gameplay"), "crop": game_crop, "x": 0, "y": game_y, "w": W, "h": gh}
        c = {**_base("cam", "Reaction cam"), "crop": cam_crop, "x": 0, "y": cam_y, "w": W, "h": ch}
        return [backdrop, g, c]
    if plan.strategy == "pip":
        det = plan.data["det"]
        cam, game = cam_rect(det), det.get("game") or [0, 0, 1, 1]
        game_crop = _crop_around(game, W / H, float(plan.data.get("cx", 0.5)), sw, sh)
        g = {**_base("game", "Gameplay"), "crop": game_crop, "x": 0, "y": 0, "w": W, "h": H}
        c = {**_base("cam", "Reaction cam"), "crop": [round(v, 5) for v in cam],
             **pip_box(cam, float(plan.data.get("ratio") or SPLIT_RATIO), sw, sh, W, H)}
        return [backdrop, g, c]
    raise ValueError(plan.strategy)


def pip_box(cam, ratio_: float, sw: int, sh: int, W: int, H: int) -> dict:
    """Where the cam goes in "pip": centred near the top, (ratio + 0.2) of the canvas wide, in its own aspect."""
    bw = W * min(0.8, ratio_ + 0.2)
    aspect = (cam[2] * sw) / max(1e-6, cam[3] * sh)
    bh = bw / aspect
    if bh > H * 0.4:
        bh = H * 0.4
        bw = bh * aspect
    return {"x": round((W - bw) / 2, 2), "y": round(H * 0.07, 2), "w": round(bw, 2), "h": round(bh, 2)}


def _crop_around(rect, aspect, cx, sw, sh):
    """A crop of `rect` with output aspect `aspect`, centred on x = cx (fraction of the frame) where it fits."""
    base = _crop_inside(rect, aspect, None, sw, sh)
    x, _, w, _ = rect
    base[0] = round(min(max(cx - base[2] / 2, x), x + w - base[2]), 5)
    return base


def _crop_inside(rect, aspect, face, sw, sh):
    """Largest crop of `rect` (fractions) with output aspect `aspect`, centred on the face if given."""
    x, y, w, h = rect
    cw, ch = w, h
    if (w * sw) / (h * sh) > aspect:
        cw = (h * sh * aspect) / sw
    else:
        ch = (w * sw / aspect) / sh
    fcx = face[0] + face[2] / 2 if face else x + w / 2
    fcy = face[1] + face[3] * 0.9 if face else y + h / 2
    cx = min(max(fcx - cw / 2, x), x + w - cw)
    cy = min(max(fcy - ch / 2, y), y + h - ch)
    return [round(v, 5) for v in (cx, cy, cw, ch)]


def _crop_avoiding(rect, aspect, avoid, sw, sh):
    """A crop of `rect` with aspect `aspect` that overlaps `avoid` (the cam) as little as possible."""
    base = _crop_inside(rect, aspect, None, sw, sh)
    if not avoid:
        return base
    x, y, w, h = rect

    def ov(a, b):
        ow = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        oh = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        return ow * oh if ow > 0 and oh > 0 else 0.0

    horiz = base[2] < w - 1e-4
    best, best_o, best_d = base, ov(base, avoid), 0.0
    for i in range(41):
        c = list(base)
        if horiz:
            c[0] = x + (w - c[2]) * i / 40
        else:
            c[1] = y + (h - c[3]) * i / 40
        o = ov(c, avoid)
        d = abs(c[0] - base[0]) if horiz else abs(c[1] - base[1])
        if o < best_o - 1e-6 or (abs(o - best_o) < 1e-6 and d < best_d):
            best, best_o, best_d = c, o, d
    return [round(v, 5) for v in best]
