"""
social_routes.py: the Social hub's API. Accounts belong to one Clipforge user; tokens never leave the server.

  GET    /api/social/networks                 every network, whether it's set up, and its text limits
  GET    /api/social/accounts                 the user's connected accounts (never their tokens)
  POST   /api/social/connect/<network>        start a sign-in: opens the network's page in the browser
  GET    /api/social/connect/status/<state>   pending | working | done | failed
  DELETE /api/social/accounts/<id>            disconnect
  GET    /social/callback/<network>           the network's redirect (when RECAPLY_SOCIAL_REDIRECT_BASE is this server)

  GET    /api/social/videos                   finished shorts and cuts that can be posted
  POST   /api/social/captions                 {job_id, clip_id, networks, tone, extra} -> per-network captions
  POST   /api/social/thumbnail/frames         {job_id, clip_id, file} -> candidate stills
  POST   /api/social/thumbnail                {job_id, clip_id, frame, text, size, template} -> a thumbnail
  POST   /api/social/thumbnail/upload         multipart: job_id, clip_id, image
  GET    /api/social/thumb/<job>/<name>       a still or thumbnail

  GET    /api/social/posts?from=&to=          the calendar: posts (grouped targets) and drafts
  POST   /api/social/posts                    create: {job_id, clip_id, file, targets: [...], when, draft, thumb_at}
  PATCH  /api/social/posts/<group>            edit text / time / draft <-> scheduled
  POST   /api/social/posts/<group>/retry      try the failed targets again
  DELETE /api/social/posts/<group>            delete what hasn't gone out
  GET    /api/social/best-times               suggested posting times per network
"""

from __future__ import annotations

import re
import time
import uuid
import webbrowser

from flask import Blueprint, abort, jsonify, request, send_from_directory

import clip_routes
from config import DESKTOP
from recaply.jobs import store
from recaply.publish import queue as pq
from recaply.publish.base import PublishError
from recaply.social import accounts, captions, networks, oauth, thumbnail

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("social", __name__)
THUMB_NAME = re.compile(r"^[\w.-]{1,120}\.(jpg|jpeg|png)$")
UPLOAD_MAX = 12 * 1024 * 1024
BEST_TIMES = {          # local time, weekday 0 = Monday; general guidance, not measured on the user's audience
    "youtube": {"days": [3, 4, 5, 6], "hours": [12, 15, 18]},
    "linkedin": {"days": [1, 2, 3], "hours": [8, 12, 17]},
    "x": {"days": [0, 1, 2, 3, 4], "hours": [9, 12, 17]},
    "instagram": {"days": [0, 2, 4, 5], "hours": [11, 14, 19]},
}


@bp.before_request
def _gate():
    if request.path == "/api/social/thumbnail/upload":
        request.max_content_length = UPLOAD_MAX


def _err(msg: str, code: int = 400):
    return jsonify(error=msg), code


def _account(aid) -> dict:
    try:
        acc = accounts.get(int(aid))
    except (TypeError, ValueError):
        acc = None
    if not acc or not auth.can_access(acc["owner"]):
        abort(404)
    return acc


def _clip(job: dict, cid) -> dict:
    for c in (job.get("result") or {}).get("clips") or []:
        if str(c["id"]) == str(cid):
            return c
    abort(404)


def _videos_of(job: dict) -> list[dict]:
    """The job's finished files that can be posted: exported shorts first, then plain cuts."""
    clips = {str(c["id"]): c for c in (job.get("result") or {}).get("clips") or []}
    out = []
    for key, cut in (job.get("cuts") or {}).items():
        if cut.get("state") != "done" or not cut.get("file") or not str(cut["file"]).lower().endswith(".mp4"):
            continue
        short = key.startswith("s")
        cid = key[1:] if short else key
        c = clips.get(cid)
        if not c:
            continue
        out.append({"job_id": job["id"], "job_title": job.get("title") or job["input"][:80], "clip_id": c["id"],
                    "file": cut["file"], "kind": "short" if short else "cut", "format": cut.get("format") or "16:9",
                    "duration": cut.get("duration") or c.get("duration"), "title": c.get("title") or "",
                    "hook": c.get("hook") or "", "description": c.get("description") or "",
                    "hashtags": c.get("hashtags") or [], "created": job.get("created")})
    out.sort(key=lambda v: (v["kind"] != "short", v["clip_id"]))
    return out


