"""
studio_routes.py — Phase 5's pages and API (a blueprint): editing, publishing,
the batch queue and the usage dashboard.

  GET  /clipjob/<id>/edit/<clip>                  the transcript editor
  GET  /api/clipjob/<id>/clip/<clip>/words        the clip's words (+ fillers, languages, saved edit)
  POST /api/clipjob/<id>/clip/<clip>/edit         render the edit: {"deleted": [word ids], "broll": bool}
  GET  /api/clipjob/<id>/clip/<clip>/captions.<srt|vtt>?lang=xx&edited=1
  GET  /api/publish                               platforms and the schedule        (Windows app only)
  POST /api/publish                               publish / schedule a finished file
  POST /api/publish/<n>/cancel | retry | reschedule
  GET  /queue, /api/queue                         all jobs and the schedule
  POST /api/batch                                 start many jobs from a list of links
  POST /api/queue/retry-failed, /api/queue/<id>/cancel
  GET  /usage, /api/usage?days=30                 time and cost dashboard
"""

from __future__ import annotations

import time
from datetime import datetime

from flask import Blueprint, Response, abort, jsonify, render_template, request
from werkzeug.datastructures import MultiDict

import clip_routes
from config import DESKTOP
from recaply import batch, publish, usage
from recaply.edit import transcript as tx
from recaply.edit import translate as tr
from recaply.jobs import runner, store
from recaply.jobs.queue import get_queue
from recaply.publish import queue as pq
from recaply.publish.base import PublishError

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("studio", __name__)


def _clip(job: dict, cid: int) -> dict:
    for c in (job.get("result") or {}).get("clips") or []:
        if c["id"] == cid:
            return c
    abort(404)


def _desktop_only():
    if not DESKTOP:
        abort(404)


def _when(raw) -> float:
    """A time the user typed (ISO, local time) or an epoch number; empty = now."""
    if raw in (None, "", "now"):
        return time.time()
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except ValueError:
        raise PublishError("That date and time isn't valid.") from None


# ---------- transcript editor ----------

@bp.route("/api/clipjob/<job_id>/clip/<int:cid>/words")
def clip_words(job_id, cid):
    job = clip_routes._owned(job_id)
    clip = _clip(job, cid)
    if job["state"] in ("queued", "running"):
        return jsonify(error="This job is still working. Try again when it's done."), 409
    from recaply.engine import clip_words as get_words
    words = get_words(job.get("result") or {}, clip)
    if not words:
        return jsonify(error="This clip has no transcript (transcription is off, or no speech was found)."), 422
    numbered = tx.number(words)
    cuts = job.get("cuts") or {}
    return jsonify(words=numbered, fillers=tx.filler_ids(words), duration=clip["duration"],
                   cut=(cuts.get(str(cid)) or {}).get("file"), edit=cuts.get(runner.edit_key(cid)),
                   short=cuts.get(runner.short_key(cid)))


