"""
Runs one task of a clip job: "analyze" (find and score clips), "cut"
(cut chosen clips) or "shorts" (render chosen clips as finished shorts). Called by whichever queue is in use: a thread of the
app (local) or an RQ worker process (server). Progress goes to the job
store, throttled to a few writes per second.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from recaply.engine import Options, analyze, cut_clips, edit_clip, render_preview, render_shorts
from recaply.ingest.sources import InputError
from recaply.jobs import store
from recaply.jobs.stages import LABELS, StageTracker
from recaply.log import get_logger
from recaply.settings import settings
from recaply.telemetry import Telemetry

log = get_logger("jobs")
# refund(keys) callbacks for clips charged to a plan, kept in memory by the local queue
REFUNDS: dict[str, Callable[[list], None]] = {}


def merge_summary(summary: dict | None, tel: Telemetry, key: str) -> dict:
    """The job summary with a later task's stages, tokens, audio minutes and cost added (so usage covers edits and shorts too)."""
    base = dict(summary or {})
    t = tel.summary()
    base[key] = t["stages"]
    base["est_usd"] = round(float(base.get("est_usd") or 0) + t["est_usd"], 5)
    tokens = {k: dict(v) for k, v in (base.get("tokens") or {}).items()}
    for prov, v in t["tokens"].items():
        a = tokens.setdefault(prov, {"in": 0, "out": 0, "calls": 0})
        for f in ("in", "out", "calls"):
            a[f] += v.get(f, 0)
    base["tokens"] = tokens
    audio = dict(base.get("audio_min") or {})
    for prov, m in t["audio_min"].items():
        audio[prov] = round(audio.get(prov, 0.0) + m, 2)
    base["audio_min"] = audio
    return base


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
    elif task == "shorts":
        _shorts(job)
    elif task == "edit":
        _edit(job)
    elif task == "preview":
        _preview(job)
    else:
        raise ValueError(f"unknown task {task}")


class _TrackedTelemetry(Telemetry):
    """Telemetry whose notes also go to the job log, tagged with the stage they happened in."""

    def __init__(self, tracker: StageTracker):
        super().__init__()
        self.tracker = tracker

    def note(self, text: str) -> None:
        super().note(text)
        self.tracker.add("warning", text)


def _analyze(job: dict) -> None:
    jid = job["id"]
    last = [0.0, ""]
    tracker = StageTracker()                                  # a (re)started analysis starts its stepper afresh

    def progress(stage: str, frac: float):
        now = time.time()
        changed = tracker.progress(stage, frac)
        if changed or stage != last[1] or now - last[0] > 0.4 or frac >= 1.0:
            last[0], last[1] = now, stage
            store.update(jid, stage=stage, progress=round(float(frac), 3), stages=tracker.stages, log=tracker.log)

    tracker.start("download", "Starting")
    tracker.add("info", "Job started")
    store.update(jid, state="running", task="analyze", stage="Starting", progress=0.0, error=None,
                 stages=tracker.stages, log=tracker.log)
    tel = _TrackedTelemetry(tracker)
    try:
        opts = Options(**{k: v for k, v in (job.get("options") or {}).items() if k in Options.__dataclass_fields__})
        result = analyze(job["input"], opts, progress=progress, telemetry=tel)
        tracker.finish()
        tracker.add("info", f"Found {len(result['clips'])} clips (scored by {result.get('scorer')})")
        fresh = store.get(jid) or job
        store.update(jid, state="ready", stage="Done", progress=1.0, result=result,
                     cuts=kept_cuts((fresh.get("result") or {}).get("clips") or [], result["clips"], fresh.get("cuts") or {}),
                     title=result["source"]["title"], summary=result["summary"], stages=tracker.stages, log=tracker.log)
        log.info("job ready", job=jid, clips=len(result["clips"]), sec=result["summary"]["total_sec"])
    except Exception as e:  # noqa: BLE001
        log.error("job failed", job=jid, error=str(e)[:300])
        msg = _friendly(e)
        where = tracker.fail(msg)
        store.update(jid, state="failed", stage=f"Failed while {LABELS[where].lower()}", error=msg,
                     summary=tel.summary(), stages=tracker.stages, log=tracker.log)


