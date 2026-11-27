"""
app.py: Clipforge itself. In the Windows app (desktop.py) it serves the app's window from 127.0.0.1 and
accounts come from the online service (desktop_auth.py); it can also run as a self-hosted web app with
local accounts (auth.py).

The product is the React studio (frontend/, built into static/studio/, glued in by studio_app.py): paste a
link or upload a video, get ranked clips, edit and export them, run a batch queue, watch live streams.
The rest is the small JSON API in clip_routes / studio_routes / live_routes plus the public pages
(landing, pricing, sign-in, account). Old page URLs redirect into /studio.

Other modules: desktop_auth / auth + accounts (sign-in, plans), common (shared helpers and work slots),
storage (optional files in Cloudflare R2), webutil (headers), config (settings).
"""

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

import accounts
import clip_routes
import editor_routes
import live_routes
import storage
import studio_app
import studio_routes
import webutil
from config import DESKTOP, HOST, PORT, env_int

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

# With a storage bucket set up, restore the database before anything reads it.
# (Worker processes skip this.)
if __name__ != "__mp_main__" and storage.ENABLED:
    storage.init(accounts.DB_PATH)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024   # forms and JSON bodies are tiny
app.register_blueprint(clip_routes.bp)      # the any-video clip finder
app.register_blueprint(live_routes.bp)      # live-stream mode (Windows app)
app.register_blueprint(studio_routes.bp)    # editing, publishing, batch queue, usage
app.register_blueprint(editor_routes.bp)     # the Editor: any video, one clip, all the Director's tools
app.register_blueprint(studio_app.bp)       # the React studio (static/studio) and its small API
auth.init_app(app)


@app.before_request
def webhook_body_limit():
    if request.path == "/billing/webhook":
        request.max_content_length = 1024 * 1024


webutil.install(app, webutil.csp(storage.origin()), auth.current_user)


def busy_work() -> bool:
    """True while clips are being found or cut, or a live stream is being recorded (the desktop app asks before quitting)."""
    from recaply.jobs import store
    try:
        if any(j["state"] in ("queued", "running") for j in store.list_jobs(everyone=True, limit=200)):
            return True
        return any(c.get("state") in ("recording", "starting") for c in live_routes.get_manager().status())
    except Exception:  # noqa: BLE001: never block quitting over a status check
        return False


# ---------- routes ----------

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /$\nAllow: /pricing\nDisallow: /\n", mimetype="text/plain")


@app.route("/")
def landing():
    """The product website (in the Windows app, straight to the studio)."""
    if DESKTOP:
        return redirect(url_for("app_home"))
    return render_template("landing.html")


@app.route("/app")
def app_home():
    return redirect("/studio")


# The old server-rendered pages are gone; their addresses (bookmarks, emails, old links) land in the studio.
@app.route("/queue")
def old_queue():
    return redirect("/studio/queue")


@app.route("/usage")
def old_usage():
    return redirect("/studio/usage")


@app.route("/live")
def old_live():
    return redirect("/studio/live")


@app.route("/clipjob/<job_id>")
def old_project(job_id):
    return redirect(f"/studio/p/{job_id}")


@app.route("/clipjob/<job_id>/edit/<int:cid>")
def old_editor(job_id, cid):
    return redirect(f"/studio/p/{job_id}/clip/{cid}")


@app.route("/reels")
@app.route("/editor")
@app.route("/job/<job_id>")
def old_pages(job_id=None):
    return redirect("/studio")


# Worker processes (exports draw frames in a process pool) import this file as "__mp_main__":
# they must not resume jobs or start background threads.
if __name__ != "__mp_main__":
    clip_routes.resume_jobs()
    live_routes.start_enabled()
    studio_routes.start_publish_scheduler()

if __name__ == "__main__":
    # Jobs live in this process's memory, so run ONE process with many threads.
    host, port = HOST, PORT
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        from waitress import serve
    except ImportError:
        print(f"Clipforge (dev server) is running. Open http://{shown}:{port} in your browser.")
        app.run(host=host, port=port, debug=False, threaded=True)
    else:
        print(f"Clipforge is running. Open http://{shown}:{port} in your browser.")
        serve(app, host=host, port=port, threads=env_int("RECAPLY_THREADS", 16),
              channel_timeout=300, ident="Clipforge")