# ---------- networks and accounts ----------

@bp.route("/api/social/networks")
def social_networks():
    return jsonify(networks=[{**n.public(), "redirect_uri": oauth.redirect_uri(n.key)} for n in networks.NETWORKS.values()])


@bp.route("/api/social/accounts")
def social_accounts():
    return jsonify(accounts=accounts.list_accounts(auth.uid()))


@bp.route("/api/social/connect/<network>", methods=["POST"])
def social_connect(network):
    try:
        flow = oauth.begin(auth.uid(), network)
    except oauth.OAuthError as e:
        return _err(str(e))
    opened = False
    if DESKTOP:                    # the app's window: open the user's real browser (their saved sign-ins live there)
        try:
            opened = bool(webbrowser.open(flow["url"]))
        except Exception:  # noqa: BLE001 — the studio also shows the link to open by hand
            pass
    return jsonify(ok=True, opened=opened, **flow)


@bp.route("/api/social/connect/status/<state>")
def social_connect_status(state):
    st = oauth.status(state, auth.uid())
    if st is None:
        return jsonify(status="expired"), 404
    return jsonify(st)


@bp.route("/api/social/accounts/<int:aid>", methods=["DELETE"])
def social_disconnect(aid):
    _account(aid)
    return jsonify(ok=accounts.remove(aid))


# ---------- videos, captions, thumbnails ----------

@bp.route("/api/social/videos")
def social_videos():
    out = []
    for j in store.list_jobs(owner=auth.uid(), limit=100):
        if j["state"] != "ready":
            continue
        full = store.get(j["id"])
        if full and auth.can_access(full.get("owner")):
            out.extend(_videos_of(full))
    return jsonify(videos=out)


@bp.route("/api/social/captions", methods=["POST"])
def social_captions():
    body = request.get_json(force=True, silent=True) or {}
    job = clip_routes._owned(str(body.get("job_id") or ""))
    clip = _clip(job, body.get("clip_id"))
    nets = [str(n) for n in body.get("networks") or [] if networks.get(str(n))]
    if not nets:
        return _err("Pick at least one network.")
    out, by = captions.generate(clip, nets, str(body.get("tone") or "hype"), str(clip.get("transcript") or ""),
                                str(body.get("extra") or ""))
    return jsonify(captions=out, by=by)


def _video_path(job: dict, name: str) -> str:
    if name not in {v["file"] for v in _videos_of(job)}:
        abort(404)
    return pq.resolve_file(job["id"], name)


@bp.route("/api/social/thumbnail/frames", methods=["POST"])
def social_frames():
    body = request.get_json(force=True, silent=True) or {}
    job = clip_routes._owned(str(body.get("job_id") or ""))
    clip = _clip(job, body.get("clip_id"))
    path = _video_path(job, str(body.get("file") or ""))
    out = thumbnail.frames(job["id"], int(clip["id"]), path)
    if not out:
        return _err("Couldn't read frames from that video.")
    return jsonify(frames=out)


@bp.route("/api/social/thumbnail", methods=["POST"])
def social_thumbnail():
    body = request.get_json(force=True, silent=True) or {}
    job = clip_routes._owned(str(body.get("job_id") or ""))
    clip = _clip(job, body.get("clip_id"))
    frame = str(body.get("frame") or "")
    if not frame.startswith("thumbs/") or not THUMB_NAME.match(frame[7:]):
        return _err("Pick a frame first.")
    try:
        name = thumbnail.render(job["id"], int(clip["id"]), frame, str(body.get("text") or ""),
                                str(body.get("size") or "youtube"), str(body.get("template") or "bold"), auth.uid())
    except (OSError, PublishError) as e:
        return _err(f"Couldn't make the thumbnail: {e}")
    return jsonify(name=name)


@bp.route("/api/social/thumbnail/upload", methods=["POST"])
def social_thumbnail_upload():
    job = clip_routes._owned(str(request.form.get("job_id") or ""))
    clip = _clip(job, request.form.get("clip_id"))
    f = request.files.get("image")
    if not f:
        return _err("Choose a picture to upload.")
    try:
        name = thumbnail.save_upload(job["id"], int(clip["id"]), f.read(UPLOAD_MAX + 1)[:UPLOAD_MAX])
    except ValueError as e:
        return _err(str(e))
    return jsonify(name=name)


