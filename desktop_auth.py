"""
desktop_auth.py — accounts in the Windows app.

The app's pages are served by a local server on 127.0.0.1 that only the
Clipforge window may use:

  * The window opens /__open?k=<key>, a random key made at every launch,
    which sets a cookie; requests without it are refused, so other programs
    and web pages in the user's browser can't drive the app.
  * Requests must name the local server as their Host (stops DNS rebinding),
    and every POST / PUT / DELETE needs the CSRF token and a same-site Origin.

Accounts, plans and clip counts live in the online service (cloud.py). The
app logs in through its API (cloud_api.py) and keeps the token encrypted with
Windows DPAPI in %LOCALAPPDATA%\\Recaply\\session.bin, so it stays signed in.
The plan is cached for when the internet is down; charging a clip needs a
connection. Plans and billing open on the website in the user's browser,
already signed in.

The web app: the website's /app page finds this server on one of
HELPER_PORTS (GET /__ping answers the website's origin only), asks the website
for a one-time code and opens /__web?c=<code> here in the browser tab. The
code is traded with the online service for this app's own token, and the tab
gets the launch cookie, so it works exactly like the window. In that tab,
plans and the account open on the website in the same tab.

Same interface as auth.py (current_user, uid, can_access, ent, charge,
refunder, csrf_token, init_app), so app.py and editor_routes.py work with either.
"""

import hmac
import json
import os
import platform
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, flash, jsonify, make_response, redirect, render_template, request, session, url_for

import accounts
from common import Offline
from config import APP_DIR, BASE_DIR, CLOUD_URL, VERSION

bp = Blueprint("auth", __name__)

PUBLIC_PATHS = {"/login", "/signup", "/__open", "/__web", "/healthz", "/favicon.ico"}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ON_DELETE = []                  # account deletion happens on the website
REFRESH_SEC = 60                # re-check the plan this often while pages are opened
# Starting new work re-checks the plan first (a used-up plan is caught before anything runs).
CREATE_PATHS = {"/start", "/api/editor/projects", "/api/editor/upload"}
LAUNCH_COOKIE = "recaply_launch"
WEB_COOKIE = "recaply_web"      # set in browser tabs opened from the website's web app
WEBSITE_ORIGIN = "{0}://{1}".format(*urlparse(CLOUD_URL)[:2])
SESSION_FILE = os.path.join(APP_DIR, "session.bin")

LAUNCH_KEY = os.environ.get("RECAPLY_LAUNCH_KEY") or secrets.token_urlsafe(24)
PORT = {"n": None}              # set by desktop.py once the local server has a port
_state = {"token": None, "user": None, "ent": None, "at": 0.0, "offline": False}
_lock = threading.Lock()


class CloudError(Exception):
    """The online service couldn't be reached (no internet, or it's down)."""


# ---------- talking to the online service ----------

