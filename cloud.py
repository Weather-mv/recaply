"""
cloud.py — Clipforge's online service: the product website, accounts, plans
and payments, and the API the Windows app signs in through (cloud_api.py).

It never handles video: recaps, clips, reels and the editor run in the
Windows app on each user's PC (desktop.py). So this server is small and
cheap to host. The web app (/app) works the same way: the page hands the
work to the Clipforge helper running on the user's PC.

    python cloud.py        # http://127.0.0.1:8000

The installer is served from <data dir>/downloads/Recaply-Setup.exe, or set
RECAPLY_DOWNLOAD_URL to link to it elsewhere (a GitHub release, R2...).
"""

import glob
import os

os.environ.setdefault("RECAPLY_DESKTOP", "0")
os.environ.setdefault("RECAPLY_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_data"))
os.environ.setdefault("PORT", "8000")

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory  # noqa: E402

import auth  # noqa: E402
import cloud_api  # noqa: E402
import webutil  # noqa: E402
from config import CLIPS_DIR, HELPER_PORTS, HOST, PORT, VERSION, env_int  # noqa: E402

DOWNLOADS = os.path.join(CLIPS_DIR, "downloads")
DOWNLOAD_URL = os.environ.get("RECAPLY_DOWNLOAD_URL", "").strip()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.before_request
def webhook_body_limit():
    if request.path == "/billing/webhook":
        request.max_content_length = 1024 * 1024


auth.init_app(app)
app.register_blueprint(cloud_api.bp)
# The web app's page looks for the Clipforge helper on this PC (see /app).
webutil.install(app, webutil.csp(connect=" ".join(f"http://127.0.0.1:{p}" for p in HELPER_PORTS)),
                auth.current_user)


def installer():
    """(link, size in MB) for the newest installer, or (None, None)."""
    if DOWNLOAD_URL:
        return DOWNLOAD_URL, None
    files = sorted(glob.glob(os.path.join(DOWNLOADS, "Recaply-Setup*.exe")), key=os.path.getmtime)
    if not files:
        return None, None
    return f"/download/{os.path.basename(files[-1])}", round(os.path.getsize(files[-1]) / 1e6)


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/download")
def download():
    link, size = installer()
    return render_template("download.html", installer=link, size_mb=size, version=VERSION)


@app.route("/download/<name>")
def download_file(name):
    if not name.startswith("Recaply-Setup") or not name.endswith(".exe"):
        abort(404)
    return send_from_directory(DOWNLOADS, name, as_attachment=True)


@app.route("/app")
def app_home():
    """The web app. Videos are processed by the Clipforge helper on the user's own PC: this
    page finds it on 127.0.0.1, signs it in to this account and opens Clipforge in this tab."""
    link, _ = installer()
    return render_template("webapp.html", installer=link, version=VERSION, ports=HELPER_PORTS)


@app.post("/app/connect")
def app_connect():
    """A one-time code for the helper (sent to it by the page, traded back via /api/v1/connect)."""
    if auth.limited(f"appconnect:{auth.uid()}", 60, 600):
        return jsonify({"error": "Too many tries. Wait a few minutes."}), 429
    return jsonify({"code": cloud_api.connect_code(auth.current_user())})


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /$\nAllow: /pricing\nAllow: /download\nDisallow: /\n", mimetype="text/plain")


if __name__ == "__main__":
    os.makedirs(DOWNLOADS, exist_ok=True)
    shown = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    try:
        from waitress import serve
    except ImportError:
        print(f"Clipforge online service (dev server): http://{shown}:{PORT}")
        app.run(host=HOST, port=PORT, threaded=True)
    else:
        print(f"Clipforge online service: http://{shown}:{PORT}")
        serve(app, host=HOST, port=PORT, threads=env_int("RECAPLY_THREADS", 16), ident="Clipforge")
