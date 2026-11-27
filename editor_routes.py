"""
editor_routes.py — the Editor's API (a blueprint): open any video for editing, and the media
library its side bin lists.

  POST /api/editor/upload     upload a video; it becomes one editable clip (no analysis)
  POST /api/editor/open       {"path"}: the same for a file on this PC (Windows app only)
  GET  /api/editor/library    every project's clips plus the Editor's own videos, newest first
"""
from __future__ import annotations

import os
import shutil

from flask import Blueprint, jsonify, request

import clip_routes
from config import DESKTOP
from recaply.edit.importer import SCORER
from recaply.ingest.sources import MEDIA_EXTS
from recaply.jobs import store
from recaply.jobs.queue import get_queue
from recaply.jobs.runner import job_dir

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("editor", __name__)


@bp.before_app_request
def editor_body_limit():
    if request.path == "/api/editor/upload":
        request.max_content_length = clip_routes.UPLOAD_MB * 1024 * 1024


def _ent():
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        return None, (jsonify({"error": (ent or {}).get("reason") or "Log in to continue.", "upgrade": True}), 402)
    return ent, None


def _profile(raw) -> str:
    keys = {p["key"] for p in clip_routes.profile_choices()}
    return raw if raw in keys else "talk"


def _create(path: str, title: str, ent: dict, job_id: str | None = None) -> str:
    opts = {"profile": _profile(request.values.get("profile") or (request.get_json(silent=True) or {}).get("profile")),
            "allow_local": True, "mode": "edit"}
    jid = store.create(path, opts, owner=auth.uid(), priority=ent["priority"], title=title[:120] or "Video",
                       **({"job_id": job_id} if job_id else {}))
    get_queue().submit(jid, "import", ent["priority"])
    return jid


@bp.route("/api/editor/upload", methods=["POST"])
def editor_upload():
    ent, err = _ent()
    if err:
        return err
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in MEDIA_EXTS:
        return jsonify({"error": "Drop a video file (MP4, MOV, MKV, WebM…)."}), 400
    jid = store.new_id()
    folder = job_dir(jid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "source" + ext)
    try:
        f.save(path)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"error": "Saving the upload failed."}), 500
    title = os.path.splitext(os.path.basename(f.filename))[0]
    return jsonify({"id": _create(path, title, ent, jid), "clip": 1})


@bp.route("/api/editor/open", methods=["POST"])
def editor_open():
    if not DESKTOP:
        return jsonify({"error": "Upload the file instead."}), 400
    ent, err = _ent()
    if err:
        return err
    path = str((request.get_json(silent=True) or {}).get("path") or "").strip().strip('"')
    if not (os.path.isfile(path) and path.lower().endswith(MEDIA_EXTS)):
        return jsonify({"error": "That file wasn't found, or isn't a video."}), 400
    return jsonify({"id": _create(path, os.path.splitext(os.path.basename(path))[0], ent), "clip": 1})


@bp.route("/api/editor/library")
def editor_library():
    """Projects with their clips (title, length, score and which exports exist), newest first.
    Reads the light columns only; `?ids=a,b` returns just those jobs (polling the busy ones)."""
    ids = request.args.get("ids")
    want = [i for i in ids.split(",") if i][:60] if ids is not None else None
    out = []
    for job in store.list_light(owner=auth.uid(), limit=60, ids=want):
        if job["state"] in ("failed", "cancelled"):
            continue
        result = job.get("result") or {}
        cuts = job.get("cuts") or {}
        clips = [{"id": c["id"], "title": c.get("title") or f"Moment at {c.get('time') or ''}", "start": c.get("start", 0),
                  "duration": c.get("duration", 0), "score": c.get("score"),
                  "exported": (cuts.get(f"s{c['id']}") or {}).get("state") == "done"}
                 for c in sorted(result.get("clips") or [], key=lambda c: c["id"])]
        out.append({"id": job["id"], "title": job["title"] or (job.get("input") or "")[:100], "state": job["state"],
                    "created": job["created"], "editor": result.get("scorer") == SCORER
                    or (job.get("options") or {}).get("mode") == "edit",
                    "duration": (result.get("source") or {}).get("duration"),
                    "thumb": (result.get("source") or {}).get("video_id"),
                    "extractor": (result.get("source") or {}).get("extractor"),
                    "media": clip_routes._media_flags(job), "clips": clips})
    return jsonify({"items": out})
