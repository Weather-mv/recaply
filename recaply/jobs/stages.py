"""
Structured job stages, so the dashboard can show a stepper and say exactly
where a job failed:

    download -> analyze -> transcribe -> score -> render

The pipeline reports free-text progress ("Transcribing Moments (Groq Cloud)...");
`stage_for()` maps it onto a fixed stage. `StageTracker` keeps, per stage,
its state (pending / running / done / failed / skipped), progress within the
stage, start and end times and the error, plus a short job log (notes and
warnings, tagged with the stage they happened in). Both are stored on the
job record (`stages`, `log` columns) and served by the studio API.
"""

from __future__ import annotations

import time

STAGES = ("download", "analyze", "transcribe", "score", "render")
LABELS = {"download": "Downloading", "analyze": "Analyzing", "transcribe": "Transcribing",
          "score": "Scoring", "render": "Rendering"}
ANALYZE_STAGES = STAGES[:4]
# overall progress range of each analyze stage (engine.analyze's fractions), for progress within a stage
RANGES = {"download": (0.0, 0.22), "analyze": (0.22, 0.34), "transcribe": (0.34, 0.76), "score": (0.76, 1.0)}
MAX_LOG = 300

_KEYWORDS = (("transcrib", "transcribe"), ("story beat", "score"), ("scor", "score"),
             ("download", "download"), ("reading the link", "download"), ("starting", "download"),
             ("analyz", "analyze"), ("checking", "analyze"), ("extracting", "analyze"),
             ("cutting", "render"), ("render", "render"), ("editing", "render"))


def stage_for(label: str) -> str | None:
    """The fixed stage a progress text belongs to; None for texts that aren't a stage ("Done")."""
    low = (label or "").lower()
    for kw, key in _KEYWORDS:
        if kw in low:
            return key
    return None


def _blank() -> dict:
    return {k: {"state": "pending", "progress": 0.0, "started": None, "finished": None, "error": None, "detail": ""}
            for k in STAGES}


class StageTracker:
    def __init__(self, stages: dict | None = None, log: list | None = None):
        base = _blank()
        for k, v in (stages or {}).items():
            if k in base and isinstance(v, dict):
                base[k].update(v)
        self.stages = base
        self.log: list[dict] = list(log or [])[-MAX_LOG:]

    @classmethod
    def from_job(cls, job: dict) -> "StageTracker":
        return cls(job.get("stages") if isinstance(job.get("stages"), dict) else None,
                   job.get("log") if isinstance(job.get("log"), list) else None)

    @property
    def current(self) -> str | None:
        for k in STAGES:
            if self.stages[k]["state"] == "running":
                return k
        return None

    def start(self, key: str, detail: str = "") -> bool:
        """Make `key` the running stage (earlier running stages are done). True when the stage changed."""
        cur = self.current
        if cur == key:
            if detail:
                self.stages[key]["detail"] = detail[:120]
            return False
        now = time.time()
        if cur is not None:
            self._close(cur, "done", now)
        for k in STAGES[:STAGES.index(key)]:             # stages jumped over (all cached, or not needed)
            if self.stages[k]["state"] == "pending" and k in ANALYZE_STAGES and key in ANALYZE_STAGES:
                self.stages[k]["state"] = "skipped"
        s = self.stages[key]
        s.update(state="running", progress=0.0, started=now, finished=None, error=None, detail=detail[:120])
        return True

    def progress(self, label: str, overall: float) -> bool:
        """Feed one engine progress call. True when the running stage changed (worth saving at once)."""
        key = stage_for(label)
        if key is None:
            return False
        changed = self.start(key, label)
        lo, hi = RANGES.get(key, (0.0, 1.0))
        frac = (float(overall) - lo) / (hi - lo) if hi > lo else float(overall)
        s = self.stages[key]
        s["progress"] = round(max(s["progress"], min(1.0, max(0.0, frac))), 3)
        return changed

    def set_progress(self, key: str, frac: float) -> None:
        if self.stages[key]["state"] != "running":
            self.start(key)
        self.stages[key]["progress"] = round(min(1.0, max(0.0, float(frac))), 3)

    def _close(self, key: str, state: str, now: float | None = None, error: str | None = None) -> None:
        s = self.stages[key]
        s.update(state=state, finished=now or time.time(), error=error)
        if state == "done":
            s["progress"] = 1.0

    def finish(self, keys: tuple[str, ...] = ANALYZE_STAGES) -> None:
        """The task ended well: its running stage is done, its stages never reached are skipped."""
        cur = self.current
        if cur is not None:
            self._close(cur, "done")
        for k in keys:
            if self.stages[k]["state"] == "pending":
                self.stages[k]["state"] = "skipped"

    def fail(self, error: str, key: str | None = None) -> str:
        """The task failed: the running stage (or `key`, or the first unfinished one) is marked failed. Returns that stage."""
        key = key or self.current or next((k for k in STAGES if self.stages[k]["state"] in ("pending", "running")),
                                          STAGES[0])
        if self.stages[key]["started"] is None:
            self.stages[key]["started"] = time.time()
        self._close(key, "failed", error=(error or "")[:500])
        self.add("error", error, key)
        return key

    def add(self, level: str, message: str, stage: str | None = None) -> None:
        self.log.append({"t": round(time.time(), 2), "level": level, "stage": stage or self.current,
                         "msg": " ".join(str(message).split())[:400]})
        del self.log[:-MAX_LOG]

    @property
    def failed_stage(self) -> str | None:
        return next((k for k in STAGES if self.stages[k]["state"] == "failed"), None)

    def public(self) -> list[dict]:
        return [{"key": k, "label": LABELS[k], **self.stages[k]} for k in STAGES]


def public(job: dict) -> dict:
    """{"stages": [...], "current", "failed_stage"} for a job record (works for jobs made before stages existed)."""
    t = StageTracker.from_job(job)
    return {"stages": t.public(), "current": t.current, "failed_stage": t.failed_stage}
