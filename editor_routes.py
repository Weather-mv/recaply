"""
editor_routes.py — the video editor's pages and API (a Flask blueprint).

A project is a folder in <data dir>/_editor/<id>/ with project.json, the
source video (source.mp4, always browser-playable H.264/AAC), uploaded
pictures (assets/) and the latest export, mirrored to the storage bucket
(storage.py) when one is set up. The page edits the document and saves it
here; exports render on the server with editor.py. Every project belongs to
the account that made it.
"""

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_from_directory

import captions
import editor
import reels
import storage
from common import (JOB_ID, JOBS, RENDER_SLOTS, allowed_url, charge_failed, ensure_mp4, fmt_dur, fmt_hms,
                    friendly_error)
from config import ALLOWED_HOSTS, BASE_DIR, CLIPS_DIR, DESKTOP, env_int
from live import LiveStream
from pipeline import (MP4_AUDIO_CODECS, MP4_VIDEO_CODECS, convert_to_mp4, download_clip_section,
                      resolve_source)

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("editor", __name__)

EDIT_DIR = os.path.join(CLIPS_DIR, "_editor")
PROJ_ID = re.compile(r"^[0-9a-f]{10}$")
PROJECTS = {}
PROJ_LOCK = threading.Lock()
UPLOAD_MB = max(1, env_int("RECAPLY_UPLOAD_MB", 2048))
IMPORT_MAX_SEC = 600                  # longest piece taken from a link
VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".flv")
IMAGE_MAX_MB = 20


@bp.before_app_request
def editor_body_limits():
    """Uploads and editor documents are bigger than everything else's tiny forms."""
    if request.path == "/api/editor/upload":
        request.max_content_length = UPLOAD_MB * 1024 * 1024
    elif request.path.startswith("/api/editor/") and request.path.endswith("/asset"):
        request.max_content_length = IMAGE_MAX_MB * 1024 * 1024
    elif request.path.startswith("/api/editor/"):
        request.max_content_length = 2 * 1024 * 1024


def proj_dir(pid):
    return os.path.join(EDIT_DIR, pid)


