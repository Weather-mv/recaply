"""
clip_routes.py — the "any video" clip finder's pages and API (a blueprint).

  POST /clips/start                 start from a link (or, in the Windows app, a file path)
  POST /api/clips/upload            start from an uploaded file
  GET  /clipjob/<id>                the job page (live progress, ranked clips)
  GET  /api/clipjob/<id>            job as JSON
  GET  /api/clipjob/<id>/events     progress stream (Server-Sent Events)
  POST /api/clipjob/<id>/cut        cut the chosen clips (charged to the plan, once per clip)
  POST /api/clipjob/<id>/shorts     render the chosen clips as finished shorts (same charge as a cut)
  GET  /api/brand                   the account's brand kit (+ choices);  POST saves it
  POST /api/brand/<logo|font|intro|outro>   upload a brand file;  DELETE removes it
  POST /api/clipjob/<id>/retry      run the analysis again (cached stages are reused)
  DELETE /api/clipjob/<id>          delete the job and its clips
  GET  /clipjob/<id>/file/<name>    a cut clip

Work runs on the job queue (recaply.jobs): threads in the app by default,
RQ workers on a server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import asdict

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, send_from_directory

from common import allowed_url
from config import DESKTOP, env_int
from recaply.ingest.sources import MEDIA_EXTS, looks_like_url
from recaply.jobs import runner, stages, store
from recaply.jobs.queue import get_queue, queue_info
from recaply.jobs.runner import job_dir
from recaply.profiles import all_profiles
from recaply.providers.llm import ai_status
from recaply.settings import settings
from recaply.render import brand as brandkit
from recaply.render import presets, reframe, subtitles
from recaply.render.shorts import ShortOptions

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("clips", __name__)
JOB_ID = re.compile(r"^[0-9a-f]{10}$")
FILE_NAME = re.compile(r"^[\w.\-]{1,160}\.(mp4|ass)$")
UPLOAD_MB = max(1, env_int("RECAPLY_UPLOAD_MB", 2048))
LENGTHS = {"auto": (None, None), "short": (15, 30), "medium": (30, 60), "long": (60, 90)}
QUALITIES = ("1080", "720", "best")
SSE_MAX_SEC = 600


@bp.before_app_request
def clip_body_limits():
    if request.path == "/api/clips/upload":
        request.max_content_length = UPLOAD_MB * 1024 * 1024


def profile_choices() -> list[dict]:
    """Profiles offered in the UI (VCT rounds runs the classic round scan)."""
    return [{"key": p.key, "label": p.label} for p in all_profiles()]


def _options(form) -> dict:
    keys = {p["key"] for p in profile_choices()}
    profile = form.get("profile", "auto")
    if profile not in keys:
        profile = "auto"
    try:
        count = min(30, max(1, int(form.get("count", 10))))
    except (TypeError, ValueError):
        count = 10
    lo, hi = LENGTHS.get(form.get("length", "auto"), (None, None))
    lang = (form.get("language") or "").strip().lower()[:5] or None
    if lang and not re.fullmatch(r"[a-z]{2,3}", lang):
        lang = None
    return {"profile": profile, "count": count, "min_len": lo, "max_len": hi, "language": lang,
            "allow_local": bool(DESKTOP)}


def short_choices() -> dict:
    """Export presets, layouts and caption styles the UI offers for shorts."""
    layouts = {"auto": "Auto", "face": "Follow the speaker", "split": "Cam on top, gameplay below",
               "action": "Gameplay crop", "center": "Centre crop", "fit": "Whole frame, blurred sides"}
    from recaply.render import bgm
    return {"exports": [{"key": k, "label": v["label"]} for k, v in presets.EXPORTS.items()],
            "music": bgm.tracks(),
            "layouts": [{"key": k, "label": layouts.get(k, k)} for k in reframe.STRATEGIES],
            "captions": [{"key": "none", "label": "No captions"}, {"key": "brand", "label": "Brand kit style"},
                         *({"key": k, "label": v["label"]} for k, v in subtitles.PRESETS.items())]}


def _owned(job_id: str) -> dict:
    """The signed-in user's job, or a 404 (also for other people's jobs and bad ids)."""
    if not JOB_ID.match(job_id or ""):
        abort(404)
    job = store.get(job_id)
    if not job or not auth.can_access(job.get("owner")):
        abort(404)
    return job


def _media_flags(job: dict) -> dict:
    """Which previews exist (source video / analysis audio); the studio's player picks the best."""
    try:
        import studio_app
        return studio_app.media_flags(job)
    except Exception:  # noqa: BLE001 — a missing preview must never break the job view
        return {"source": False, "audio": False}