def kept_cuts(old_clips: list[dict], new_clips: list[dict], cuts: dict) -> dict:
    """
    The cut / short / preview / edit records still valid after a re-analysis.
    Clip ids are positions in the new result, so a record is kept only when
    its id still means the same moment (same start and end); otherwise a clip
    would play another clip's video.
    """
    def span(c):
        return round(float(c["start"]), 2), round(float(c["end"]), 2)
    old = {c["id"]: span(c) for c in old_clips}
    new = {c["id"]: span(c) for c in new_clips}
    out = {}
    for key, rec in cuts.items():
        digits = key.lstrip("pse")
        if digits.isdigit() and old.get(int(digits)) is not None and old.get(int(digits)) == new.get(int(digits)):
            out[key] = rec
    return out


def _render_start(job: dict, label: str) -> StageTracker:
    """The job's stepper with its render stage running (cut / shorts / edit tasks)."""
    tracker = StageTracker.from_job(job)
    tracker.stages["render"].update(state="pending", progress=0.0, error=None, finished=None)
    tracker.start("render", label)
    tracker.add("info", label)
    return tracker


def _render_end(jid: str, tracker: StageTracker, failed: int, total: int, error: str | None) -> None:
    if failed and failed >= total:
        tracker.fail(error or "Nothing was rendered.", "render")
    else:
        if failed:
            tracker.add("warning", f"{failed} of {total} failed: {error or ''}", "render")
        tracker.finish(("render",))
    store.update(jid, stages=tracker.stages, log=tracker.log)


def _cut(job: dict) -> None:
    jid = job["id"]
    args = job.get("task_args") or {}
    ids = [int(i) for i in args.get("ids", [])]
    quality = args.get("quality", "1080")
    result = job.get("result") or {}
    store.update(jid, state="running", task="cut", stage=f"Cutting {len(ids)} clips", progress=0.0)
    tracker = _render_start(job, f"Cutting {len(ids)} clips")
    store.update(jid, stages=tracker.stages, log=tracker.log)
    done = [0]

    def progress(cid: int, state: str, frac: float):
        if state in ("done", "failed"):
            done[0] += 1
            tracker.set_progress("render", done[0] / max(1, len(ids)))
            store.update(jid, progress=round(done[0] / max(1, len(ids)), 3), stages=tracker.stages)
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
    _render_end(jid, tracker, len(failed), len(ids), next((out[i]["error"] for i in failed), None))
    summary = (job.get("summary") or {})
    summary = {**summary, "cut": tel.summary()["stages"]}
    store.update(jid, state="ready", stage="Done" if not failed else f"{len(failed)} clips failed",
                 progress=1.0, summary=summary)


def edit_key(clip_id: int) -> str:
    """Key of a clip's transcript edit in the job's `cuts` record."""
    return f"e{int(clip_id)}"


def edit_words_path(jid: str, clip_id: int) -> str:
    return os.path.join(job_dir(jid), f"edit_{int(clip_id)}.json")


