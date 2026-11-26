"""
Hook check: does the clip's opening make a viewer stay?

Audio spikes and motion find loud moments, not good openers. After the
clips are snapped to their final start, the best `hook_top` of them get one
more AI pass on what a viewer actually meets first: the words of the first
3 seconds and a description of the first frame (a local vision model in
Ollama, or a plain OpenCV description when there's none). The model rates
the HOOK STRENGTH 1-10 and says whether the clip starts mid-action; when it
does, it names the stakes in a few words ("5-star wanted level", "last
round"), which the shorts renderer shows for the first second.

Clips rated below `hook_min` are dropped. If none pass, the three best
openers are kept (with a note) so a project is never empty.
"""

from __future__ import annotations

import base64
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from recaply.log import get_logger
from recaply.providers.http import ProviderError
from recaply.providers.llm import LLM
from recaply.telemetry import Telemetry

log = get_logger("hook")

HOOK_SECONDS = 3.0
KEEP_IF_NONE_PASS = 3
FRAME_W = 448
STAKES_MAX = 40          # characters on screen
MID_ACTION_MOTION = 0.7  # motion in the first 3 s above which a clip counts as starting mid-action

SYSTEM = ("You are a viral short-form editor. You judge only the first 3 seconds of a clip: what the viewer "
          "hears and sees before deciding to scroll away. Answer with JSON only.")

# the reason comes first: small local models score better after they've said why
SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "mid_action": {"type": "boolean"},
        "stakes": {"type": "string"},
    },
    "required": ["reason", "score", "mid_action", "stakes"],
}

RUBRIC = (
    "HOOK STRENGTH scale (use the whole range):\n"
    "1-3: noise. Nothing is said, or filler words, and the picture alone doesn't show what will happen.\n"
    "4-5: something is going on, but a new viewer can't tell what or why to care.\n"
    "6-7: a clear situation or a strong line; the viewer gets curious about what happens next.\n"
    "8-9: instantly clear tension or a promise of a payoff (a question, a threat, a bold claim, a big reaction).\n"
    "10: the viewer immediately knows what the payoff will be and must see it.\n"
)


def opening_words(words: list[dict], start: float, seconds: float = HOOK_SECONDS) -> str:
    """What is said in the clip's first `seconds` (words are absolute seconds)."""
    return " ".join(w["w"] for w in words if start - 0.05 <= w["s"] < start + seconds).strip()


def build_prompt(clip: dict, opening: str, frame: str, title: str, profile_label: str) -> str:
    sig = clip.get("opening_signals") or clip.get("signals") or {}
    loud = ", ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in sorted(sig.items(), key=lambda kv: -kv[1])[:3])
    return (
        f"Video: \"{title}\" ({profile_label}).\n"
        f"Clip: {clip['duration']:.0f} s long. The payoff (later in the clip, the viewer doesn't know it yet; "
        f"never put it in stakes): {clip.get('reason') or 'unknown'}.\n\n"
        f"OPENING 3 SECONDS\n"
        f"- Said: \"{opening or '(nothing, no speech)'}\"\n"
        f"- First frame: {frame or 'unknown'}\n"
        f"- Measured at the start: {loud or 'nothing notable'}\n\n"
        "Rate this opening 3 seconds on HOOK STRENGTH from 1-10. A 10 means the viewer immediately knows what "
        "the payoff will be and wants to see it. A 3 means it's just noise.\n"
        + RUBRIC +
        "Judge the meaning, whatever the language: Hindi, Hinglish and English are all fine for this audience. "
        "If nothing is said, judge the picture: gameplay action, a chase or a big reaction can hook without words.\n\n"
        "Then: does the clip start mid-action (things are already happening, a chase, a fight, a reaction) with "
        "no setup a new viewer needs? Set mid_action true or false.\n"
        "Always write `stakes`: an on-screen label of 2 to 5 words that tells a new viewer what is going on or "
        "at stake in THIS clip, from what is said or seen in it (e.g. who is chasing whom, what could be lost). "
        "It sets up the tension and NEVER gives away how it ends (\"Cops right behind him\", not \"Chase ends in "
        "a crash\"). Don't repeat the video title. Don't guess facts that aren't in the clip; if you can't tell, "
        "\"\".\n"
        "Return JSON with: reason (one short sentence about the opening), score (1-10), mid_action (true/false), "
        "stakes (text or \"\")."
    )


