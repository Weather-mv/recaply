"""
Import a video straight into the Editor: no analysis, just one clip over the video (the first
EDIT_MAX seconds of a long one) so every Director tool works on it: captions, layouts, facecam,
trims, word cuts, preview and export. Words are transcribed the first time the clip is opened.
"""
from __future__ import annotations

from recaply.ingest.sources import resolve_file
from recaply.profiles import get_profile
from common import fmt_hms

EDIT_MAX = 600.0           # longest clip opened at once; the trim fields pick another part of a longer video
SCORER = "editor"          # marks an Editor import (no ranking was done)


def edit_result(path: str, title: str = "", profile: str = "talk") -> dict:
    """A job result for `path` with one editable clip. Raises InputError when the file can't be read."""
    src = resolve_file(path)
    if title:
        src.title = title
    try:
        prof = get_profile(profile)
    except KeyError:
        prof = get_profile("talk")
    duration = round(float(src.duration or 0), 3)
    end = round(min(duration, EDIT_MAX), 3) if duration else EDIT_MAX
    clip = {
        "id": 1, "start": 0.0, "end": end, "duration": round(end, 2), "time": fmt_hms(0),
        "score": None, "llm_score": None, "virality": None, "signal_score": None, "text_score": None,
        "reason": "Added in the Editor", "title": src.title, "hook": "", "description": "", "hashtags": [],
        "emphasis": [], "beats": [], "beat_score": None, "transcript": "", "signals": {}, "window": [0.0, end],
    }
    return {
        "source": src.public(), "source_path": src.location,
        "profile": {"key": prof.key, "label": prof.label, "reason": "Editor import",
                    "clip_min": prof.clip_min, "clip_max": prof.clip_max},
        "scorer": SCORER, "transcriber": None, "clips": [clip], "shortlist": 1,
        "options": {"profile": prof.key, "allow_local": True, "language": None},
        "signals_used": [], "chat_messages": 0, "cues": None, "funnel": {}, "summary": {},
        "editor": {"long": duration > EDIT_MAX, "max": EDIT_MAX},
    }