@bp.route("/api/clipjob/<job_id>/clip/<int:cid>/layout")
def clip_layout(job_id, cid):
    """
    Where the facecam, gameplay, face and action are in this clip's cut file,
    so the Director previews every layout the way the export will draw it.
    Measured once per cut file (~10 s) and kept next to it.
    """
    import json
    import os

    import editor
    from recaply.render import reframe
    job = clip_routes._owned(job_id)
    _clip(job, cid)
    cut = (job.get("cuts") or {}).get(str(cid)) or {}
    if cut.get("state") != "done" or not cut.get("file"):
        return jsonify(error="Cut this clip first: the layout is found from its picture."), 409
    path = os.path.join(runner.job_dir(job_id), cut["file"])
    if not os.path.isfile(path):
        return jsonify(error="The cut file is gone. Cut the clip again."), 404
    cache = path + ".layout2.json"                # bump when analyse() returns new fields
    if os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
        try:
            with open(cache, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except (OSError, ValueError):
            pass
    meta = editor.probe(path)
    if not meta.get("video"):
        return jsonify(error="This clip has no picture."), 422
    info = {**reframe.analyse(path, meta), "w": meta["w"], "h": meta["h"]}
    profile = ((job.get("result") or {}).get("profile") or {}).get("key")
    from recaply.profiles.base import get_profile
    try:
        pref = get_profile(profile).reframe if profile else "face"
    except Exception:  # noqa: BLE001
        pref = "face"
    # what "auto" will pick on export (same order as reframe.choose)
    if info["cam"]:
        info["auto"] = "split"
    elif pref in reframe.CAM_LAYOUTS:
        info["auto"] = "action"
    elif pref == "face":
        info["auto"] = "face" if info["face_keys"] else "fit"
    else:
        info["auto"] = pref if pref in reframe.STRATEGIES else "fit"
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(info, f)
    except OSError:
        pass
    return jsonify(info)


@bp.route("/api/clipjob/<job_id>/clip/<int:cid>/edit", methods=["POST"])
def clip_edit(job_id, cid):
    job = clip_routes._owned(job_id)
    _clip(job, cid)
    if job["state"] in ("queued", "running"):
        return jsonify(error="This job is still working. Try again when it's done."), 409
    body = request.get_json(force=True, silent=True) or {}
    try:
        deleted = sorted({int(i) for i in body.get("deleted", [])})[:5000]
    except (TypeError, ValueError):
        return jsonify(error="bad request"), 400
    store.update(job_id, task_args={"clip": cid, "deleted": deleted, "broll": bool(body.get("broll"))})
    store.set_cut(job_id, runner.edit_key(cid), {"state": "queued", "error": None})
    ent = auth.ent() or {}
    get_queue().submit(job_id, "edit", ent.get("priority", 0))
    return jsonify(ok=True)


@bp.route("/api/clipjob/<job_id>/clip/<int:cid>/captions.<ext>")
def clip_captions(job_id, cid, ext):
    if ext not in ("srt", "vtt"):
        abort(404)
    job = clip_routes._owned(job_id)
    clip = _clip(job, cid)
    lang = (request.args.get("lang") or "").lower()
    edited = request.args.get("edited") == "1"
    words = None
    if edited:
        import json
        try:
            with open(runner.edit_words_path(job_id, cid), encoding="utf-8") as f:
                words = json.load(f)
        except (OSError, ValueError):
            words = None
    if words is None:
        from recaply.engine import clip_words as get_words
        words = get_words(job.get("result") or {}, clip)
    if not words:
        return jsonify(error="No transcript for this clip."), 422
    code = "en"
    if lang and lang != "original":
        from recaply.providers.http import ProviderError
        from recaply.providers.llm import get_llm
        try:
            words = tr.translate_words(words, lang, get_llm())
        except ProviderError as e:
            return jsonify(error=str(e)), 422
        code = lang
    text = tr.to_srt(words, code) if ext == "srt" else tr.to_vtt(words, code)
    return Response(text, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="clip{cid}_{code}.{ext}"'})


# ---------- publishing (Windows app: the credentials are the user's own, on their PC) ----------

def _owned_items() -> list[dict]:
    out = []
    for it in pq.list_items(limit=300):
        j = store.get(it["job_id"])
        if j and auth.can_access(j.get("owner")):
            out.append({**it, "job_title": j["title"] or j["input"][:60]})
    return out


@bp.route("/api/publish")
def publish_state():
    _desktop_only()
    return jsonify(platforms=publish.overview(), items=_owned_items(), now=time.time())


@bp.route("/api/publish", methods=["POST"])
def publish_add():
    _desktop_only()
    body = request.get_json(force=True, silent=True) or {}
    job = clip_routes._owned(str(body.get("job") or ""))
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.replace(",", " ").split() if t]
    try:
        n = pq.enqueue(job["id"], str(body.get("file") or ""), str(body.get("platform") or ""), str(body.get("title") or ""),
                       str(body.get("description") or ""), tags, _when(body.get("when")),
                       body.get("privacy") if body.get("privacy") in ("public", "unlisted", "private") else "public")
    except PublishError as e:
        return jsonify(error=str(e)), 400
    if not body.get("when") or body.get("when") == "now":
        pq.start_scheduler()
    return jsonify(ok=True, id=n)


def _item(n: int) -> dict:
    it = pq.get(n)
    j = store.get(it["job_id"]) if it else None
    if not it or not j or not auth.can_access(j.get("owner")):
        abort(404)
    return it


@bp.route("/api/publish/<int:n>/<action>", methods=["POST"])
def publish_action(n, action):
    _desktop_only()
    _item(n)
    body = request.get_json(silent=True) or {}
    if action == "cancel":
        ok = pq.cancel(n)
    elif action == "retry":
        ok = pq.retry(n)
    elif action == "reschedule":
        try:
            ok = pq.reschedule(n, _when(body.get("when")))
        except PublishError as e:
            return jsonify(error=str(e)), 400
    else:
        abort(404)
    return jsonify(ok=ok)


# ---------- batch queue ----------

def _queue_jobs() -> list[dict]:
    out = []
    for j in store.list_jobs(owner=auth.uid(), limit=200):
        out.append({"id": j["id"], "title": j["title"] or j["input"][:90], "input": j["input"][:200], "state": j["state"],
                    "stage": j["stage"], "progress": j["progress"], "created": j["created"], "error": j["error"]})
    return out


@bp.route("/api/queue")
def queue_state():
    jobs = _queue_jobs()
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["state"]] = counts.get(j["state"], 0) + 1
    return jsonify(jobs=jobs, counts=counts, publish=_owned_items() if DESKTOP else [])


@bp.route("/api/batch", methods=["POST"])
def batch_start():
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        return jsonify(error=(ent or {}).get("reason") or "Log in to continue.", upgrade=True), 402
    body = request.get_json(force=True, silent=True) or {}
    raw = body.get("inputs") or ""
    lines = batch.parse_inputs(raw if isinstance(raw, str) else "\n".join(str(x) for x in raw))
    if not lines:
        return jsonify(error="Paste at least one link (one per line)."), 400
    opts = clip_routes._options(MultiDict({k: v for k, v in (body.get("options") or {}).items() if isinstance(v, (str, int))}))
    results = batch.start_batch(lines, opts, owner=auth.uid(), priority=ent["priority"], allow_local=bool(DESKTOP))
    return jsonify(results=results, started=sum(1 for r in results if "job" in r))


@bp.route("/api/queue/retry-failed", methods=["POST"])
def retry_failed():
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        return jsonify(error=(ent or {}).get("reason") or "Log in to continue.", upgrade=True), 402
    return jsonify(ok=True, jobs=batch.retry_failed(owner=auth.uid()))


@bp.route("/api/queue/<job_id>/cancel", methods=["POST"])
def queue_cancel(job_id):
    clip_routes._owned(job_id)
    return jsonify(ok=batch.cancel_queued(job_id))


# ---------- usage ----------

@bp.route("/api/usage")
def usage_state():
    try:
        days = min(365, max(1, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    data = usage.collect(owner=auth.uid(), days=days)
    if DESKTOP:
        data["publish"] = usage.publish_counts()
    return jsonify(data)


def start_publish_scheduler() -> None:
    """Called once at start-up: uploads scheduled items (and Social hub posts) when they come due."""
    try:
        pq.start_scheduler()
    except Exception:  # noqa: BLE001 — never block the app from opening
        pass
