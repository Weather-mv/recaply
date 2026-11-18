"""
Runs one task of a clip job: "analyze" (find and score clips) or "cut"
(cut chosen clips). Called by whichever queue is in use: a thread of the
app (local) or an RQ worker process (server). Progress goes to the job
store, throttled to a few writes per second.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from recaply.engine import Options, analyze, cut_clips
from recaply.ingest.sources import InputError
from recaply.jobs import store
from recaply.log import get_logger
from recaply.settings import settings
from recaply.telemetry import Telemetry

log = get_logger("jobs")
# refund(keys) callbacks for clips charged to a plan, kept in memory by the local queue
REFUNDS: dict[str, Callable[[list], None]] = {}


def job_dir(job_id: str) -> str:
    return os.path.join(settings.data_dir, "_clipjobs", job_id)


def _friendly(e: Exception) -> str:
    if isinstance(e, InputError):
        return str(e)
    try:
        from common import friendly_error
        return friendly_error(e)[:500]
    except Exception:  # noqa: BLE001
        return str(e)[:500]


def run_task(job_id: str, task: str = "analyze") -> None:
    job = store.get(job_id)
    if job is None or job["state"] == "cancelled":
        return
    if task == "analyze":
        _analyze(job)
    elif task == "cut":
        _cut(job)
    else:
        raise ValueError(f"unknown task {task}")


def _analyze(job: dict) -> None:
    jid = job["id"]
    last = [0.0, ""]

    def progress(stage: str, frac: float):
        now = time.time()
        if stage != last[1] or now - last[0] > 0.4 or frac >= 1.0:
            last[0], last[1] = now, stage
            store.update(jid, stage=stage, progress=round(float(frac), 3))

    store.update(jid, state="running", task="analyze", stage="Starting", progress=0.0, error=None)
    tel = Telemetry()
    try:
        opts = Options(**{k: v for k, v in (job.get("options") or {}).items() if k in Options.__dataclass_fields__})
        result = analyze(job["input"], opts, progress=progress, telemetry=tel)
        store.update(jid, state="ready", stage="Done", progress=1.0, result=result,
                     title=result["source"]["title"], summary=result["summary"])
        log.info("job ready", job=jid, clips=len(result["clips"]), sec=result["summary"]["total_sec"])
    except Exception as e:  # noqa: BLE001
        log.error("job failed", job=jid, error=str(e)[:300])
        store.update(jid, state="failed", stage="Failed", error=_friendly(e), summary=tel.summary())


def _cut(job: dict) -> None:
    jid = job["id"]
    args = job.get("task_args") or {}
    ids = [int(i) for i in args.get("ids", [])]
    quality = args.get("quality", "1080")
    result = job.get("result") or {}
    store.update(jid, state="running", task="cut", stage=f"Cutting {len(ids)} clips", progress=0.0)
    done = [0]

    def progress(cid: int, state: str, frac: float):
        if state in ("done", "failed"):
            done[0] += 1
            store.update(jid, progress=round(done[0] / max(1, len(ids)), 3))
        elif frac == 0.0 or frac >= 1.0:
            store.set_cut(jid, cid, {"state": "cutting"})

    tel = Telemetry()
    try:
        out = cut_clips(result, ids, job_dir(jid), quality=quality, progress=progress, telemetry=tel)
    except Exception as e:  # noqa: BLE001 — the whole batch failed (e.g. source gone)
        out = {i: {"error": _friendly(e)} for i in ids}
    failed = []
    for cid, info in out.items():
        if "error" in info:
            failed.append(cid)
            store.set_cut(jid, cid, {"state": "failed", "error": info["error"]})
        else:
            store.set_cut(jid, cid, {"state": "done", **info, "quality": quality})
    refund = REFUNDS.pop(jid, None)
    if failed and refund:
        try:
            refund([f"clip:{jid}:{i}" for i in failed])
        except Exception:  # noqa: BLE001
            pass
    summary = (job.get("summary") or {})
    summary = {**summary, "cut": tel.summary()["stages"]}
    store.update(jid, state="ready", stage="Done" if not failed else f"{len(failed)} clips failed",
                 progress=1.0, summary=summary)