def save_project(pid):
    with PROJ_LOCK:
        data = json.dumps(PROJECTS[pid])
    path = os.path.join(proj_dir(pid), "project.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    storage.put_bytes(path, data)


def load_projects():
    if not os.path.isdir(EDIT_DIR):
        return
    for pid in os.listdir(EDIT_DIR):
        path = os.path.join(EDIT_DIR, pid, "project.json")
        if not PROJ_ID.match(pid) or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if p.get("state") == "importing":
            p.update(state="failed", error="Server restarted while importing.")
        if (p.get("render") or {}).get("state") in ("queued", "rendering"):
            p["render"].update(state="failed", error="Server restarted.")
        p.pop("detecting", None)
        if p.get("doc") and p.get("meta"):
            try:        # older documents: blurred background setting -> layer, new fields filled in
                p["doc"] = editor.clean_doc(p["doc"], p["meta"], os.path.join(EDIT_DIR, pid, "assets"))
            except Exception:  # noqa: BLE001 — keep it as it was
                traceback.print_exc()
        if (p.get("asr") or {}).get("state") == "running":
            p["asr"].update(state="failed", error="Server restarted while transcribing.")
        PROJECTS[pid] = p


def get_project(pid):
    """The signed-in user's project for a URL segment, or a 404 (also for other people's projects)."""
    if not PROJ_ID.match(pid or "") or pid not in PROJECTS or not auth.can_access(PROJECTS[pid].get("owner")):
        abort(404)
    return PROJECTS[pid]


def delete_user_projects(uid):
    with PROJ_LOCK:
        mine = [k for k, p in PROJECTS.items() if p.get("owner") == uid]
        for k in mine:
            PROJECTS.pop(k, None)
    for k in mine:
        storage.delete_tree(proj_dir(k))


auth.ON_DELETE.append(delete_user_projects)


def project_busy(p):
    return p.get("state") == "importing" or (p.get("render") or {}).get("state") in ("queued", "rendering")


def project_view(p, full=False):
    """What the pages need (the document and detection only for the editor itself)."""
    meta = p.get("meta") or {}
    doc = p.get("doc") or {}
    out = {k: p.get(k) for k in ("id", "name", "created", "updated", "state", "stage", "progress", "error", "origin")}
    out.update(duration=meta.get("duration"), format=doc.get("format"),
               length=round(editor.duration_of(doc), 2) if doc else None,
               render={k: (p.get("render") or {}).get(k) for k in
                       ("state", "progress", "error", "size_mb", "seconds", "file", "format", "size", "stage", "eta")})
    if full:
        out.update(meta=meta, doc=doc, detect=p.get("detect"), detecting=bool(p.get("detecting")),
                   want_reaction=bool(p.get("want_reaction")), asr=p.get("asr") or {},
                   asr_available=captions.available())
    return out


def new_project(name, origin, want_reaction=False):
    pid = uuid.uuid4().hex[:10]
    os.makedirs(os.path.join(proj_dir(pid), "assets"), exist_ok=True)
    now = time.time()
    with PROJ_LOCK:
        PROJECTS[pid] = {"id": pid, "name": (name or "Untitled")[:80], "created": now, "updated": now,
                         "origin": origin, "state": "importing", "stage": "Starting", "progress": None,
                         "error": None, "meta": None, "doc": None, "render": {},
                         "want_reaction": bool(want_reaction), "owner": auth.uid()}
    save_project(pid)
    return pid


def set_project(pid, **kw):
    with PROJ_LOCK:
        PROJECTS[pid].update(kw)


def finish_import(pid, path):
    """Make `path` the project's browser-playable source.mp4, then set up the first document."""
    d = proj_dir(pid)
    dst = os.path.join(d, "source.mp4")
    set_project(pid, stage="Checking the video", progress=None)
    meta = editor.probe(path)
    if meta["video"] not in MP4_VIDEO_CODECS or (meta["audio"] and meta["audio"] not in MP4_AUDIO_CODECS) \
            or not path.lower().endswith(".mp4"):
        set_project(pid, stage="Converting to MP4", progress=0.0)
        convert_to_mp4(path, dst + ".conv.mp4", progress=lambda f: set_project(pid, progress=round(f, 3)))
        os.replace(dst + ".conv.mp4", dst)
        if os.path.abspath(path) != os.path.abspath(dst) and os.path.exists(path):
            os.remove(path)
    elif os.path.abspath(path) != os.path.abspath(dst):
        os.replace(path, dst)
    meta = editor.probe(dst)
    if meta["duration"] <= 0.2:
        raise RuntimeError("The video is too short to edit.")
    editor.poster(dst, os.path.join(d, "poster.jpg"), min(meta["duration"] * 0.3, 20))
    storage.upload(dst)
    storage.upload(os.path.join(d, "poster.jpg"))
    want = PROJECTS[pid].get("want_reaction")
    doc = editor.default_doc(meta)
    if want:
        set_project(pid, stage="Finding the cam and the gameplay")
        try:
            det = editor.detect_reaction(dst, meta)
        except Exception as e:  # noqa: BLE001 — the editor can still be used by hand
            det = {"found": False, "error": str(e)[:200]}
        set_project(pid, detect=det)
    set_project(pid, meta=meta, doc=doc, state="ready", stage=None, progress=None, updated=time.time())
    save_project(pid)


def run_import(pid, work):
    try:
        work()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_project(pid, state="failed", error=friendly_error(e)[:400], stage=None)
        save_project(pid)


def import_link(pid, url, start, length, from_end):
    d = proj_dir(pid)
    set_project(pid, stage="Finding the video")
    src = resolve_source(url)
    set_project(pid, name=(src.get("title") or "Video")[:80])
    raw_path = os.path.join(d, "import.mp4")
    progress = lambda f: set_project(pid, stage="Converting", progress=round(f, 3))  # noqa: E731
    if src.get("live"):
        stream = LiveStream(url)
        if not stream.readable:
            raise RuntimeError("This live stream can't be read right now. Try again in a minute.")
        head = stream.head()
        if head is None:
            stream.refresh()
            head = stream.head() or 0
        edge = max(0.0, (head - 1) * stream.seg)
        if from_end or start >= edge:
            start = max(0.0, edge - length)
        end = min(edge, start + length)
        if end - start < 1:
            raise RuntimeError("The stream hasn't got that far yet.")
        set_project(pid, stage=f"Downloading {fmt_dur(end - start)} of the live stream")
        stream.clip(start, end, raw_path, quality="1080", on_convert=progress)
    else:
        dur = src.get("duration") or 0
        if dur and start >= dur:
            raise RuntimeError(f"The video is only {fmt_hms(dur)} long.")
        end = min(dur, start + length) if dur else start + length
        set_project(pid, stage=f"Downloading {fmt_dur(end - start)} from {fmt_hms(start)}")
        download_clip_section(url, start, end, raw_path, raw_info=src.get("raw"), quality="1080",
                              on_convert=progress)
    finish_import(pid, raw_path)


def parse_time(v):
    """Seconds from '90', '1:30' or '1:02:03'."""
    v = str(v or "").strip()
    if not v:
        return 0.0
    parts = v.split(":")
    if len(parts) > 3:
        raise ValueError(v)
    total = 0.0
    for part in parts:
        total = total * 60 + float(part or 0)
    if total < 0 or total != total:
        raise ValueError(v)
    return total


def render_project(pid, priority=0, watermark=False, refund=None):
    """Export a project. `refund()` gives the clip back to the user's plan if the export fails."""
    p = PROJECTS[pid]
    d = proj_dir(pid)
    with RENDER_SLOTS.take(priority):
        with PROJ_LOCK:
            r = p["render"]
            r.update(state="rendering", progress=0.0, started=time.time())
            doc, meta = p["doc"], p["meta"]
        t = time.time()
        name = f"export_{uuid.uuid4().hex[:6]}.mp4"

        def progress(f, stage="Encoding"):
            with PROJ_LOCK:
                if stage == "Encoding" and r.get("stage") != "Encoding":
                    r["encode_started"] = time.time()
                r["progress"] = round(f, 3)
                r["stage"] = stage
                if stage == "Encoding" and f > 0.03:
                    took = time.time() - r["encode_started"]
                    r["eta"] = round(took / f * (1 - f))

        try:
            if not storage.fetch(os.path.join(d, "source.mp4")):
                raise RuntimeError("The project's video is missing.")
            enc = editor.render(os.path.join(d, "source.mp4"), os.path.join(d, name), doc, meta,
                                os.path.join(d, "assets"), progress=progress, watermark=watermark)
            old = r.get("file")
            with PROJ_LOCK:
                r.update(state="done", progress=1.0, file=name, encoder=enc, seconds=round(time.time() - t),
                         size_mb=round(os.path.getsize(os.path.join(d, name)) / 1e6, 1),
                         format=doc["format"], size=doc["size"])
            storage.upload(os.path.join(d, name))
            if old and old != name:
                storage.delete(os.path.join(d, old))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            with PROJ_LOCK:
                r.update(state="failed", error=str(e)[:300])
            if refund:
                refund()
    save_project(pid)


@bp.route("/editor")
def editor_home():
    return render_template("editor_home.html", upload_mb=UPLOAD_MB, import_max=IMPORT_MAX_SEC,
                           any_site="*" in ALLOWED_HOSTS)


@bp.route("/editor/<pid>")
def editor_page(pid):
    p = get_project(pid)
    return render_template("editor.html", pid=pid, name=p.get("name") or "Untitled",
                           formats=editor.FORMATS, has_font=bool(reels.font_file()),
                           js_ver=int(os.path.getmtime(os.path.join(BASE_DIR, "static", "editor.js"))),
                           consts={"LINE_H": editor.LINE_H, "BASELINE": editor.BASELINE,
                                   "TEXT_PAD": editor.TEXT_PAD, "TEXT_MAX": editor.TEXT_MAX,
                                   "MAX_LAYERS": editor.MAX_LAYERS,
                                   # the Valorant kill feed as a share of the frame (for the POV layout)
                                   "KILLFEED": [round(v / d, 5) for v, d in zip(reels.KILLFEED, (1920, 1080, 1920, 1080))]})


@bp.route("/editor/font.ttf")
def editor_font():
    """The caption font, so the preview measures and draws text exactly like the export."""
    path = reels.font_file()
    if not path:
        abort(404)
    resp = send_from_directory(os.path.dirname(path), os.path.basename(path), mimetype="font/ttf")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/api/editor/projects")
def editor_projects():
    with PROJ_LOCK:
        items = sorted((project_view(p) for p in PROJECTS.values() if auth.can_access(p.get("owner"))),
                       key=lambda p: p["updated"] or 0, reverse=True)
    return jsonify({"projects": items})


@bp.route("/api/editor/projects", methods=["POST"])
def editor_create():
    ent = auth.ent()
    if not ent["can_create"]:
        return jsonify({"error": ent["reason"], "upgrade": True}), 402
    body = request.get_json(force=True, silent=True) or {}
    kind = body.get("kind")
    if kind == "clip":
        job_id, cid = str(body.get("job") or ""), str(body.get("cid") or "")
        job = JOBS.get(job_id) if JOB_ID.match(job_id) else None
        job = job if job and auth.can_access(job.get("owner")) else None
        d = job and job.get("downloads", {}).get(cid)
        if not d or d.get("state") != "done":
            return jsonify({"error": "That clip isn't downloaded."}), 400
        c = next((c for c in job.get("candidates") or [] if str(c["id"]) == cid), {})
        src = os.path.join(CLIPS_DIR, job_id, d["file"])
        name = f"{c.get('label') or 'Clip ' + cid} · {(job.get('title') or '')[:50]}".strip(" ·")
        pid = new_project(name, {"kind": "clip", "job": job_id, "cid": int(cid)},
                          want_reaction=bool(body.get("reaction")))
        dst = os.path.join(proj_dir(pid), "import.mp4")

        def work():
            set_project(pid, stage="Copying the clip")
            if not storage.fetch(src):
                raise RuntimeError("The clip's file is missing. Download the round again.")
            ensure_mp4(src)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
            finish_import(pid, dst)
        threading.Thread(target=run_import, args=(pid, work), daemon=True).start()
        return jsonify({"id": pid})
    if kind == "link":
        url = str(body.get("url") or "").strip()
        if not allowed_url(url):
            return jsonify({"error": "Paste a YouTube or Twitch link." if "*" not in ALLOWED_HOSTS
                            else "That doesn't look like a link."}), 400
        try:
            start = parse_time(body.get("start"))
            length = parse_time(body.get("length") or "60")
        except ValueError:
            return jsonify({"error": "Times look like 1:30:00 or 90."}), 400
        if not 1 <= length <= IMPORT_MAX_SEC:
            return jsonify({"error": f"The length can be 1 second to {IMPORT_MAX_SEC // 60} minutes."}), 400
        pid = new_project("Video from a link", {"kind": "link", "url": url, "start": start, "length": length},
                          want_reaction=bool(body.get("reaction")))
        threading.Thread(target=run_import, daemon=True,
                         args=(pid, lambda: import_link(pid, url, start, length, bool(body.get("from_end"))))).start()
        return jsonify({"id": pid})
    return jsonify({"error": "bad request"}), 400


@bp.route("/api/editor/upload", methods=["POST"])
def editor_upload():
    ent = auth.ent()
    if not ent["can_create"]:            # before reading the upload
        return jsonify({"error": ent["reason"], "upgrade": True}), 402
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in VIDEO_TYPES:
        return jsonify({"error": "Upload a video file (MP4, MOV, MKV, WebM…)."}), 400
    name = os.path.splitext(os.path.basename(f.filename))[0][:80] or "Upload"
    pid = new_project(name, {"kind": "upload"}, want_reaction=request.form.get("reaction") == "1")
    path = os.path.join(proj_dir(pid), "import" + ext)
    try:
        f.save(path)
    except OSError as e:
        set_project(pid, state="failed", error=f"Saving the upload failed: {e}")
        save_project(pid)
        return jsonify({"error": "Saving the upload failed."}), 500
    threading.Thread(target=run_import, args=(pid, lambda: finish_import(pid, path)), daemon=True).start()
    return jsonify({"id": pid})


@bp.route("/api/editor/<pid>")
def editor_get(pid):
    with PROJ_LOCK:
        return jsonify(project_view(get_project(pid), full=True))


@bp.route("/api/editor/<pid>", methods=["PUT"])
def editor_save(pid):
    p = get_project(pid)
    if p.get("state") != "ready":
        return jsonify({"error": "The video isn't ready yet."}), 409
    body = request.get_json(force=True, silent=True) or {}
    doc = editor.clean_doc(body.get("doc"), p["meta"], os.path.join(proj_dir(pid), "assets"))
    with PROJ_LOCK:
        p["doc"] = doc
        p["want_reaction"] = False
        if body.get("asr_applied") and isinstance(p.get("asr"), dict):
            p["asr"]["applied"] = True
        if isinstance(body.get("name"), str) and body["name"].strip():
            p["name"] = body["name"].strip()[:80]
        p["updated"] = time.time()
    save_project(pid)
    return jsonify({"ok": True, "updated": p["updated"]})


@bp.route("/api/editor/<pid>", methods=["DELETE"])
def editor_delete(pid):
    p = get_project(pid)
    if project_busy(p):
        return jsonify({"error": "Wait until it has finished importing or exporting."}), 409
    with PROJ_LOCK:
        PROJECTS.pop(pid, None)
    storage.delete_tree(proj_dir(pid))
    return jsonify({"ok": True})


@bp.route("/api/editor/<pid>/detect", methods=["POST"])
def editor_detect(pid):
    """Find the streamer's cam and the gameplay (cached; ?again=1 re-runs it)."""
    p = get_project(pid)
    if p.get("state") != "ready":
        return jsonify({"error": "The video isn't ready yet."}), 409
    with PROJ_LOCK:
        if p.get("detect") and not request.args.get("again"):
            return jsonify(p["detect"])
        if p.get("detecting"):
            return jsonify({"error": "Already looking, try again in a few seconds."}), 409
        p["detecting"] = True
    try:
        if not storage.fetch(os.path.join(proj_dir(pid), "source.mp4")):
            raise RuntimeError("The project's video is missing.")
        det = editor.detect_reaction(os.path.join(proj_dir(pid), "source.mp4"), p["meta"])
    except Exception as e:  # noqa: BLE001
        det = {"found": False, "error": str(e)[:200]}
    finally:
        with PROJ_LOCK:
            p.pop("detecting", None)
    with PROJ_LOCK:
        p["detect"] = det
        p["want_reaction"] = False
    save_project(pid)
    return jsonify(det)


@bp.route("/api/editor/<pid>/transcribe", methods=["POST"])
def editor_transcribe(pid):
    """Speech to text for the trimmed part of the video (runs in the background)."""
    p = get_project(pid)
    if p.get("state") != "ready":
        return jsonify({"error": "The video isn't ready yet."}), 409
    if not captions.available():
        return jsonify({"error": "Speech to text isn't installed (pip install faster-whisper)."}), 400
    if not (p.get("meta") or {}).get("audio"):
        return jsonify({"error": "This video has no sound to transcribe."}), 400
    body = request.get_json(force=True, silent=True) or {}
    quality = body.get("quality") if body.get("quality") in captions.MODELS else "best"
    lang = body.get("language") if isinstance(body.get("language"), str) and re.match(r"^[a-z]{2,3}$", body.get("language")) else None
    vocab = " ".join(str(body.get("vocab") or "").split())[:400]
    with PROJ_LOCK:
        if (p.get("asr") or {}).get("state") == "running":
            return jsonify({"error": "Already transcribing."}), 409
        t0, t1 = (p.get("doc") or {}).get("trim") or [0, p["meta"]["duration"]]
        p["asr"] = {"state": "running", "stage": "Waiting", "progress": None, "quality": quality,
                    "start": t0, "end": t1, "started": time.time()}
    save_project(pid)

    def work():
        def progress(stage, frac):
            with PROJ_LOCK:
                p["asr"].update(stage=stage, progress=None if frac is None else round(frac, 3))
        try:
            if not storage.fetch(os.path.join(proj_dir(pid), "source.mp4")):
                raise RuntimeError("The project's video is missing.")
            words, info = captions.transcribe(os.path.join(proj_dir(pid), "source.mp4"), t0, t1, quality,
                                              lang, vocab, progress)
            with open(os.path.join(proj_dir(pid), "transcript.json"), "w", encoding="utf-8") as f:
                json.dump({"words": words, "info": info, "start": t0, "end": t1}, f)
            storage.upload(os.path.join(proj_dir(pid), "transcript.json"))
            with PROJ_LOCK:
                p["asr"] = {"state": "done", "words": len(words), "info": info, "start": t0, "end": t1,
                            "seconds": round(time.time() - p["asr"]["started"])}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            with PROJ_LOCK:
                p["asr"] = {"state": "failed", "error": str(e)[:300]}
        save_project(pid)
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@bp.route("/api/editor/<pid>/transcript")
def editor_transcript(pid):
    get_project(pid)
    path = os.path.join(proj_dir(pid), "transcript.json")
    if not os.path.exists(path):
        abort(404)
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@bp.route("/api/editor/<pid>/waveform")
def editor_waveform(pid):
    """Audio loudness peaks for the timeline (cached)."""
    p = get_project(pid)
    if p.get("state") != "ready":
        abort(404)
    path = os.path.join(proj_dir(pid), "waveform.json")
    if not os.path.exists(path):
        src = os.path.join(proj_dir(pid), "source.mp4")
        has_audio = (p.get("meta") or {}).get("audio")
        if has_audio and not storage.fetch(src):
            abort(404)
        peaks = editor.waveform(src) if has_audio else []
        with open(path, "w", encoding="utf-8") as f:
            json.dump(peaks, f)
        storage.upload(path)
    resp = send_from_directory(proj_dir(pid), "waveform.json", mimetype="application/json")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.route("/api/editor/<pid>/asset", methods=["POST"])
def editor_asset(pid):
    """A picture for an image layer, re-saved as PNG (so only real images get stored)."""
    get_project(pid)
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file."}), 400
    try:
        from PIL import Image
        im = Image.open(f.stream)
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((2048, 2048))
    except Exception:  # noqa: BLE001
        return jsonify({"error": "That isn't a picture Recaply can read (use PNG, JPG or WebP)."}), 400
    name = uuid.uuid4().hex[:12] + ".png"
    im.save(os.path.join(proj_dir(pid), "assets", name))
    storage.upload(os.path.join(proj_dir(pid), "assets", name))
    return jsonify({"asset": name, "w": im.width, "h": im.height})


@bp.route("/api/editor/<pid>/export", methods=["POST"])
def editor_export(pid):
    p = get_project(pid)
    if p.get("state") != "ready":
        return jsonify({"error": "The video isn't ready yet."}), 409
    body = request.get_json(force=True, silent=True) or {}
    doc = editor.clean_doc(body.get("doc"), p["meta"], os.path.join(proj_dir(pid), "assets"))
    if not reels.font_file() and any(L["type"] in ("text", "captions") for L in doc["layers"]):
        return jsonify({"error": "No bold font found for text layers. Set RECAPLY_FONT to a .ttf file."}), 400
    if (p.get("render") or {}).get("state") in ("queued", "rendering"):
        return jsonify({"error": "Already exporting."}), 409
    # A project made from a downloaded clip was paid for with that clip; others count once.
    o = p.get("origin") or {}
    key = f"dl:{o.get('job')}:{o.get('cid')}" if o.get("kind") == "clip" else f"ex:{pid}"
    ok, info = auth.charge([key])
    if not ok:
        return charge_failed(info)
    ent = auth.ent()
    give_back = auth.refunder()
    refund = (lambda: give_back([key])) if info else None
    with PROJ_LOCK:
        if (p.get("render") or {}).get("state") in ("queued", "rendering"):
            return jsonify({"error": "Already exporting."}), 409
        p["doc"] = doc
        p["updated"] = time.time()
        old = p.get("render") or {}
        p["render"] = {"state": "queued", "progress": 0.0, "file": old.get("file")}
    save_project(pid)
    threading.Thread(target=render_project, args=(pid, ent["priority"], ent["watermark"], refund),
                     daemon=True).start()
    return jsonify({"ok": True, "watermark": ent["watermark"]})


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:60] or "recaply"


