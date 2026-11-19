"""
studio_app.py: the glue for the React "AI Studio" (frontend/, built into static/studio/).

  GET  /studio, /studio/...                    the single-page app (any path under it)
  GET  /api/studio/session                     CSRF token, plan and the facts the UI needs at start-up
  GET  /api/studio/projects                    the dashboard list (state, stage, detected content type, best score)
  POST /api/studio/projects                    start a project from a link / local file, JSON in, {id} out
  GET  /api/clipjob/<id>/media/<source|audio>  the video the clips came from (uploads and local files) or the
                                               audio the analysis used (links), for the preview player
  GET  /api/studio/capabilities                what a clip can be exported as (presets, layouts, captions)

Everything else the studio does uses the clip finder's existing API (clip_routes.py, studio_routes.py).
"""

from __future__ import annotations

import mimetypes
import os

from flask import Blueprint, abort, jsonify, request, send_file

import clip_routes
from common import allowed_url
from config import BASE_DIR, DESKTOP
from recaply.ingest.sources import MEDIA_EXTS, looks_like_url
from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.profiles import all_profiles
from recaply.providers.llm import ollama_status
from recaply.settings import settings

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("studio_app", __name__)
DIST = os.path.join(BASE_DIR, "static", "studio")


# ---------- the app itself ----------

@bp.route("/studio", defaults={"rest": ""}, strict_slashes=False)
@bp.route("/studio/<path:rest>")
def spa(rest):
    index = os.path.join(DIST, "index.html")
    if not os.path.isfile(index):
        return ("The studio hasn't been built yet. In the frontend folder run: npm install && npm run build",
                503, {"Content-Type": "text/plain; charset=utf-8"})
    with open(index, encoding="utf-8") as f:
        html = f.read().replace("__CSRF__", auth.csrf_token())
    return html, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


# ---------- small JSON API ----------

@bp.route("/api/studio/session")
def session_info():
    user = auth.current_user() or {}
    ent = auth.ent() or {}
    return jsonify({
        "csrf": auth.csrf_token(), "desktop": bool(DESKTOP),
        "user": {"name": user.get("name") or (user.get("email") or "").split("@")[0], "email": user.get("email")},
        "plan": {"name": ent.get("name"), "left": ent.get("left"), "limit": ent.get("limit"),
                 "can_create": bool(ent.get("can_create", True)), "reason": ent.get("reason")},
        "llm": settings.llm_provider,
        "profiles": [{"key": p.key, "label": p.label, "kind": p.kind, "hidden": p.hidden} for p in all_profiles()],
    })


@bp.route("/api/studio/capabilities")
def capabilities():
    from recaply.edit.translate import LANGUAGES
    return jsonify({**clip_routes.short_choices(), "languages": [{"key": k, "label": v} for k, v in LANGUAGES.items()]})


def _project(job: dict) -> dict:
    """One dashboard row. `profile` is None until the analysis has decided what the video is."""
    result = job.get("result") or {}
    clips = result.get("clips") or []
    prof = result.get("profile") or {}
    return {
        "id": job["id"], "title": job["title"] or (job.get("input") or "")[:100], "state": job["state"],
        "stage": job.get("stage") or "", "progress": job.get("progress") or 0.0, "created": job["created"],
        "error": job.get("error"),
        "profile": {"key": prof.get("key"), "label": prof.get("label"), "reason": prof.get("reason")} if prof else None,
        "clips": len(clips), "best": max((c.get("score") or 0 for c in clips), default=None) if clips else None,
        "beats": sum(len(c.get("beats") or []) for c in clips),
        "duration": (result.get("source") or {}).get("duration"),
        "thumb": (result.get("source") or {}).get("video_id"),
        "extractor": (result.get("source") or {}).get("extractor"),
    }


@bp.route("/api/studio/projects")
def projects():
    rows = []
    for j in store.list_jobs(owner=auth.uid(), limit=40):
        full = store.get(j["id"])        # list_jobs omits the (big) result column
        if full:
            rows.append(_project(full))
    return jsonify({"projects": rows})


@bp.route("/api/studio/projects", methods=["POST"])
def create_project():
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        return jsonify({"error": (ent or {}).get("reason") or "Log in to continue.", "upgrade": True}), 402
    body = request.get_json(force=True, silent=True) or {}
    text = str(body.get("url") or "").strip().strip('"')
    if not text:
        return jsonify({"error": "Paste a link first."}), 400
    opts = clip_routes._options({k: body.get(k) for k in ("profile", "count", "length", "language") if body.get(k) is not None})
    if looks_like_url(text):
        if not allowed_url(text):
            return jsonify({"error": "Only YouTube, Twitch and Kick links are supported."}), 400
        import live_routes
        if live_routes.available() and live_routes.is_live_link(text):
            try:
                live_routes.watch_link(text, opts.get("profile") or "gaming")
            except Exception as e:  # noqa: BLE001 — shown to the user as is
                return jsonify({"error": f"Couldn't start live clipping: {e}"}), 400
            return jsonify({"live": True, "redirect": "/live"})
    elif not DESKTOP:
        return jsonify({"error": "Paste a link, or upload the file."}), 400
    elif not (os.path.isfile(text) and text.lower().endswith(MEDIA_EXTS)):
        return jsonify({"error": "That file wasn't found, or isn't a video."}), 400
    jid = clip_routes._start(text, opts, title="" if looks_like_url(text) else os.path.basename(text))
    return jsonify({"id": jid})


# ---------- media for the preview player ----------

def _media_path(job: dict, kind: str) -> str | None:
    result = job.get("result") or {}
    src = result.get("source") or {}
    if kind == "source":
        p = None
        if src.get("kind") == "file":
            p = result.get("source_path") or job.get("input")
        if p and os.path.isfile(p) and p.lower().endswith(MEDIA_EXTS):
            return p
        return None
    if kind == "audio" and src.get("key"):
        from recaply.cache import StageCache
        folder = StageCache(src["key"]).dir
        if os.path.isdir(folder):
            for n in sorted(os.listdir(folder)):
                if n.startswith("audio.") and not n.endswith((".part", ".ytdl", ".tmp")):
                    return os.path.join(folder, n)
    return None


@bp.route("/api/clipjob/<job_id>/media/<kind>")
def media(job_id, kind):
    if kind not in ("source", "audio"):
        abort(404)
    job = clip_routes._owned(job_id)
    path = _media_path(job, kind)
    if not path:
        abort(404)
    return send_file(path, mimetype=mimetypes.guess_type(path)[0] or "application/octet-stream", conditional=True)


def media_flags(job: dict) -> dict:
    """Which previews exist for a job (shown to the player so it picks the best one)."""
    return {"source": _media_path(job, "source") is not None, "audio": _media_path(job, "audio") is not None}