def _call(method, path, body=None, token=None, timeout=20):
    """(status, json) from the online service's API. Raises CloudError when it can't be reached."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": f"Clipforge/{VERSION} (Windows)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CLOUD_URL}/api/v1/{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": f"Clipforge's servers answered with an error ({e.code}). Try again in a minute."}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CloudError("Can't reach Clipforge's servers. Check your internet connection and try again.") from e


# ---------- the saved sign-in (encrypted with Windows DPAPI) ----------

def _dpapi(data, protect):
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows couldn't decrypt the saved sign-in")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _save():
    os.makedirs(APP_DIR, exist_ok=True)
    if not _state["token"]:
        try:
            os.remove(SESSION_FILE)
        except OSError:
            pass
        return
    raw = json.dumps({k: _state[k] for k in ("token", "user", "ent", "at")}).encode()
    with open(SESSION_FILE + ".tmp", "wb") as f:
        f.write(_dpapi(raw, True))
    os.replace(SESSION_FILE + ".tmp", SESSION_FILE)


def _load():
    try:
        with open(SESSION_FILE, "rb") as f:
            saved = json.loads(_dpapi(f.read(), False))
        _state.update({k: saved.get(k) for k in ("token", "user", "ent")}, at=float(saved.get("at") or 0))
    except (OSError, ValueError):
        pass


def _signed_in(data):
    with _lock:
        _state.update(token=data["token"], user=data["user"], ent=data["ent"], at=time.time(), offline=False)
        _save()


def _sign_out():
    with _lock:
        _state.update(token=None, user=None, ent=None, at=0.0, offline=False)
        _save()


def refresh(force=False):
    """Re-read the plan from the online service (at most every REFRESH_SEC unless forced)."""
    token = _state["token"]
    if not token or (not force and time.time() - _state["at"] < REFRESH_SEC):
        return
    try:
        code, data = _call("GET", "me", token=token, timeout=8)
    except CloudError:
        _state["offline"] = True
        _state["at"] = time.time() - REFRESH_SEC + 15      # retry in 15 s
        return
    if code == 401:
        _sign_out()
    elif code == 200:
        with _lock:
            _state.update(user=data["user"], ent=data["ent"], at=time.time(), offline=False)
            _save()


# ---------- the interface app.py and editor_routes.py use ----------

def current_user():
    return _state["user"] if _state["token"] else None


def uid():
    u = current_user()
    return u["id"] if u else None


def can_access(owner):
    """The signed-in user's own things (and, for an admin, things made before accounts existed)."""
    u = current_user()
    return bool(u) and (owner == u["id"] or bool(u.get("is_admin")))


def ent():
    e = _state["ent"] if current_user() else None
    return dict(e, offline=_state["offline"]) if e else None


def charge(keys):
    """Charge clips to the plan: (True, n) or (False, message)."""
    token = _state["token"]
    if not token:
        return False, "Log in to continue."
    try:
        code, data = _call("POST", "charge", {"keys": list(keys)}, token=token)
    except CloudError as e:
        _state["offline"] = True
        return False, Offline(str(e))
    if code == 401:
        _sign_out()
        return False, "Your session has ended. Log in again."
    if data.get("ent"):
        with _lock:
            _state.update(ent=data["ent"], at=time.time(), offline=False)
            _save()
    return (True, data.get("charged", 0)) if code == 200 else (False, data.get("error") or "Couldn't check your plan.")


def refunder():
    """refund(keys), callable later from a background thread (gives clips back when work fails)."""
    token = _state["token"]

    def refund(keys):
        for attempt in range(3):
            try:
                code, data = _call("POST", "refund", {"keys": list(keys)}, token=token)
                if code == 200 and token == _state["token"]:
                    with _lock:
                        _state.update(ent=data["ent"], at=time.time())
                return
            except CloudError:
                time.sleep(5 * (attempt + 1))
        print(f"[recaply] couldn't give back clips {keys}: the online service is unreachable")
    return refund


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def device_label():
    return f"{platform.node() or 'PC'} (Windows app {VERSION})"


# ---------- wiring ----------

def _secret_key():
    path = os.path.join(APP_DIR, ".secret_key")
    try:
        with open(path, encoding="ascii") as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if len(key) < 32:
        os.makedirs(APP_DIR, exist_ok=True)
        key = secrets.token_hex(32)
        with open(path, "w", encoding="ascii") as f:
            f.write(key)
    return key


def _local_host(host):
    name, _, port = (host or "").rpartition(":")
    return name in ("127.0.0.1", "localhost") and (PORT["n"] is None or port == str(PORT["n"]))