def view(job: dict) -> dict:
    """What the page needs (no local paths)."""
    result = dict(job.get("result") or {})
    result.pop("source_path", None)
    return {
        "id": job["id"], "state": job["state"], "task": job.get("task"), "stage": job["stage"],
        "progress": job["progress"], "title": job["title"] or job["input"][:120],
        "error": job.get("error"), "created": job["created"], "result": result or None,
        "cuts": job.get("cuts") or {}, "summary": job.get("summary") or {},
        "options": job.get("options") or {}, "version": job["version"],
        "media": _media_flags(job), **stages.public(job),
    }


def _start(input_text: str, opts: dict, title: str = "") -> str:
    ent = auth.ent()
    jid = store.create(input_text, opts, owner=auth.uid(), priority=ent["priority"] if ent else 0, title=title)
    get_queue().submit(jid, "analyze", ent["priority"] if ent else 0)
    return jid


@bp.route("/clips/start", methods=["POST"])
def clips_start():
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        flash((ent or {}).get("reason") or "Log in to continue.", "error")
        return redirect("/app")
    text = (request.form.get("url") or "").strip().strip('"')
    opts = _options(request.form)
    if not text:
        return redirect("/app")
    if looks_like_url(text):
        if not allowed_url(text):
            flash("Only YouTube, Twitch and Kick links are supported.", "error")
            return redirect("/app")
    elif not DESKTOP:
        flash("Paste a link, or upload the file.", "error")
        return redirect("/app")
    elif not (os.path.isfile(text) and text.lower().endswith(MEDIA_EXTS)):
        flash("That file wasn't found, or isn't a video.", "error")
        return redirect("/app")
    if looks_like_url(text):
        import live_routes
        if live_routes.available() and live_routes.is_live_link(text):
            try:
                live_routes.watch_link(text, opts.get("profile") or "gaming")
            except Exception as e:  # noqa: BLE001 — shown to the user as is
                flash(f"Couldn't start live clipping: {e}", "error")
                return redirect("/app")
            flash("That's a live stream, so Clipforge is watching it and will clip the big moments as they happen.", "ok")
            return redirect("/live")
    jid = _start(text, opts, title=os.path.basename(text) if not looks_like_url(text) else "")
    return redirect(f"/clipjob/{jid}")


@bp.route("/api/clips/upload", methods=["POST"])
def clips_upload():
    ent = auth.ent()
    if not ent or not ent["can_create"]:          # checked before the upload is read
        return jsonify({"error": (ent or {}).get("reason") or "Log in to continue.", "upgrade": True}), 402
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in MEDIA_EXTS:
        return jsonify({"error": "Upload a video or audio file (MP4, MOV, MKV, WebM, MP3…)."}), 400
    jid = store.new_id()
    folder = job_dir(jid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "source" + ext)
    try:
        f.save(path)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"error": "Saving the upload failed."}), 500
    opts = {**_options(request.form), "allow_local": True}
    title = os.path.splitext(os.path.basename(f.filename))[0][:120] or "Upload"
    store.create(path, opts, owner=auth.uid(), priority=ent["priority"], title=title, job_id=jid)
    get_queue().submit(jid, "analyze", ent["priority"])
    return jsonify({"id": jid})


@bp.route("/api/clips/health")
def clips_health():
    """Is the local AI ready and is the job queue up? (the dashboard shows the hint when not)"""
    st = ai_status()                 # "llm" is the provider that will be asked first; "order" the whole fallback chain
    return jsonify({"llm": st["provider"], "llm_order": st["order"], "ollama": st["ollama"], "queue": queue_info()})


@bp.route("/api/clipjob/<job_id>")
def clipjob_get(job_id):
    return jsonify(view(_owned(job_id)))


@bp.route("/api/clipjob/<job_id>/events")
def clipjob_events(job_id):
    _owned(job_id)

    def stream():
        last, t0, beat = None, time.time(), time.time()
        yield "retry: 2000\n\n"
        while time.time() - t0 < SSE_MAX_SEC:
            v = store.version(job_id)
            if v is None:
                yield "event: gone\ndata: {}\n\n"
                return
            if v != last:
                last = v
                job = store.get(job_id)
                if job is None:
                    return
                yield f"data: {json.dumps(view(job))}\n\n"
                beat = time.time()
            elif time.time() - beat > 15:
                yield ": keep-alive\n\n"
                beat = time.time()
            time.sleep(0.5)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/api/clipjob/<job_id>/cut", methods=["POST"])