def _edit(job: dict) -> None:
    import json
    jid = job["id"]
    args = job.get("task_args") or {}
    cid = int(args.get("clip", -1))
    result = job.get("result") or {}
    cuts = job.get("cuts") or {}
    store.update(jid, state="running", task="edit", stage="Editing the clip", progress=0.1)
    store.set_cut(jid, edit_key(cid), {"state": "rendering", "error": None})
    tracker = _render_start(job, f"Editing clip {cid}")
    store.update(jid, stages=tracker.stages, log=tracker.log)
    err = None
    tel = Telemetry()
    try:
        info = edit_clip(result, cid, [int(i) for i in args.get("deleted", [])], job_dir(jid),
                         cut_file=(cuts.get(str(cid)) or {}).get("file"), broll=bool(args.get("broll")), telemetry=tel)
        with open(edit_words_path(jid, cid), "w", encoding="utf-8") as f:
            json.dump(info["words"], f)
        if str(cid) not in cuts:                                  # the plain cut made on the way is kept (reused by shorts)
            p = os.path.join(job_dir(jid), info["cut_file"])
            store.set_cut(jid, cid, {"state": "done", "file": info["cut_file"], "quality": "1080",
                                     "size_mb": round(os.path.getsize(p) / 1e6, 1) if os.path.exists(p) else 0})
        store.set_cut(jid, edit_key(cid), {"state": "done", "file": info["file"], "deleted": info["deleted"],
                                           "duration": info["duration"], "removed_sec": info["removed_sec"],
                                           "broll": info["broll"], "notes": info["notes"], "broll_on": bool(args.get("broll"))})
        stage = "Done"
    except Exception as e:  # noqa: BLE001
        log.error("edit failed", job=jid, clip=cid, error=str(e)[:300])
        err = _friendly(e)
        store.set_cut(jid, edit_key(cid), {"state": "failed", "error": err})
        stage = "The edit failed"
    _render_end(jid, tracker, 1 if err else 0, 1, err)
    summary = merge_summary(job.get("summary"), tel, "edit")
    store.update(jid, state="ready", stage=stage, progress=1.0, summary=summary)


def preview_key(clip_id: int) -> str:
    """Key of a clip's 5-second preview in the job's `cuts` record."""
    return f"p{int(clip_id)}"


def _preview(job: dict) -> None:
    """A free, low-resolution preview of a clip (crop + captions). Never touches the stepper or the plan's clip count."""
    jid = job["id"]
    args = job.get("task_args") or {}
    cid = int(args.get("id", -1))
    key = preview_key(cid)
    store.update(jid, state="running", task="preview", stage="Rendering a preview", progress=0.1)
    store.set_cut(jid, key, {"state": "rendering", "stage": "Starting", "error": None, "file": None})
    tel = Telemetry()
    last = [""]

    def progress(frac: float, label: str):
        if label != last[0]:
            last[0] = label
            store.set_cut(jid, key, {"state": "rendering", "stage": label})
        store.update(jid, progress=round(float(frac), 3))

    try:
        info = render_preview(job.get("result") or {}, cid, job_dir(jid), args.get("options"), args.get("start"),
                              args.get("end"), owner=job.get("owner"), progress=progress, telemetry=tel)
        store.set_cut(jid, key, {"state": "done", "stage": "", "file": info["file"], "layout": info["layout"],
                                 "duration": info["duration"], "start": info["start"], "end": info["end"],
                                 "notes": info["notes"], "error": None, "t": round(time.time(), 1)})
        stage = "Done"
    except Exception as e:  # noqa: BLE001 — a failed preview never fails the project
        log.error("preview failed", job=jid, clip=cid, error=str(e)[:300])
        store.set_cut(jid, key, {"state": "failed", "stage": "", "error": _friendly(e)})
        stage = "The preview failed"
    store.update(jid, state="ready", stage=stage, progress=1.0)


def short_key(clip_id: int) -> str:
    """Key of a clip's short in the job's `cuts` record (plain ids are the cut files)."""
    return f"s{int(clip_id)}"