def init_app(app):
    _load()
    app.secret_key = _secret_key()
    app.config.update(SESSION_COOKIE_NAME="recaply_app", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", PERMANENT_SESSION_LIFETIME=timedelta(days=365))
    app.register_blueprint(bp)

    @app.before_request
    def gate():
        if not _local_host(request.host):
            return "Clipforge only answers its own window.", 403
        path = request.path
        if path in ("/__open", "/__show", "/__web", "/__ping"):      # each checks its own key or code
            return None
        if not hmac.compare_digest(request.cookies.get(LAUNCH_COOKIE, ""), LAUNCH_KEY):
            return ("Open Clipforge from the Start menu.", 403) if request.method in SAFE_METHODS else (
                jsonify({"error": "Restart Clipforge and try again."}), 403)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                return jsonify({"error": "Blocked a request from another site."}), 403
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not token or not hmac.compare_digest(str(token), session.get("csrf", "")):
                msg = "Your session expired. Reload the page and try again."
                if path.startswith("/api/") or request.headers.get("X-CSRF-Token") is not None:
                    return jsonify({"error": msg}), 400
                flash(msg, "error")
                return redirect(request.path)
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            return None
        if request.method == "GET" and not path.startswith(("/api/", "/clips/")):
            refresh()                       # pages re-check the plan; polling APIs don't
        elif request.method == "POST" and path in CREATE_PATHS:
            refresh(force=True)
        if current_user():
            return None
        if path.startswith("/api/") or request.method not in SAFE_METHODS:
            return jsonify({"error": "Log in to continue.", "login": url_for("auth.login")}), 401
        return redirect(url_for("auth.login"))

    @app.context_processor
    def template_globals():
        return {"user": current_user(), "ent": ent(), "csrf_token": csrf_token, "plans": accounts.PLANS,
                "billing": "desktop", "asset_ver": _asset_version(), "desktop": True, "web_tab": _in_web_tab()}


_ver = {"t": 0, "v": "0"}


def _asset_version():
    now = time.time()
    if now - _ver["t"] > 2:
        newest = 0
        for root, _, files in os.walk(os.path.join(BASE_DIR, "static")):
            for f in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
        _ver.update(t=now, v=str(int(newest)))
    return _ver["v"]


# ---------- pages ----------

@bp.route("/__open")
def open_window():
    """The window's first page: proves it was opened by desktop.py, then shows the app."""
    if not hmac.compare_digest(request.args.get("k", ""), LAUNCH_KEY):
        return "Open Clipforge from the Start menu.", 403
    resp = make_response(redirect(url_for("app_home") if current_user() else url_for("auth.login")))
    resp.set_cookie(LAUNCH_COOKIE, LAUNCH_KEY, httponly=True, samesite="Strict")
    return resp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user() and request.method == "GET":
        return redirect(url_for("app_home"))
    error, email = None, ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()[:254]
        try:
            code, data = _call("POST", "login", {"email": email, "password": request.form.get("password") or "",
                                                  "device": device_label()})
        except CloudError as e:
            code, data = 0, {"error": str(e)}
        if code == 200:
            _signed_in(data)
            session.permanent = True
            return redirect(url_for("app_home"))
        error = data.get("error") or "Couldn't log in. Try again."
    return render_template("auth.html", mode="login", error=error, email=email, next=""), (401 if error else 200)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user() and request.method == "GET":
        return redirect(url_for("app_home"))
    error, form = None, {"email": "", "name": ""}
    if request.method == "POST":
        form = {"email": (request.form.get("email") or "").strip()[:254], "name": (request.form.get("name") or "")[:60]}
        try:
            code, data = _call("POST", "signup", {**form, "password": request.form.get("password") or "",
                                                   "device": device_label()})
        except CloudError as e:
            code, data = 0, {"error": str(e)}
        if code == 200:
            _signed_in(data)
            session.permanent = True
            flash("Welcome to Clipforge! Your 5-day free trial has started.", "ok")
            return redirect(url_for("app_home"))
        error = data.get("error") or "Couldn't create the account. Try again."
    return render_template("auth.html", mode="signup", error=error, form=form, plan=""), (400 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    token = _state["token"]
    _sign_out()
    if token:
        threading.Thread(target=lambda: _quiet(_call, "POST", "logout", token=token), daemon=True).start()
    return redirect(f"{CLOUD_URL}/" if _in_web_tab() else url_for("auth.login"))


def _quiet(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _in_web_tab():
    """True in a browser tab opened from the website's web app (not the Clipforge window)."""
    return request.cookies.get(WEB_COOKIE) == "1"


@bp.route("/__ping", methods=["GET", "OPTIONS"])
def ping():
    """Lets the website's web app find this app. Only the website's pages may read the answer."""
    resp = jsonify({"app": "recaply", "version": VERSION}) if request.method == "GET" else make_response("", 204)
    if request.headers.get("Origin") == WEBSITE_ORIGIN:
        resp.headers["Access-Control-Allow-Origin"] = WEBSITE_ORIGIN
        resp.headers["Access-Control-Allow-Private-Network"] = "true"     # Chrome's local network check
        resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/__web")
def open_web():
    """The web app's first page in the browser: trades the website's one-time code for this
    app's sign-in (same account as the website), then opens Clipforge in the tab."""
    code = request.args.get("c", "")[:600]
    try:
        status, data = _call("POST", "connect", {"code": code, "device": device_label()}, timeout=15)
    except CloudError as e:
        status, data = 0, {"error": str(e)}
    if status != 200:
        msg = data.get("error") or "Couldn't connect to your account."
        return render_template("web_bounce.html", error=msg, website=f"{CLOUD_URL}/app"), 400
    old = _state["token"]
    switched = bool(_state["user"]) and _state["user"]["id"] != data["user"]["id"]
    _signed_in(data)
    if old:          # the earlier sign-in of this app is replaced by this one
        threading.Thread(target=lambda: _quiet(_call, "POST", "logout", token=old), daemon=True).start()
    session.permanent = True
    if switched:
        flash(f"Signed in as {data['user']['email']}, like on the website.", "ok")
    # A page of our own moves on to the app: a redirect would still count as coming from the
    # website, and the browser would hold back the SameSite=Strict cookies set here.
    resp = make_response(render_template("web_bounce.html", error=None, target=url_for("app_home")))
    resp.set_cookie(LAUNCH_COOKIE, LAUNCH_KEY, httponly=True, samesite="Strict")
    resp.set_cookie(WEB_COOKIE, "1", httponly=True, samesite="Strict", max_age=365 * 86400)
    return resp


def _open_website(page):
    """Open a page of the website in the user's browser, signed in to the same account."""
    url = f"{CLOUD_URL}{page}"
    try:
        code, data = _call("POST", "handoff", {"next": page}, token=_state["token"], timeout=10)
        if code == 200:
            url = data["url"]
    except CloudError:
        pass
    webbrowser.open(url)


def _back():
    ref = urlparse(request.referrer or "")
    return redirect(ref.path if ref.path and _local_host(ref.netloc) else url_for("app_home"))


@bp.route("/pricing")
def pricing():
    if _in_web_tab():               # already in the browser, signed in on the website
        return redirect(f"{CLOUD_URL}/pricing")
    _open_website("/pricing")
    flash("Plans opened in your browser. Come back here when you're done: your plan updates by itself.", "ok")
    return _back()


@bp.route("/account")
def account():
    if _in_web_tab():
        return redirect(f"{CLOUD_URL}/account")
    _open_website("/account")
    flash("Your account opened in your browser.", "ok")
    return _back()


@bp.route("/api/plan")
def plan_status():
    """The current plan (re-checked now). The pages call this when the window gets focus."""
    refresh(force=True)
    return jsonify({"ent": ent(), "user": current_user()})


@bp.route("/website")
def website():
    if _in_web_tab():
        return redirect(f"{CLOUD_URL}/")
    _open_website("/")
    return _back()


@bp.route("/library")
def library():
    """Show the folder with every recap, clip and reel in File Explorer."""
    from config import CLIPS_DIR
    if os.name == "nt":
        os.startfile(CLIPS_DIR)  # noqa: S606 — opens Explorer on our own data folder
    return _back()