@bp.route("/api/social/thumb/<job_id>/<name>")
def social_thumb(job_id, name):
    job = clip_routes._owned(job_id)
    if not THUMB_NAME.match(name or ""):
        abort(404)
    return send_from_directory(thumbnail.thumbs_dir(job["id"]), name, conditional=True)


# ---------- posts and the calendar ----------

def _when(raw) -> float | None:
    if raw in (None, "", "now"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except ValueError:
        raise PublishError("That date and time isn't valid.") from None


def _group_status(items: list[dict]) -> str:
    st = {i["status"] for i in items}
    for s in ("uploading", "failed", "queued", "draft"):
        if s in st:
            return "partial" if s == "failed" and "done" in st else s
    return "done" if st == {"done"} else ("cancelled" if st == {"cancelled"} else next(iter(st)))


def _posts(items: list[dict]) -> list[dict]:
    accs = {a["id"]: a for a in accounts.list_accounts(auth.uid())}
    jobs: dict[str, dict | None] = {}
    groups: dict[str, list[dict]] = {}
    for it in items:
        if not it.get("post_group"):
            continue
        if it["job_id"] not in jobs:
            jobs[it["job_id"]] = store.get(it["job_id"])
        j = jobs[it["job_id"]]
        if not j or not auth.can_access(j.get("owner")):
            continue
        groups.setdefault(it["post_group"], []).append(it)
    out = []
    for g, its in groups.items():
        first = its[0]
        j = jobs[first["job_id"]]
        out.append({
            "group": g, "job_id": first["job_id"], "job_title": j.get("title") or j["input"][:80],
            "clip_id": first.get("clip_id"), "file": first["file"], "run_at": min(i["run_at"] for i in its),
            "status": _group_status(its), "title": first["title"], "thumb_at": first.get("thumb_at"),
            "targets": [{"id": i["id"], "platform": i["platform"], "account_id": i.get("account_id"),
                         "account": (accs.get(i.get("account_id")) or {}).get("name", ""),
                         "avatar": (accs.get(i.get("account_id")) or {}).get("avatar", ""),
                         "title": i["title"], "caption": i["description"], "tags": i["tags"], "privacy": i["privacy"],
                         "thumbnail": i.get("thumbnail"), "status": i["status"], "error": i.get("error"),
                         "result": i.get("result"), "attempts": i["attempts"]} for i in its]})
    out.sort(key=lambda p: p["run_at"])
    return out


@bp.route("/api/social/posts")
def social_posts():
    now = time.time()
    try:
        start = float(request.args.get("from") or now - 40 * 86400)
        end = float(request.args.get("to") or now + 60 * 86400)
    except ValueError:
        return _err("Bad date range.")
    return jsonify(posts=_posts(pq.in_range(start, end)), now=now)


def _target(job: dict, t: dict) -> dict:
    acc = _account(t.get("account_id"))
    thumb = str(t.get("thumbnail") or "") or None
    if thumb and (not thumb.startswith("thumbs/") or not THUMB_NAME.match(thumb[7:])):
        raise PublishError("That thumbnail isn't part of the project.")
    net = networks.get(acc["network"])
    caption = str(t.get("caption") or "")
    if len(caption) > net.caption_max:
        raise PublishError(f"The {net.label} caption is {len(caption)} characters; the limit is {net.caption_max}.")
    title = " ".join(str(t.get("title") or "").split())
    if net.title_max and len(title) > net.title_max:
        raise PublishError(f"The {net.label} title is longer than {net.title_max} characters.")
    if acc["status"] == "reconnect":
        raise PublishError(f"Reconnect {acc['name']} ({net.label}) on the Accounts tab first.")
    privacy = t.get("privacy") if t.get("privacy") in ("public", "unlisted", "private") else "public"
    return {"account_id": acc["id"], "platform": acc["network"], "title": title or caption[:90] or "Clip",
            "description": caption, "thumbnail": thumb, "privacy": privacy,
            "tags": [str(x).lstrip("#") for x in (t.get("tags") or [])][:30]}


@bp.route("/api/social/posts", methods=["POST"])
def social_post_create():
    body = request.get_json(force=True, silent=True) or {}
    job = clip_routes._owned(str(body.get("job_id") or ""))
    clip = _clip(job, body.get("clip_id"))
    file = str(body.get("file") or "")
    _video_path(job, file)
    targets = body.get("targets") or []
    if not targets:
        return _err("Pick at least one account to post to.")
    try:
        ts = [_target(job, t) for t in targets]
        run_at = _when(body.get("when"))
        draft = bool(body.get("draft"))
        if run_at and run_at < time.time() - 60 and not draft:
            raise PublishError("That time has already passed. Pick a later time or post now.")
        thumb_at = body.get("thumb_at")
        thumb_at = float(thumb_at) if isinstance(thumb_at, (int, float)) else None
        group = uuid.uuid4().hex[:12]
        for t in ts:
            pq.enqueue(job["id"], file, t["platform"], t["title"], t["description"], t["tags"], run_at, t["privacy"],
                       account_id=t["account_id"], thumbnail=t["thumbnail"], thumb_at=thumb_at, post_group=group,
                       owner=auth.uid(), status="draft" if draft else "queued", clip_id=int(clip["id"]))
    except PublishError as e:
        return _err(str(e))
    if not draft:
        pq.start_scheduler()
    return jsonify(ok=True, group=group)


def _owned_group(group: str) -> list[dict]:
    if not re.match(r"^[0-9a-f]{12}$", group or ""):
        abort(404)
    its = pq.group(group)
    if not its:
        abort(404)
    j = store.get(its[0]["job_id"])
    if not j or not auth.can_access(j.get("owner")):
        abort(404)
    return its


@bp.route("/api/social/posts/<group>", methods=["PATCH"])
def social_post_edit(group):
    its = _owned_group(group)
    body = request.get_json(force=True, silent=True) or {}
    try:
        common: dict = {}
        if "when" in body:
            when = _when(body.get("when"))
            common["run_at"] = when or time.time()
        if "draft" in body:
            common["status"] = "draft" if body.get("draft") else "queued"
        if common.get("status") == "queued" and common.get("run_at", min(i["run_at"] for i in its)) < time.time() - 60:
            raise PublishError("That time has already passed. Pick a later time or post now.")
        if "thumb_at" in body:
            common["thumb_at"] = float(body["thumb_at"]) if isinstance(body["thumb_at"], (int, float)) else None
        edits = {int(t["id"]): t for t in body.get("targets") or [] if str(t.get("id", "")).isdigit()}
        job = store.get(its[0]["job_id"])
        changed = 0
        for it in its:
            f = dict(common)
            if it["id"] in edits:
                t = _target(job, {**edits[it["id"]], "account_id": it["account_id"]})
                f.update(title=t["title"], description=t["description"], thumbnail=t["thumbnail"], tags=t["tags"],
                         privacy=t["privacy"])
            if f and it["status"] in ("draft", "queued", "failed", "cancelled"):
                if it["status"] in ("failed", "cancelled") and "status" not in f:
                    f["status"] = "queued"
                changed += pq.update(it["id"], **f)
    except PublishError as e:
        return _err(str(e))
    pq.start_scheduler()
    return jsonify(ok=True, changed=changed)


@bp.route("/api/social/posts/<group>/retry", methods=["POST"])
def social_post_retry(group):
    its = _owned_group(group)
    n = sum(pq.retry(i["id"]) for i in its if i["status"] in ("failed", "cancelled"))
    pq.start_scheduler()
    return jsonify(ok=True, retried=n)


@bp.route("/api/social/posts/<group>", methods=["DELETE"])
def social_post_delete(group):
    its = _owned_group(group)
    n = sum(pq.delete(i["id"]) for i in its)
    return jsonify(ok=True, deleted=n, kept=len(its) - n)


@bp.route("/social/callback/<network>")
def social_callback(network):
    """The network's redirect when RECAPLY_SOCIAL_REDIRECT_BASE points at this server (hosted / tunnel set-ups)."""
    if network not in networks.NETWORKS:
        abort(404)
    ok, msg = oauth.complete(network, {k: v for k, v in request.args.items()})
    return oauth.page(ok, msg), 200 if ok else 400


@bp.route("/api/social/best-times")
def social_best_times():
    return jsonify(best=BEST_TIMES, note="General guidance per network (your own audience may differ).")

