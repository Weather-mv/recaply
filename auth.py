"""
auth.py — the online service's sign up, log in, account page, plans and
billing, plus the security every request goes through (the Windows app uses
desktop_auth.py instead, which signs in through cloud_api.py):

  * Every page except the website, pricing and the login pages needs an
    account. Pages redirect to the login; API calls get 401.
  * Sessions are signed cookies (HttpOnly, SameSite=Lax, Secure behind
    HTTPS). Changing the password logs out every other session.
  * Every POST / PUT / DELETE needs the session's CSRF token (sent as the
    X-CSRF-Token header by static/js/recaply.js, or a hidden form field) and
    a matching Origin when the browser sends one.
  * Login and sign-up attempts are rate limited per address and per email.
"""

import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)

import accounts
from config import BASE_DIR, CLIPS_DIR, PUBLIC_URL, SECURE_COOKIES

bp = Blueprint("auth", __name__)

PUBLIC_PATHS = {"/", "/pricing", "/login", "/signup", "/healthz", "/billing/webhook", "/favicon.ico", "/robots.txt",
                "/download", "/auth/handoff"}
# The Windows app's API signs requests with a token instead of the cookie session.
TOKEN_PREFIX = "/api/v1/"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ON_DELETE = []          # callbacks(uid) that remove a deleted user's recaps and projects


def secret_key():
    """RECAPLY_SECRET_KEY, or a random key kept in the data folder (made on first run)."""
    key = os.environ.get("RECAPLY_SECRET_KEY")
    if key:
        return key
    path = os.path.join(CLIPS_DIR, ".secret_key")
    try:
        with open(path, encoding="ascii") as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if len(key) < 32:
        key = secrets.token_hex(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(key)
    return key


# ---------- the signed-in user ----------

def current_user():
    if "user" not in g:
        u = accounts.get(session.get("uid")) if session.get("uid") else None
        if u and session.get("sv") != u["session_ver"]:
            u = None                          # password changed since this session started
        g.user = u
    return g.user


def uid():
    u = current_user()
    return u["id"] if u else None


def can_access(owner):
    """True if the signed-in user may see something owned by `owner` (None = made before accounts)."""
    u = current_user()
    return bool(u) and (owner == u["id"] or bool(u["is_admin"]))


def ent():
    u = current_user()
    if not u:
        return None
    if "ent" not in g:
        g.ent = accounts.entitlements(u)
    return g.ent


def charge(keys):
    """Charge clips to the signed-in user's plan: (True, n) or (False, message)."""
    return accounts.charge(current_user(), keys)


def refunder():
    """refund(keys) for the signed-in user, callable later from a background thread
    (gives clips back when a download or export fails)."""
    user = current_user()
    return lambda keys: accounts.refund(user, keys)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def login_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["sv"] = user["session_ver"]
    csrf_token()


def safe_next(target):
    target = str(target or "")
    p = urlparse(target)
    return target if target.startswith("/") and not target.startswith("//") and not p.netloc and "\\" not in target else None


def base_url():
    return PUBLIC_URL or request.host_url.rstrip("/")


# ---------- rate limiting ----------

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def limited(key, limit, window):
    """True if `key` was used `limit` times in the last `window` seconds (and records this use)."""
    now = time.time()
    with _hits_lock:
        q = _hits[key]
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        if len(_hits) > 20000:                # forget idle keys
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 3600]:
                _hits.pop(k, None)
    return False


TRUST_PROXY = os.environ.get("RECAPLY_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes", "on")


def client_ip():
    """Behind a host's proxy (PythonAnywhere...) remote_addr is the proxy, shared by every
    visitor; the real address is in X-Real-IP. Only trusted when RECAPLY_TRUST_PROXY is on,
    since anyone can send that header straight to an unproxied server."""
    if TRUST_PROXY:
        ip = request.headers.get("X-Real-IP", "").strip()
        if ip:
            return ip
    return request.remote_addr or "?"


# ---------- wiring ----------