def _shorts(job: dict) -> None:
    jid = job["id"]
    args = job.get("task_args") or {}
    ids = [int(i) for i in args.get("ids", [])]
    result = job.get("result") or {}
    cuts = job.get("cuts") or {}
    store.update(jid, state="running", task="shorts", stage=f"Rendering {len(ids)} shorts", progress=0.0)
    tracker = _render_start(job, f"Rendering {len(ids)} shorts")
    store.update(jid, stages=tracker.stages, log=tracker.log)
    stage = {}
    finished = [0]

    def progress(cid: int, label: str, frac: float):
        if frac >= 1.0:
            finished[0] += 1
            tracker.set_progress("render", finished[0] / max(1, len(ids)))
            store.update(jid, progress=round(finished[0] / max(1, len(ids)), 3), stages=tracker.stages)
            return
        if stage.get(cid) != label:
            stage[cid] = label
            store.set_cut(jid, short_key(cid), {"state": "rendering", "stage": label})

    # a clip that was cut already (same folder) is reused instead of cut again
    cut_files = {int(k): v["file"] for k, v in cuts.items() if str(k).isdigit() and v.get("state") == "done" and v.get("file")}
    # a clip edited in the transcript editor is rendered from its edited file, with its edited words
    words_for: dict = {}
    for cid in ids:
        ec = cuts.get(edit_key(cid)) or {}
        wp = edit_words_path(jid, cid)
        if ec.get("state") == "done" and ec.get("file") and os.path.exists(os.path.join(job_dir(jid), ec["file"])) and os.path.exists(wp):
            try:
                import json
                with open(wp, encoding="utf-8") as f:
                    words_for[cid] = json.load(f)
                cut_files[cid] = ec["file"]
            except (OSError, ValueError):
                pass
    tel = Telemetry()
    trim = args.get("trim")
    if trim and trim.get("id") in ids and trim.get("id") not in words_for:
        # new cut points: the clip is cut again from the source with them (a transcript edit keeps its own file)
        result = {**result, "clips": [{**c, "start": float(trim["start"]), "end": float(trim["end"]),
                                       "duration": round(float(trim["end"]) - float(trim["start"]), 2)}
                                      if c["id"] == trim["id"] else c for c in result.get("clips") or []]}
        cut_files.pop(trim["id"], None)
        tracker.add("info", f"Clip {trim['id']} re-cut from {trim['start']:.1f}s to {trim['end']:.1f}s")
    for cid in ids:
        store.set_cut(jid, short_key(cid), {"state": "queued"})
    try:
        out = render_shorts(result, ids, job_dir(jid), args.get("options"), owner=job.get("owner"),
                            watermark=bool(args.get("watermark")), cut_files=cut_files,
                            progress=progress, telemetry=tel, words_for=words_for)
    except Exception as e:  # noqa: BLE001 — e.g. source gone
        out = {i: {"error": _friendly(e)} for i in ids}
    failed = []
    for cid in ids:
        info = out.get(cid) or {"error": "Nothing was rendered."}
        if "error" in info:
            failed.append(cid)
            store.set_cut(jid, short_key(cid), {"state": "failed", "error": info["error"]})
        else:
            store.set_cut(jid, short_key(cid), {"state": "done", "stage": "", **info})
            if info.get("cut_file") and str(cid) not in cuts and cid not in words_for:       # keep the plain cut too (reused next time)
                p = os.path.join(job_dir(jid), info["cut_file"])
                size = round(os.path.getsize(p) / 1e6, 1) if os.path.exists(p) else 0
                store.set_cut(jid, cid, {"state": "done", "file": info["cut_file"], "quality": "1080", "size_mb": size})
    _render_end(jid, tracker, len(failed), len(ids), next(((out.get(i) or {}).get("error") for i in failed), None))
    refund = REFUNDS.pop(jid, None)
    unpaid = [i for i in failed if i not in cut_files]          # a clip already cut stays paid for
    if unpaid and refund:
        try:
            refund([f"clip:{jid}:{i}" for i in unpaid])
        except Exception:  # noqa: BLE001
            pass
    summary = merge_summary(job.get("summary"), tel, "shorts")
    store.update(jid, state="ready", stage="Done" if not failed else f"{len(failed)} shorts failed",
                 progress=1.0, summary=summary)
