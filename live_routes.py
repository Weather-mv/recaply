"""
live_routes.py — live-stream mode's page and API (a blueprint, Windows app only:
recording needs the user's own PC and disk).

  GET    /live                              the page: channels, status, event feed
  GET    /api/live                          channels + status, recent events, profile choices
  GET    /api/live/events?since=ID          events newer than ID
  POST   /api/live/channels                 add / update a channel (JSON); {"start": true} also starts it
  DELETE /api/live/channels/<name>          stop and remove a channel
  POST   /api/live/channels/<name>/start    start watching
  POST   /api/live/channels/<name>/stop     stop (pending moments are still cut)
"""

from __future__ import annotations

import os

from flask import Blueprint, abort, jsonify, render_template, request

from config import DESKTOP, LOCAL_ONLY
from recaply.live import config as lcfg
from recaply.live import events
from recaply.live.config import ConfigError
from recaply.live.manager import get_manager
from recaply.profiles import all_profiles

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

bp = Blueprint("live", __name__)
# Recording needs the machine's own disk and bandwidth: the Windows app, or the web app run on this
# computer (127.0.0.1). A shared online server doesn't offer it.
LIVE_OK = bool(DESKTOP or LOCAL_ONLY or os.environ.get("RECAPLY_LIVE", "").lower() in ("1", "true", "yes", "on"))


def available() -> bool:
    """Live mode is offered when the app runs on this computer: the Windows app, a local bind, or a page
    opened as localhost / 127.0.0.1 (the HOST variable some shells set must not turn it off)."""
    if os.environ.get("RECAPLY_LIVE", "").lower() in ("0", "false", "no", "off"):
        return False
    if LIVE_OK:
        return True
    try:
        name = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    except RuntimeError:
        return False
    return name in ("127.0.0.1", "localhost", "::1")


@bp.app_context_processor
def _live_flag():
    return {"live_ok": available()}


@bp.before_request
def _only_desktop():
    if not available():
        abort(404)
    if auth.uid() is None:
        return jsonify(error="Log in to continue."), 401
    return None


def _name(name: str) -> str:
    if not lcfg.NAME.match(name):
        abort(404)
    return name


def _mine(cfg) -> bool:
    return cfg is not None and (cfg.owner is None or cfg.owner == auth.uid())


def _guard(name: str) -> str:
    """404 for a channel that belongs to someone else (a missing one is reported by the action itself)."""
    cfg = lcfg.get(name)
    if cfg is not None and not _mine(cfg):
        abort(404)
    return name


def _state() -> dict:
    cfgs = {n: c for n, c in lcfg.load_all().items() if _mine(c)}
    return {"channels": [c for c in get_manager().status() if c["name"] in cfgs],
            "configs": {n: c.public() for n, c in cfgs.items()},
            "events": events.list_events(limit=60),
            "profiles": [{"key": p.key, "label": p.label} for p in all_profiles() if not p.hidden]}


@bp.route("/live")
def live_page():
    return render_template("live.html", desktop=DESKTOP or available())


@bp.route("/api/live")
def live_state():
    return jsonify(_state())


@bp.route("/api/live/events")
def live_events():
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    return jsonify(events=events.list_events(since_id=since, limit=100))


@bp.route("/api/live/channels", methods=["POST"])
def live_save():
    body = request.get_json(silent=True) or {}
    old = lcfg.get(str(body.get("name") or "").strip().lower())
    if old and not _mine(old):
        return jsonify(error="That channel name is taken."), 400
    body["owner"] = auth.uid()
    try:
        # secrets are never sent back to the page: a blank webhook on an existing channel keeps the saved one
        if old:
            for k in ("webhook", "discord"):
                if body.get(k) in (None, True):
                    body[k] = getattr(old, k)
            body = {**old.__dict__, **body}
        cfg = lcfg.validate(body)
        lcfg.save(cfg)
        if body.get("start"):
            get_manager().start(cfg)
    except ConfigError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, channel=cfg.public())


@bp.route("/api/live/channels/<name>", methods=["DELETE"])
def live_delete(name):
    name = _guard(_name(name))
    get_manager().stop(name, wait=15)
    return jsonify(ok=lcfg.remove(name))


@bp.route("/api/live/channels/<name>/start", methods=["POST"])
def live_start(name):
    try:
        get_manager().start_saved(_guard(_name(name)))
    except ConfigError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@bp.route("/api/live/channels/<name>/stop", methods=["POST"])
def live_stop(name):
    return jsonify(ok=get_manager().stop(_guard(_name(name))))


def start_enabled() -> list[str]:
    """Start the channels marked "watch when the app starts" (called once at start-up)."""
    try:
        return get_manager().start_enabled()
    except Exception:  # noqa: BLE001 — never block the app from opening
        return []


_LIVE_URL = __import__("re").compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(live/|(@[\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)/live)|"
    r"twitch\.tv/[A-Za-z0-9_]{3,25}/?(\?.*)?$|kick\.com/[A-Za-z0-9_-]{3,25}/?(\?.*)?$)", __import__("re").I)


def channel_name_for(url: str) -> str:
    """A short, valid, unique-enough channel name from a link."""
    import hashlib
    import re
    tail = re.sub(r"[^a-z0-9]+", "-", url.lower().split("//", 1)[-1].split("?")[0].strip("/").replace("www.", ""))[:30].strip("-")
    return f"{tail or 'stream'}-{hashlib.sha1(url.encode()).hexdigest()[:5]}"


def is_live_link(url: str) -> bool:
    """True when `url` is a live link: by its shape, or (for a plain YouTube watch link) by asking YouTube."""
    if _LIVE_URL.match(url or ""):
        return True
    if "youtube.com/watch" in (url or "") or "youtu.be/" in (url or ""):
        try:
            from recaply.ingest.sources import resolve
            return bool(resolve(url).is_live)
        except Exception:  # noqa: BLE001 — not live as far as we can tell; the normal job reports its own problem
            return False
    return False


def watch_link(url: str, profile: str = "gaming") -> str:
    """Start (or find) a Live-mode channel for this link and return its name."""
    name = channel_name_for(url)
    cfg = lcfg.get(name)
    if cfg is None or not _mine(cfg):
        cfg = lcfg.validate({"name": name, "url": url, "profile": profile if profile not in ("", "auto") else "gaming",
                             "owner": auth.uid()})
        lcfg.save(cfg)
    mgr = get_manager()
    if not mgr.running(name):
        mgr.start(cfg)
    return name