def parse(data: dict, title: str = "", payoff: str = "") -> dict | None:
    try:
        score = int(round(float(data.get("score"))))
    except (TypeError, ValueError):
        return None
    if not 1 <= score <= 10:
        return None
    mid = data.get("mid_action")
    mid = mid if isinstance(mid, bool) else str(mid).strip().lower() in ("true", "yes", "1")
    stakes = " ".join(str(data.get("stakes") or "").split()).strip(" \"'.")
    if len(stakes) < 3 or stakes.lower() in ("none", "n/a", "no", "null"):
        stakes = ""
    norm = lambda t: " ".join("".join(ch for ch in t.lower() if ch.isalnum() or ch == " ").split())  # noqa: E731
    def copied(a: str, b: str) -> bool:
        """one is a copy of the other (only texts long enough to mean something are compared)"""
        a, b = norm(a), norm(b)
        return len(min(a, b, key=len)) >= 8 and (a in b or b in a)

    if stakes and title and copied(stakes, title):
        stakes = ""                               # the video's title says nothing about this clip's stakes
    if stakes and payoff and copied(stakes, payoff):
        stakes = ""                               # a copy of the payoff would spoil the clip
    words = stakes.split()
    if len(words) > 6:                            # keep it a label, not a sentence
        stakes = " ".join(words[:6])
    return {"score": score, "reason": " ".join(str(data.get("reason") or "").split())[:200],
            "mid_action": bool(mid), "stakes": stakes[:STAKES_MAX]}


# ---------- the first frame ----------

def grab_frames(inputs: list[tuple[str, dict]], times: list[float]) -> dict[float, bytes]:
    """A JPEG of the video at each time (`inputs` = the video stream: a file or a stream URL). Missing on failure."""
    from pipeline import http_input_args
    loc, headers = inputs[0]

    def one(t):
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        if loc.startswith("http"):
            if headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            cmd += http_input_args()
        cmd += ["-ss", f"{max(0.0, t + 0.1):.2f}", "-i", loc, "-frames:v", "1", "-an",
                "-vf", f"scale={FRAME_W}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "pipe:1"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return t, b""
        return t, out

    with ThreadPoolExecutor(4) as ex:
        return {t: jpg for t, jpg in ex.map(one, times) if jpg}


def describe_with_vision(jpg: bytes, model: str, base_url: str, timeout: float = 90) -> str:
    """One sentence about the frame from a local vision model in Ollama ("" when it fails)."""
    from recaply.providers.http import post_json
    from recaply.providers.llm import _OLLAMA_TURN
    # Small captioners (moondream) answer best to the plain prompt on /api/generate; chat-style questions
    # made them start mid-word and repeat themselves.
    body = {"model": model, "stream": False, "keep_alive": "2m", "prompt": "Describe this image.",
            "images": [base64.b64encode(jpg).decode()],
            "options": {"temperature": 0, "num_predict": 70, "repeat_penalty": 1.3}}
    try:
        with _OLLAMA_TURN:
            res = post_json(f"{base_url.rstrip('/')}/api/generate", body, timeout=timeout, retries=1)
    except (ProviderError, OSError, ValueError) as e:
        log.warning("frame description failed", model=model, error=str(e)[:160])
        return ""
    text = " ".join((res.get("response") or "").split())
    if not text or not text[0].isalpha() or not text[0].isupper():
        return ""                                 # a broken caption reads worse than the OpenCV description
    sentences = [s.strip() for s in text.replace("!", ".").split(". ") if s.strip()]
    return (". ".join(sentences[:2]).rstrip(".") + ".")[:300]


def describe_plain(jpg: bytes) -> str:
    """What OpenCV can say about a frame without a vision model: faces, light, colour and how busy it is."""
    import cv2

    import editor
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return ""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bright = float(gray.mean())
    sat = float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean())
    edges = float((cv2.Canny(gray, 80, 160) > 0).mean())
    parts = []
    try:
        det = cv2.FaceDetectorYN.create(editor.FACE_MODEL, "", (w, h), 0.7)
        faces = det.detect(img)[1]
        faces = [] if faces is None else faces
    except Exception:  # noqa: BLE001
        faces = []
    if len(faces):
        big = max(faces, key=lambda f: f[2] * f[3])
        share = big[2] * big[3] / (w * h)
        where = "in a small corner box (a streamer's facecam)" if share < 0.03 else "close-up" if share > 0.08 else "in frame"
        parts.append(f"{len(faces)} face{'s' if len(faces) > 1 else ''}, the biggest {where}")
    else:
        parts.append("no faces")
    parts.append("dark scene" if bright < 50 else "bright scene" if bright > 160 else "normal lighting")
    parts.append("busy, detailed picture (likely action or a game HUD)" if edges > 0.12
                 else "simple, calm picture" if edges < 0.04 else "moderately detailed picture")
    if sat > 110:
        parts.append("very colourful")
    return "; ".join(parts)


