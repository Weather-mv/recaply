"""
studio_app.py: the glue for the React "AI Studio" (frontend/, built into static/studio/).

  GET  /studio, /studio/...                    the single-page app (any path under it)
  GET  /api/studio/session                     CSRF token, plan and the facts the UI needs at start-up
  GET  /api/studio/projects                    the dashboard list (state, stage, detected content type, best score)
  POST /api/studio/projects                    start a project from a link / local file, JSON in, {id} out
  GET  /api/clipjob/<id>/media/<source|audio>  the video the clips came from (uploads and local files) or the
                                               audio the analysis used (links), for the preview player
  GET  /api/studio/capabilities                what a clip can be exported as (presets, layouts, captions)
  GET  /api/clipjob/<id>/log                   stage stepper, error, job log and pipeline notes

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
from recaply.jobs import stages as job_stages
from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.profiles import all_profiles
from recaply.providers.llm import ai_status, ollama_status  # noqa: F401  (ollama_status: kept for callers)
from recaply import user_settings
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
        "llm": ai_status()["provider"],
        "profiles": [{"key": p.key, "label": p.label, "kind": p.kind, "hidden": p.hidden} for p in all_profiles()],
    })


@bp.route("/api/studio/capabilities")
def capabilities():
    from recaply.edit.translate import LANGUAGES
    return jsonify({**clip_routes.short_choices(), "languages": [{"key": k, "label": v} for k, v in LANGUAGES.items()]})


@bp.route("/api/studio/caption-font/<key>")
def caption_font(key):
    """A caption font file, so the Director's live preview draws captions with the font the export uses."""
    from flask import abort, send_file

    from recaply.render import brand
    if key not in brand.FONT_CHOICES:
        abort(404)
    path = brand.choice_path(key)
    if not path:
        abort(404)
    r = send_file(path, mimetype="font/otf" if path.lower().endswith(".otf") else "font/ttf", max_age=86400)
    return r


@bp.route("/api/studio/system")
def system_stats():
    """CPU / RAM / GPU load for the dashboard widget (this PC; nothing leaves it)."""
    from recaply import sysmon
    return jsonify(sysmon.snapshot())


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
        "beats": sum(c["beats_n"] if "beats_n" in c else len(c.get("beats") or []) for c in clips),
        "duration": (result.get("source") or {}).get("duration"),
        "thumb": (result.get("source") or {}).get("video_id"),
        "extractor": (result.get("source") or {}).get("extractor"),
        "scorer": result.get("scorer"), **job_stages.public(job),
    }


@bp.route("/api/studio/projects")
def projects():
    """The dashboard list, from the light columns only. `?ids=a,b` returns just those (the page polls
    its running jobs that way and reloads the whole list rarely)."""
    ids = request.args.get("ids")
    want = [i for i in ids.split(",") if i][:40] if ids is not None else None
    return jsonify({"projects": [_project(j) for j in store.list_light(owner=auth.uid(), limit=40, ids=want)]})


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
        from recaply.live import links
        # the Live page also accepts watch?v= / youtu.be links to a stream: one cached lookup decides
        status = links.video_status(text) if body.get("live_only") and links.is_video_link(text) else None
        if live_routes.available() and (status in ("live", "upcoming") or live_routes.is_live_link(text)):
            try:
                live_routes.watch_link(text, opts.get("profile") or "gaming")
            except Exception as e:  # noqa: BLE001 — shown to the user as is
                return jsonify({"error": f"Couldn't start live clipping: {e}"}), 400
            return jsonify({"live": True, "redirect": "/studio/live"})
        if body.get("live_only") and status == "ended":
            # the Live page, with a broadcast that has already ended: it's a video now, so clip it as one
            jid = clip_routes._start(text, opts, title="")
            return jsonify({"id": jid, "ended": True, "message": "That stream has ended, so Clipforge is clipping it as a video."})
        if body.get("live_only"):                    # the Live page: never start a normal project from a link that isn't live
            return jsonify({"error": "That link isn't live right now. Paste it on the Projects page to clip it as a video, "
                                     "or use a channel link (twitch.tv/name, kick.com/name) to wait for it to go live."}), 400
    elif body.get("live_only"):
        return jsonify({"error": "Paste a live link."}), 400
    elif not DESKTOP:
        return jsonify({"error": "Paste a link, or upload the file."}), 400
    elif not (os.path.isfile(text) and text.lower().endswith(MEDIA_EXTS)):
        return jsonify({"error": "That file wasn't found, or isn't a video."}), 400
    jid = clip_routes._start(text, opts, title="" if looks_like_url(text) else os.path.basename(text))
    return jsonify({"id": jid})


