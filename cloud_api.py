"""
cloud_api.py — the online service's API for the Windows app (/api/v1).

The app signs in with email and password and gets a token (Bearer header on
every call). Videos never come here: the app only asks which plan the user
is on and charges clips against it before downloading or exporting.

  POST /api/v1/login    {email, password, device}  -> {token, user, ent}
  POST /api/v1/signup   {email, name, password, device} -> {token, user, ent}
  GET  /api/v1/me                                   -> {user, ent}
  POST /api/v1/charge   {keys: [...]}               -> {ok, charged, ent} | 402 {error, ent}
  POST /api/v1/refund   {keys: [...]}               -> {ok, ent}
  POST /api/v1/logout                               -> {ok}
  POST /api/v1/handoff  {next}                      -> {url}  (opens the website signed in)
  POST /api/v1/connect  {code, device}              -> {token, user, ent}  (the web app, see below)
  GET  /api/v1/version                              -> {version, download}

Plus /auth/handoff, which turns a handoff link into a website session.

The web app (/app on the website) runs on the Recaply helper on the user's
PC. The website makes a one-time connect code for the signed-in user
(connect_code); the browser takes it to the helper on 127.0.0.1, and the
helper trades it here for its own token, so it's signed in to the same account.
"""

import secrets
import threading
import time

from flask import Blueprint, current_app, jsonify, redirect, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import accounts
import auth
from config import VERSION

bp = Blueprint("api", __name__)
HANDOFF_SEC = 120


def _body():
    return request.get_json(force=True, silent=True) or {}


def _token():
    h = request.headers.get("Authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _user():
    return accounts.user_for_token(_token())


def _signed_in(user, label):
    return jsonify({"token": accounts.issue_token(user, label), "user": accounts.public_user(user),
                    "ent": accounts.entitlements(user)})


def _needs_login():
    return jsonify({"error": "Your session has ended. Log in again.", "login": True}), 401


@bp.post("/api/v1/login")
def api_login():
    b = _body()
    email = str(b.get("email") or "").strip()[:254]
    if auth.limited(f"ip:{auth.client_ip()}", 30, 900) or auth.limited(f"login:{email.lower()}", 8, 900):
        return jsonify({"error": "Too many attempts. Wait 15 minutes and try again."}), 429
    user = accounts.authenticate(email, b.get("password"))
    if not user:
        return jsonify({"error": "That email and password don't match an account."}), 401
    return _signed_in(user, b.get("device"))


@bp.post("/api/v1/signup")
def api_signup():
    b = _body()
    if auth.limited(f"signup:{auth.client_ip()}", 6, 3600):
        return jsonify({"error": "Too many new accounts from this network. Try again later."}), 429
    try:
        user = accounts.create_user(b.get("email"), b.get("name"), b.get("password"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return _signed_in(user, b.get("device"))


@bp.get("/api/v1/me")
def api_me():
    user = _user()
    if not user:
        return _needs_login()
    return jsonify({"user": accounts.public_user(user), "ent": accounts.entitlements(user)})


@bp.post("/api/v1/charge")
def api_charge():
    user = _user()
    if not user:
        return _needs_login()
    keys = [str(k)[:120] for k in (_body().get("keys") or [])][:500]
    ok, info = accounts.charge(user, keys)
    ent = accounts.entitlements(user)
    if not ok:
        return jsonify({"error": info, "upgrade": True, "ent": ent}), 402
    return jsonify({"ok": True, "charged": info, "ent": ent})


@bp.post("/api/v1/refund")
def api_refund():
    user = _user()
    if not user:
        return _needs_login()
    accounts.refund(user, [str(k)[:120] for k in (_body().get("keys") or [])][:500])
    return jsonify({"ok": True, "ent": accounts.entitlements(user)})


@bp.post("/api/v1/logout")
def api_logout():
    accounts.revoke_token(_token())
    return jsonify({"ok": True})


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="recaply-handoff")


@bp.post("/api/v1/handoff")
def api_handoff():
    """A one-time link that opens the website already signed in (plans, billing, account)."""
    user = _user()
    if not user:
        return _needs_login()
    nxt = auth.safe_next(_body().get("next")) or "/account"
    code = _serializer().dumps({"uid": user["id"], "sv": user["session_ver"], "next": nxt})
    return jsonify({"url": f"{auth.base_url()}/auth/handoff?c={code}"})


@bp.get("/auth/handoff")
def handoff():
    try:
        data = _serializer().loads(request.args.get("c", ""), max_age=HANDOFF_SEC)
    except BadSignature:
        return redirect(url_for("auth.login"))
    user = accounts.get(data.get("uid"))
    if not user or user["session_ver"] != data.get("sv"):
        return redirect(url_for("auth.login"))
    auth.login_session(user)
    return redirect(auth.safe_next(data.get("next")) or url_for("auth.account"))


CONNECT_SEC = 120
_used = {}                  # connect codes already traded: nonce -> expiry (each works once)
_used_lock = threading.Lock()


def connect_code(user):
    """A one-time code (2 minutes) that signs the user's Recaply helper in to this account."""
    return URLSafeTimedSerializer(current_app.secret_key, salt="recaply-connect").dumps(
        {"uid": user["id"], "sv": user["session_ver"], "n": secrets.token_urlsafe(12)})


@bp.post("/api/v1/connect")
def api_connect():
    b = _body()
    if auth.limited(f"connect:{auth.client_ip()}", 30, 900):
        return jsonify({"error": "Too many attempts. Wait 15 minutes and try again."}), 429
    try:
        data = URLSafeTimedSerializer(current_app.secret_key, salt="recaply-connect").loads(
            str(b.get("code") or "")[:600], max_age=CONNECT_SEC)
    except BadSignature:
        return jsonify({"error": "That link has expired. Open the web app from the website again."}), 400
    now = time.time()
    with _used_lock:
        for n in [n for n, t in _used.items() if t < now]:
            del _used[n]
        if data.get("n") in _used:
            return jsonify({"error": "That link was already used. Open the web app from the website again."}), 400
        _used[data.get("n")] = now + CONNECT_SEC
    user = accounts.get(data.get("uid"))
    if not user or user["session_ver"] != data.get("sv"):
        return jsonify({"error": "Log in on the website again."}), 401
    return _signed_in(user, b.get("device"))


@bp.get("/api/v1/version")
def api_version():
    return jsonify({"version": VERSION, "download": f"{auth.base_url()}/download"})