def vision_ready(model: str, base_url: str) -> bool:
    if not model:
        return False
    from recaply.providers.llm import ollama_status
    st = ollama_status(base_url, model)
    return bool(st["running"] and st["model_ready"])


# ---------- the pass ----------

def check_hooks(clips: list[dict], words: list[dict], frames: dict[float, bytes], llm: LLM, title: str,
                profile_label: str, vision_model: str = "", base_url: str = "",
                telemetry: Telemetry | None = None) -> int:
    """
    Rate the opening of each clip in place: adds hook_score (1-10), hook_reason, hook_frame,
    mid_action and context (the stakes line, "" when none). Returns how many were rated.
    """
    use_vision = vision_ready(vision_model, base_url)
    rated = 0
    # every frame first, then every rating: on a small GPU Ollama would otherwise swap the vision model and
    # the text model in and out for each clip
    described: dict[int, str] = {}
    for i, c in enumerate(clips):
        jpg = frames.get(c["start"])
        if jpg:
            frame = describe_with_vision(jpg, vision_model, base_url) if use_vision else ""
            described[i] = frame or describe_plain(jpg)
    kwargs = {"schema": SCHEMA} if getattr(llm, "supports_schema", False) else {}

    def rate(i: int):
        c = clips[i]
        try:
            return llm.complete_json(SYSTEM, build_prompt(c, opening_words(words, c["start"]), described.get(i, ""),
                                                          title, profile_label), 400, **kwargs)
        except (ProviderError, OSError, ValueError) as e:
            log.warning("hook check failed", clip=c.get("id"), error=str(e)[:160])
            return None

    # a cloud model answers several at once; a local one (one small GPU) is asked one at a time
    workers = 1 if getattr(llm, "provider", "") == "ollama" else min(4, max(1, len(clips)))
    with ThreadPoolExecutor(workers) as ex:
        answers = list(ex.map(rate, range(len(clips))))
    for i, c in enumerate(clips):
        frame = described.get(i, "")
        opening = opening_words(words, c["start"])
        if answers[i] is None:
            continue
        data, usage = answers[i]
        if telemetry:
            telemetry.add_tokens(usage.provider, usage.tokens_in, usage.tokens_out)
        got = parse(data, title, c.get("reason") or "")
        if not got:
            continue
        # mid-action: the model says so, or the first 3 s are already fast motion
        motion = float((c.get("opening_signals") or c.get("signals") or {}).get("motion", 0) or 0)
        mid = got["mid_action"] or motion >= MID_ACTION_MOTION
        c.update(hook_score=got["score"], hook_reason=got["reason"], hook_frame=frame[:300],
                 hook_opening=opening[:200], mid_action=mid, context=got["stakes"] if mid else "")
        rated += 1
    return rated


def apply(clips: list[dict], min_score: int, weight: float = 0.25) -> tuple[list[dict], str]:
    """
    Drop rated clips below `min_score` (unrated ones, e.g. when the AI failed, stay) and fold the hook into
    the score (weight of 10 x hook). Returns (clips, note). When nothing passes, the best openers are kept.
    """
    rated = [c for c in clips if c.get("hook_score") is not None]
    if not rated:
        return clips, ""
    for c in rated:
        c["score"] = round((1 - weight) * c["score"] + weight * 10 * c["hook_score"], 1)
    passed = [c for c in clips if c.get("hook_score") is None or c["hook_score"] >= min_score]
    dropped = len(clips) - len(passed)
    if passed and any(c.get("hook_score") is not None for c in passed):
        note = f"hook check: {dropped} of {len(rated)} clips had a weak opening (below {min_score}/10) and were left out" \
            if dropped else ""
        return passed, note
    best = sorted(rated, key=lambda c: (c["hook_score"], c["score"]), reverse=True)[:KEEP_IF_NONE_PASS]
    unrated = [c for c in clips if c.get("hook_score") is None]
    return best + unrated, (f"hook check: no clip scored {min_score}/10 or more on its opening, "
                            f"so the {len(best)} strongest openers are shown")