def _can_configure() -> bool:
    """The AI keys are this PC's: only the owner at this PC may read or change them (never a visitor of a hosted copy)."""
    from config import PUBLIC_URL
    return bool(DESKTOP) or (not PUBLIC_URL and (request.remote_addr or "") in ("127.0.0.1", "::1"))


@bp.route("/api/studio/settings", methods=["GET", "POST"])
def user_settings_api():
    """The Settings page: which AI keys are set (never their values), the provider order, and what will be used."""
    if not _can_configure():
        return jsonify({"error": "Settings can only be changed on the PC that runs Clipforge."}), 403
    problems = {}
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        problems = user_settings.update(settings, body.get("set") or {})
    return jsonify({"settings": user_settings.status(), "problems": problems, "ai": ai_status(),
                    "providers": ["groq", "gemini", "anthropic", "openai", "ollama"]})


@bp.route("/api/studio/storage", methods=["GET", "POST"])
def storage_api():
    """Disk use and the clean-up setting. GET ?days=N previews what a clean-up would delete;
    POST {"days"} saves how many days finished projects are kept (0 = forever)."""
    from recaply import cleanup
    if not _can_configure():
        return jsonify({"error": "Storage can only be managed on the PC that runs Clipforge."}), 403
    if request.method == "POST":
        try:
            cleanup.set_days((request.get_json(force=True, silent=True) or {}).get("days", 0))
        except (TypeError, ValueError) as e:
            return jsonify({"error": str(e) if isinstance(e, ValueError) else "days must be a number."}), 400
    days = cleanup.get_days()
    try:
        ask = int(request.args.get("days", days or 30))
    except ValueError:
        ask = 30
    return jsonify({"days": days, "choices": list(cleanup.CHOICES), "usage": cleanup.usage(),
                    "preview": {"days": ask, **cleanup.preview(None, ask, everyone=True)} if ask > 0 else None})


@bp.route("/api/studio/storage/clean", methods=["POST"])
def storage_clean():
    """Delete finished projects older than {"days"} (and caches nothing uses) right now."""
    from recaply import cleanup
    if not _can_configure():
        return jsonify({"error": "Storage can only be managed on the PC that runs Clipforge."}), 403
    try:
        days = int((request.get_json(force=True, silent=True) or {}).get("days", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "days must be a number."}), 400
    if days < 0:
        return jsonify({"error": "days can't be negative."}), 400
    freed = cleanup.clean(None, days, everyone=True)
    return jsonify({"ok": True, "freed": freed, "usage": cleanup.usage()})


@bp.route("/api/studio/storage/legacy", methods=["POST"])
def storage_clean_legacy():
    """Delete the previous version's files (old recaps, the classic editor's files)."""
    from recaply import cleanup
    if not _can_configure():
        return jsonify({"error": "Storage can only be managed on the PC that runs Clipforge."}), 403
    return jsonify({"ok": True, "freed": cleanup.clean_legacy(), "usage": cleanup.usage()})


@bp.route("/api/studio/settings/test", methods=["POST"])
def user_settings_test():
    """One tiny request to a provider, so the user knows its key works. Says why when it doesn't."""
    if not _can_configure():
        return jsonify({"error": "Settings can only be changed on the PC that runs Clipforge."}), 403
    name = str((request.get_json(force=True, silent=True) or {}).get("provider") or "").lower()
    if name == "ollama":
        st = ollama_status()
        ok = bool(st["running"] and st["model_ready"])
        return jsonify({"ok": ok, "message": f"Ollama is running with {st['model']}." if ok else st["hint"]})
    from recaply.providers.http import ProviderError
    from recaply.providers.llm import get_llm
    llm = get_llm(name) if name in ("groq", "gemini", "anthropic", "openai") else None
    if llm is None:
        return jsonify({"ok": False, "message": "Save an API key for this provider first."})
    try:
        data, _usage = llm.complete_json("You answer with JSON only.", 'Reply with {"ok": true}.', max_tokens=40)
    except (ProviderError, OSError, ValueError) as e:
        return jsonify({"ok": False, "message": " ".join(str(e).split())[:200]})
    return jsonify({"ok": True, "message": f"{name} answered ({llm.model})."})


@bp.route("/api/clipjob/<job_id>/log")
def job_log(job_id):
    """Where a job is or where it failed: the stage stepper, the error, the job log and the pipeline's notes."""
    job = clip_routes._owned(job_id)
    return jsonify({"id": job["id"], "state": job["state"], "stage": job.get("stage") or "",
                    "error": job.get("error"), **job_stages.public(job),
                    "log": job.get("log") if isinstance(job.get("log"), list) else [],
                    "notes": (job.get("summary") or {}).get("notes") or []})


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