@bp.route("/editor/<pid>/<kind>")
def editor_file(pid, kind):
    p = get_project(pid)
    d = proj_dir(pid)
    if kind == "source.mp4" and p.get("state") == "ready":
        return send_media(d, "source.mp4")
    if kind == "poster.jpg" and os.path.exists(os.path.join(d, "poster.jpg")):
        resp = send_from_directory(d, "poster.jpg")
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp
    r = p.get("render") or {}
    if kind == "export.mp4" and r.get("state") == "done" and r.get("file"):
        fmt = (r.get("format") or "").replace(":", "x")
        return send_media(d, r["file"], f"{_slug(p.get('name'))}_{fmt}.mp4" if request.args.get("dl") else None)
    abort(404)


def send_media(folder, name, download_name=None):
    """A video: a signed link to the storage bucket when it's there (browsers fetch it
    straight from Cloudflare), otherwise the copy on this server's disk."""
    path = os.path.join(folder, name)
    url = storage.link(path, download_name)
    if url:
        resp = redirect(url)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    if not storage.fetch(path):
        abort(404)
    return send_from_directory(folder, name, as_attachment=bool(download_name), download_name=download_name)


@bp.route("/editor/<pid>/assets/<name>")
def editor_asset_file(pid, name):
    get_project(pid)
    if not editor.ASSET_NAME.match(name):
        abort(404)
    return send_from_directory(os.path.join(proj_dir(pid), "assets"), name)