def clipjob_cut(job_id):
    job = _owned(job_id)
    if job["state"] in ("queued", "running"):
        return jsonify({"error": "This job is still working. Try again when it's done."}), 409
    body = request.get_json(force=True, silent=True) or {}
    quality = body.get("quality") if body.get("quality") in QUALITIES else "1080"
    known = {c["id"] for c in ((job.get("result") or {}).get("clips") or [])}
    try:
        ids = [int(i) for i in body.get("ids", [])][:50]
    except (TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400
    ids = [i for i in dict.fromkeys(ids) if i in known]
    if not ids:
        return jsonify({"error": "Pick at least one clip."}), 400
    # Each clip counts once against the plan; cutting it again (other quality) is free.
    ok, info = auth.charge([f"clip:{job_id}:{i}" for i in ids])
    if not ok:
        from common import charge_failed
        return charge_failed(info)
    for i in ids:
        store.set_cut(job_id, i, {"state": "queued"})
    store.update(job_id, task_args={"ids": ids, "quality": quality})
    ent = auth.ent() or {}
    get_queue().submit(job_id, "cut", ent.get("priority", 0), refund=auth.refunder())
    return jsonify({"ok": True, "queued": len(ids)})


@bp.route("/api/clipjob/<job_id>/shorts", methods=["POST"])
def clipjob_shorts(job_id):
    job = _owned(job_id)
    if job["state"] in ("queued", "running"):
        return jsonify({"error": "This job is still working. Try again when it's done."}), 409
    body = request.get_json(force=True, silent=True) or {}
    known = {c["id"] for c in ((job.get("result") or {}).get("clips") or [])}
    try:
        ids = [int(i) for i in body.get("ids", [])][:20]
    except (TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400
    ids = [i for i in dict.fromkeys(ids) if i in known]
    if not ids:
        return jsonify({"error": "Pick at least one clip."}), 400
    opts = asdict(ShortOptions.from_dict(body.get("options")))      # unknown values fall back to defaults
    trim = None
    if body.get("trim") is not None:                                 # new cut points for a single clip
        total = float(((job.get("result") or {}).get("source") or {}).get("duration") or 0)
        try:
            ts, te = float(body["trim"]["start"]), float(body["trim"]["end"])
        except (TypeError, ValueError, KeyError):
            return jsonify({"error": "bad request"}), 400
        if len(ids) != 1 or not (0 <= ts < te) or te - ts < 2 or te - ts > 600 or (total and te > total + 0.5):
            return jsonify({"error": "Pick a start and an end between 2 and 600 seconds apart, inside the video."}), 400
        trim = {"id": ids[0], "start": ts, "end": te}
    # a short is its clip, finished: it counts once against the plan, like the cut
    ok, info = auth.charge([f"clip:{job_id}:{i}" for i in ids])
    if not ok:
        from common import charge_failed
        return charge_failed(info)
    ent = auth.ent() or {}
    for i in ids:
        store.set_cut(job_id, runner.short_key(i), {"state": "queued"})
    store.update(job_id, task_args={"ids": ids, "options": opts, "watermark": bool(ent.get("watermark")), "trim": trim})
    get_queue().submit(job_id, "shorts", ent.get("priority", 0), refund=auth.refunder())
    return jsonify({"ok": True, "queued": len(ids), "watermark": bool(ent.get("watermark"))})


@bp.route("/api/clipjob/<job_id>/preview", methods=["POST"])
def clipjob_preview(job_id):
    """A free 5-second, low-resolution preview of one clip with the chosen crop and captions (optionally from
    other start / end times). Nothing is charged; the result appears as cuts["p<clip id>"] (file, state)."""
    job = _owned(job_id)
    if job["state"] in ("queued", "running"):
        return jsonify({"error": "This job is still working. Try again when it's done."}), 409
    body = request.get_json(force=True, silent=True) or {}
    clips = {c["id"]: c for c in ((job.get("result") or {}).get("clips") or [])}
    try:
        cid = int(body.get("id"))
        start = float(body["start"]) if body.get("start") is not None else None
        end = float(body["end"]) if body.get("end") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400
    if cid not in clips:
        return jsonify({"error": "That clip isn't in this project."}), 404
    opts = asdict(ShortOptions.from_dict(body.get("options")))
    store.set_cut(job_id, runner.preview_key(cid), {"state": "queued", "error": None})
    store.update(job_id, task_args={"id": cid, "options": opts, "start": start, "end": end})
    ent = auth.ent() or {}
    get_queue().submit(job_id, "preview", ent.get("priority", 0))
    return jsonify({"ok": True})


@bp.route("/api/brand", methods=["GET", "POST"])
def brand_api():
    ent = auth.ent()
    if not ent:
        return jsonify({"error": "Log in to continue."}), 401
    owner = auth.uid()
    if request.method == "POST":
        raw = request.get_json(force=True, silent=True) or {}
        kit = brandkit.load(owner)
        for k in ("logo_pos", "accent", "text_color", "highlight_color", "keyword_color", "caption_style", "font"):
            if isinstance(raw.get(k), str):
                setattr(kit, k, raw[k])
        for k in ("logo_size", "logo_opacity"):
            try:
                if k in raw:
                    setattr(kit, k, float(raw[k]))
            except (TypeError, ValueError):
                pass
        brandkit.save(owner, kit)
    return jsonify({"kit": asdict(brandkit.load(owner)), "fonts": brandkit.available_fonts(),
                    "positions": brandkit.POSITIONS, "captions": short_choices()["captions"][2:]})


@bp.route("/api/brand/<which>", methods=["POST", "DELETE"])
def brand_file(which):
    if which not in brandkit.MEDIA_FILES:
        abort(404)
    if not auth.ent():
        return jsonify({"error": "Log in to continue."}), 401
    owner = auth.uid()
    if request.method == "DELETE":
        brandkit.remove(owner, which)
        return jsonify({"ok": True})
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    allowed = {"logo": (".png", ".jpg", ".jpeg", ".webp"), "font": (".ttf", ".otf"),
               "intro": (".mp4", ".mov", ".webm", ".mkv"), "outro": (".mp4", ".mov", ".webm", ".mkv")}[which]
    if ext not in allowed:
        return jsonify({"error": f"Upload a {' / '.join(allowed)} file."}), 400
    tmp = os.path.join(brandkit.brand_dir(owner), f"_upload{ext}")
    try:
        f.save(tmp)
        {"logo": brandkit.save_logo, "font": brandkit.save_font}.get(
            which, lambda o, p: brandkit.save_clip(o, which, p))(owner, tmp)
    except Exception as e:  # noqa: BLE001 — not an image / font / video, too long...
        return jsonify({"error": str(e)[:200] or "That file couldn't be used."}), 400
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return jsonify({"ok": True})


@bp.route("/api/clipjob/<job_id>/retry", methods=["POST"])
def clipjob_retry(job_id):
    job = _owned(job_id)
    if job["state"] in ("queued", "running"):
        return jsonify({"error": "Already running."}), 409
    ent = auth.ent()
    if not ent or not ent["can_create"]:
        return jsonify({"error": (ent or {}).get("reason") or "Log in to continue.", "upgrade": True}), 402
    get_queue().submit(job_id, "analyze", ent["priority"])
    return jsonify({"ok": True})


@bp.route("/api/clipjob/<job_id>", methods=["DELETE"])
def clipjob_delete(job_id):
    job = _owned(job_id)
    if job["state"] in ("queued", "running"):
        return jsonify({"error": "Wait for the job to finish first."}), 409
    store.delete(job_id)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    return jsonify({"ok": True})


@bp.route("/clipjob/<job_id>/file/<name>")
def clipjob_file(job_id, name):
    job = _owned(job_id)
    files = {f for c in (job.get("cuts") or {}).values() for f in (c.get("file"), c.get("ass")) if f}
    if not FILE_NAME.match(name or "") or name not in files:
        abort(404)
    return send_from_directory(job_dir(job_id), name, as_attachment=request.args.get("dl") == "1",
                               conditional=True)


def recent_jobs(limit: int = 12) -> list[dict]:
    """The user's latest clip jobs for the dashboard."""
    out = []
    for j in store.list_jobs(owner=auth.uid(), limit=limit):
        out.append({"id": j["id"], "title": j["title"] or j["input"][:100], "state": j["state"],
                    "created": j["created"], "kind": "clips"})
    return out


def resume_jobs() -> int:
    """Re-queue clip jobs interrupted by a restart (called once at start-up)."""
    return get_queue().resume()