def init_app(app):
    accounts.init()
    app.secret_key = secret_key()
    app.config.update(
        SESSION_COOKIE_NAME="recaply_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SECURE_COOKIES,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    app.register_blueprint(bp)

    @app.before_request
    def require_login():
        path = request.path
        if path.startswith(("/static/", TOKEN_PREFIX)) or path in PUBLIC_PATHS:
            return None
        if current_user():
            return None
        if path.startswith("/api/") or request.method not in SAFE_METHODS:
            return jsonify({"error": "Log in to continue.", "login": url_for("auth.login")}), 401
        nxt = request.full_path.rstrip("?") if request.query_string else path
        return redirect(url_for("auth.login", next=nxt))

    @app.before_request
    def check_csrf():
        if request.method in SAFE_METHODS or request.path == "/billing/webhook" or request.path.startswith(TOKEN_PREFIX):
            return None
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return _csrf_fail()
        # Header first: reading request.form would parse a big upload before its size limit.
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not token or not hmac.compare_digest(str(token), session.get("csrf", "")):
            return _csrf_fail()
        return None

    @app.context_processor
    def template_globals():
        return {"user": current_user(), "ent": ent(), "csrf_token": csrf_token, "plans": accounts.PLANS,
                "billing": accounts.billing_mode(), "asset_ver": asset_version()}


def _csrf_fail():
    msg = "Your session expired. Reload the page and try again."
    if request.path.startswith("/api/") or request.is_json or request.headers.get("X-CSRF-Token") is not None:
        return jsonify({"error": msg}), 400
    flash(msg, "error")
    return redirect(request.referrer if safe_next(urlparse(request.referrer or "").path) else url_for("auth.login"))


_ver = {"t": 0, "v": "0"}


def asset_version():
    """Cache-busting stamp for /static (newest file time, checked at most every 2 s)."""
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

@bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next")) or url_for("app_home")
    if current_user() and request.method == "GET":
        return redirect(nxt)
    error, email = None, ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()[:254]
        if limited(f"ip:{client_ip()}", 30, 900) or limited(f"login:{email.lower()}", 8, 900):
            error = "Too many attempts. Wait 15 minutes and try again."
        else:
            user = accounts.authenticate(email, request.form.get("password"))
            if user:
                login_session(user)
                return redirect(nxt)
            error = "That email and password don't match an account."
    return render_template("auth.html", mode="login", error=error, email=email, next=nxt), (401 if error else 200)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    plan = request.values.get("plan") if request.values.get("plan") in accounts.PAID else ""
    if current_user() and request.method == "GET":
        return redirect(url_for("auth.pricing") if plan else url_for("app_home"))
    error, form = None, {"email": "", "name": ""}
    if request.method == "POST":
        form = {"email": (request.form.get("email") or "").strip()[:254], "name": (request.form.get("name") or "")[:60]}
        if limited(f"signup:{client_ip()}", 6, 3600):
            error = "Too many new accounts from this network. Try again later."
        elif request.form.get("website"):            # honeypot field, hidden from people
            error = "Couldn't create the account."
        else:
            try:
                user = accounts.create_user(form["email"], form["name"], request.form.get("password"))
            except ValueError as e:
                error = str(e)
            else:
                login_session(user)
                flash("Welcome to Recaply! Your 5-day free trial has started.", "ok")
                return redirect(url_for("auth.pricing", pick=plan) if plan else url_for("app_home"))
    return render_template("auth.html", mode="signup", error=error, form=form, plan=plan), (400 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("landing"))


@bp.route("/pricing")
def pricing():
    return render_template("pricing.html", pick=request.args.get("pick") if request.args.get("pick") in accounts.PAID else "")


@bp.route("/account")
def account():
    u = current_user()
    return render_template("account.html", upgraded=request.args.get("upgraded") in accounts.PAID,
                           stripe_customer=bool(u.get("stripe_customer")))


@bp.route("/account/profile", methods=["POST"])
def account_profile():
    accounts.update(uid(), name=" ".join((request.form.get("name") or "").split())[:60])
    flash("Saved.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/account/password", methods=["POST"])
def account_password():
    u = current_user()
    if limited(f"pw:{u['id']}", 8, 900):
        flash("Too many attempts. Wait a few minutes.", "error")
    elif not accounts.authenticate(u["email"], request.form.get("current")):
        flash("Your current password isn't right.", "error")
    elif request.form.get("new") != request.form.get("confirm"):
        flash("The new passwords don't match.", "error")
    else:
        try:
            accounts.set_password(u["id"], request.form.get("new"))
        except ValueError as e:
            flash(str(e), "error")
        else:
            login_session(accounts.get(u["id"]))       # stay logged in here; other sessions end
            flash("Password changed. Other devices have been logged out.", "ok")
    return redirect(url_for("auth.account") + "#security")


@bp.route("/account/delete", methods=["POST"])
def account_delete():
    u = current_user()
    if not accounts.authenticate(u["email"], request.form.get("password")):
        flash("Your password isn't right, so nothing was deleted.", "error")
        return redirect(url_for("auth.account") + "#danger")
    for fn in ON_DELETE:
        fn(u["id"])
    accounts.delete_user(u["id"])
    session.clear()
    flash("Your account and everything in it has been deleted.", "ok")
    return redirect(url_for("landing"))


# ---------- billing ----------

@bp.route("/billing/checkout", methods=["POST"])
def billing_checkout():
    plan = request.form.get("plan")
    u = current_user()
    if plan not in accounts.PAID:
        abort(400)
    mode = accounts.billing_mode()
    if accounts.plan_of(u) == plan and u["plan_status"] == "active":
        flash(f"You're already on {accounts.PLANS[plan]['name']}.", "ok")
        return redirect(url_for("auth.account"))
    if mode == "stripe":
        if u.get("stripe_sub") and u["plan_status"] == "active":
            return redirect(accounts.portal_url(u, base_url()), 303)   # switch plans in Stripe's portal
        try:
            return redirect(accounts.checkout_url(u, plan, base_url()), 303)
        except RuntimeError as e:
            flash(f"Couldn't start the payment: {e}", "error")
            return redirect(url_for("auth.pricing"))
    if mode == "local":
        accounts.update(u["id"], plan=plan, plan_status="active", period_ends=time.time() + 30 * 86400)
        flash(f"You're on {accounts.PLANS[plan]['name']} now. (Payments aren't set up on this local "
              "server, so nothing was charged.)", "ok")
        return redirect(url_for("auth.account"))
    flash("Payments aren't set up on this server yet.", "error")
    return redirect(url_for("auth.pricing"))


@bp.route("/billing/manage", methods=["POST"])
def billing_manage():
    u = current_user()
    if accounts.billing_mode() == "stripe" and u.get("stripe_customer"):
        try:
            return redirect(accounts.portal_url(u, base_url()), 303)
        except RuntimeError as e:
            flash(f"Couldn't open billing: {e}", "error")
    elif accounts.billing_mode() == "local" and u["plan"] in accounts.PAID:
        accounts.update(u["id"], plan="free", plan_status="canceled", period_ends=None)
        flash("Your plan was cancelled.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    event = accounts.verify_webhook(request.get_data(), request.headers.get("Stripe-Signature"))
    if event is None:
        return jsonify({"error": "bad signature"}), 400
    accounts.handle_event(event)
    return jsonify({"ok": True})
