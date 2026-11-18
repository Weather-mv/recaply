"""
accounts.py — users, plans and usage, stored in SQLite (<data dir>/recaply.db).

Plans (PLANS below):
  free     5-day trial, 5 clips in total, watermark on reels and exports.
  starter  $9/month, 30 clips a month, no watermark, full editor, AI captions.
  pro      $19/month, unlimited clips, fastest processing (6 downloads at once).

A "clip" is one highlight downloaded from a recap or one editor project
exported. Each is charged once: downloading the same round again or
re-exporting the same project is free. Reels are made from clips that were
already charged, so they don't count.

Passwords are hashed with scrypt (werkzeug). The first account created on an
install becomes its admin: it sees recaps made before accounts existed and
has unlimited use.

Payments use Stripe Checkout when STRIPE_SECRET_KEY is set (plain HTTPS
calls, no SDK). Without it, a server that only listens on this computer
switches plans straight away so everything can be tried; a public server
without Stripe can't change plans.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from werkzeug.security import check_password_hash, generate_password_hash

from config import CLIPS_DIR, LOCAL_ONLY

DB_PATH = os.path.join(CLIPS_DIR, "recaply.db")
DAY = 86400
TRIAL_DAYS = 5

PLANS = {
    "free": {
        "name": "Free", "price": 0, "clips": 5, "watermark": True, "priority": 0, "parallel": 2,
        "blurb": "Try Recaply for 5 days.",
        "features": ["5 clips during the 5-day trial", "Round detection, ACE and clutch filters",
                     "AI captions included", "9:16 reels with a Recaply watermark"],
    },
    "starter": {
        "name": "Starter", "price": 9, "clips": 30, "watermark": False, "priority": 1, "parallel": 3,
        "blurb": "For creators posting every week.",
        "features": ["30 clips every month", "No watermark", "Full editing panel and timeline",
                     "AI captions (Whisper large-v3)", "POV reels with the kill feed"],
    },
    "pro": {
        "name": "Pro", "price": 19, "clips": None, "watermark": False, "priority": 2, "parallel": 6,
        "blurb": "For teams and daily uploaders.",
        "features": ["Unlimited clips", "Fastest processing: 6 clips download at once",
                     "Everything in Starter"],
    },
}
PAID = ("starter", "pro")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PASSWORD_MIN = 8

STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICES = {"starter": os.environ.get("STRIPE_PRICE_STARTER", ""), "pro": os.environ.get("STRIPE_PRICE_PRO", "")}


def billing_mode():
    """'stripe', 'local' (plans switch instantly, no payment) or 'off'."""
    if STRIPE_KEY and all(STRIPE_PRICES.values()):
        return "stripe"
    return "local" if LOCAL_ONLY else "off"


# ---------- database ----------

_local = threading.local()
_write = threading.Lock()


def db():
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        _local.con = con
    return con


def init():
    with _write, db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL DEFAULT '',
                pw_hash TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'free',
                plan_status TEXT NOT NULL DEFAULT 'trialing',
                trial_ends REAL NOT NULL,
                period_ends REAL,
                created REAL NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                session_ver INTEGER NOT NULL DEFAULT 1,
                stripe_customer TEXT,
                stripe_sub TEXT
            );
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                period TEXT NOT NULL,
                clips INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, period)
            );
            CREATE TABLE IF NOT EXISTS devices (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_ver INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL,
                last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS charged (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                at REAL NOT NULL,
                PRIMARY KEY (user_id, key)
            );
        """)


def _row(r):
    return dict(r) if r else None


def get(uid):
    try:
        return _row(db().execute("SELECT * FROM users WHERE id = ?", (int(uid),)).fetchone())
    except (TypeError, ValueError):
        return None


def by_email(email):
    return _row(db().execute("SELECT * FROM users WHERE email = ?", (email.strip(),)).fetchone())


def count_users():
    return db().execute("SELECT COUNT(*) FROM users").fetchone()[0]


def clean_email(email):
    email = str(email or "").strip().lower()
    return email if EMAIL_RE.match(email) and len(email) <= 254 else None


def password_problem(pw):
    pw = str(pw or "")
    if len(pw) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(pw) > 200:
        return "That password is too long."
    if pw.isdigit() or pw.lower() in ("password", "password1", "12345678", "qwertyui", "recaply1"):
        return "That password is too easy to guess."
    return None


def create_user(email, name, password):
    """New account on the free trial. Raises ValueError with a message for the form."""
    email = clean_email(email)
    if not email:
        raise ValueError("Enter a valid email address.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    name = " ".join(str(name or "").split())[:60]
    now = time.time()
    with _write, db() as con:
        first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        admins = {e.strip().lower() for e in os.environ.get("RECAPLY_ADMIN_EMAILS", "").split(",") if e.strip()}
        try:
            cur = con.execute(
                "INSERT INTO users (email, name, pw_hash, trial_ends, created, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                (email, name, generate_password_hash(password), now + TRIAL_DAYS * DAY, now,
                 1 if first or email in admins else 0))
        except sqlite3.IntegrityError:
            raise ValueError("There's already an account with that email. Log in instead.") from None
    return get(cur.lastrowid)


_DUMMY_HASH = generate_password_hash("not-a-real-password")


def authenticate(email, password):
    user = by_email(clean_email(email) or "")
    # Hash even when there's no such user, so timing doesn't reveal which emails exist.
    ok = check_password_hash(user["pw_hash"] if user else _DUMMY_HASH, str(password or ""))
    return user if user and ok else None


def set_password(uid, password):
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    with _write, db() as con:
        con.execute("UPDATE users SET pw_hash = ?, session_ver = session_ver + 1 WHERE id = ?",
                    (generate_password_hash(password), uid))


def update(uid, **fields):
    allowed = {"name", "plan", "plan_status", "period_ends", "stripe_customer", "stripe_sub", "trial_ends"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    with _write, db() as con:
        con.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), uid))


def delete_user(uid):
    with _write, db() as con:
        con.execute("DELETE FROM users WHERE id = ?", (uid,))


# ---------- Windows app sign-ins ----------
# The app gets a random token at login; only its SHA-256 is stored. Changing
# the password (session_ver) signs every PC out.

def _token_hash(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def issue_token(user, label=""):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _write, db() as con:
        con.execute("INSERT INTO devices (token_hash, user_id, session_ver, label, created, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (_token_hash(token), user["id"], user["session_ver"],
                                                  str(label or "")[:80], now, now))
    return token


def user_for_token(token):
    if not token:
        return None
    r = db().execute("SELECT user_id, session_ver, last_seen FROM devices WHERE token_hash = ?",
                     (_token_hash(token),)).fetchone()
    user = get(r[0]) if r else None
    if not user or user["session_ver"] != r[1]:
        return None
    if time.time() - r[2] > 3600:
        with _write, db() as con:
            con.execute("UPDATE devices SET last_seen = ? WHERE token_hash = ?", (time.time(), _token_hash(token)))
    return user


def revoke_token(token):
    with _write, db() as con:
        con.execute("DELETE FROM devices WHERE token_hash = ?", (_token_hash(token),))


def public_user(user):
    """What the Windows app may know about the account."""
    return {k: user[k] for k in ("id", "email", "name", "plan", "plan_status", "created")} | {
        "is_admin": bool(user["is_admin"]), "has_billing": bool(user.get("stripe_customer"))}


# ---------- plans and usage ----------

def _period(plan_key):
    return "trial" if plan_key == "free" else time.strftime("%Y-%m")


def plan_of(user):
    """The plan the user gets right now (a cancelled plan lasts until its paid period ends)."""
    if not user:
        return "free"
    if user["is_admin"]:
        return "pro"
    plan = user["plan"] if user["plan"] in PLANS else "free"
    if plan in PAID and user["plan_status"] in ("canceled", "past_due") and (user["period_ends"] or 0) < time.time():
        return "free"
    return plan


def used(user, plan_key=None):
    plan_key = plan_key or plan_of(user)
    r = db().execute("SELECT clips FROM usage WHERE user_id = ? AND period = ?",
                     (user["id"], _period(plan_key))).fetchone()
    return r[0] if r else 0


def entitlements(user):
    """Everything the pages and limits need to know about the user's plan."""
    key = plan_of(user)
    plan = PLANS[key]
    n = used(user, key)
    out = {"plan": key, "name": plan["name"], "limit": plan["clips"], "used": n,
           "left": None if plan["clips"] is None else max(0, plan["clips"] - n),
           "watermark": plan["watermark"], "priority": plan["priority"], "parallel": plan["parallel"],
           "admin": bool(user["is_admin"]), "status": user["plan_status"], "can_create": True, "reason": ""}
    if key == "free":
        left_s = user["trial_ends"] - time.time()
        out["trial_days_left"] = max(0, int(-(-left_s // DAY)))
        if left_s <= 0:
            out.update(can_create=False, reason="Your 5-day free trial has ended. Pick a plan to keep making clips.")
        elif out["left"] == 0:
            out.update(can_create=False, reason="You've used the 5 free clips in your trial. Upgrade to make more.")
    elif out["left"] == 0:
        out.update(can_create=False, reason=f"You've used all {plan['clips']} clips this month. "
                                            "Upgrade to Pro for unlimited clips.")
    return out


def charge(user, keys):
    """
    Charge clips for `keys` (one per clip; already-charged keys are free).
    Returns (True, charged_count) or (False, message). Atomic, so two tabs
    can't both take the last clip.
    """
    ent = entitlements(user)
    if not ent["can_create"]:
        return False, ent["reason"]
    keys = list(dict.fromkeys(keys))
    with _write, db() as con:
        done = {r[0] for r in con.execute(
            f"SELECT key FROM charged WHERE user_id = ? AND key IN ({','.join('?' * len(keys))})",
            (user["id"], *keys))} if keys else set()
        new = [k for k in keys if k not in done]
        if not new:
            return True, 0
        period = _period(ent["plan"])
        row = con.execute("SELECT clips FROM usage WHERE user_id = ? AND period = ?", (user["id"], period)).fetchone()
        n = row[0] if row else 0
        if ent["limit"] is not None and n + len(new) > ent["limit"]:
            left = max(0, ent["limit"] - n)
            return False, (f"That's {len(new)} new clip{'s' if len(new) > 1 else ''}, but your plan has {left} left"
                           f"{' this month' if ent['plan'] != 'free' else ' in your trial'}. "
                           "Pick fewer clips or upgrade.")
        now = time.time()
        con.executemany("INSERT INTO charged (user_id, key, at) VALUES (?, ?, ?)", [(user["id"], k, now) for k in new])
        con.execute("INSERT INTO usage (user_id, period, clips) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, period) DO UPDATE SET clips = clips + excluded.clips",
                    (user["id"], period, len(new)))
    return True, len(new)


def refund(user, keys):
    """Give clips back when the work failed (download or export error)."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    with _write, db() as con:
        gone = con.execute(f"DELETE FROM charged WHERE user_id = ? AND key IN ({','.join('?' * len(keys))})",
                           (user["id"], *keys)).rowcount
        if gone:
            con.execute("UPDATE usage SET clips = MAX(0, clips - ?) WHERE user_id = ? AND period = ?",
                        (gone, user["id"], _period(plan_of(user))))


# ---------- Stripe ----------

def _form(params, prefix=""):
    out = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out += _form(v, key)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                out += _form(item, f"{key}[{i}]") if isinstance(item, dict) else [(f"{key}[{i}]", item)]
        elif v is not None:
            out.append((key, str(v).lower() if isinstance(v, bool) else str(v)))
    return out


def stripe(method, path, params=None):
    data = urllib.parse.urlencode(_form(params or {})).encode() if params else None
    req = urllib.request.Request(f"https://api.stripe.com/v1/{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {STRIPE_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message")
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"Stripe error {e.code}") from None


def checkout_url(user, plan, base):
    params = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICES[plan], "quantity": 1}],
        "success_url": f"{base}/account?upgraded={plan}",
        "cancel_url": f"{base}/pricing",
        "client_reference_id": str(user["id"]),
        "metadata": {"uid": str(user["id"]), "plan": plan},
        "subscription_data": {"metadata": {"uid": str(user["id"]), "plan": plan}},
        "allow_promotion_codes": True,
    }
    if user.get("stripe_customer"):
        params["customer"] = user["stripe_customer"]
    else:
        params["customer_email"] = user["email"]
    return stripe("POST", "checkout/sessions", params)["url"]


def portal_url(user, base):
    return stripe("POST", "billing_portal/sessions",
                  {"customer": user["stripe_customer"], "return_url": f"{base}/account"})["url"]


def verify_webhook(payload, header, tolerance=300):
    """The event from a signed Stripe webhook, or None if the signature is wrong or old."""
    if not STRIPE_WEBHOOK_SECRET or not header:
        return None
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", "0"))
    except ValueError:
        return None
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if abs(time.time() - ts) > tolerance or not any(hmac.compare_digest(expected, s) for s in sigs):
        return None
    return json.loads(payload)


def _plan_for_price(price_id):
    return next((k for k, v in STRIPE_PRICES.items() if v and v == price_id), None)


def handle_event(event):
    obj = (event.get("data") or {}).get("object") or {}
    kind = event.get("type")
    if kind == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        uid, plan = meta.get("uid") or obj.get("client_reference_id"), meta.get("plan")
        if get(uid) and plan in PAID:
            update(int(uid), plan=plan, plan_status="active", stripe_customer=obj.get("customer"),
                   stripe_sub=obj.get("subscription"))
    elif kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        r = db().execute("SELECT id FROM users WHERE stripe_sub = ? OR stripe_customer = ?",
                         (obj.get("id"), obj.get("customer"))).fetchone()
        if not r:
            return
        items = (obj.get("items") or {}).get("data") or []
        plan = _plan_for_price(((items[0] if items else {}).get("price") or {}).get("id")) or None
        status = obj.get("status") or "active"
        if kind.endswith("deleted"):
            status = "canceled"
        elif obj.get("cancel_at_period_end"):
            status = "canceled"
        fields = {"plan_status": "active" if status in ("active", "trialing") else status,
                  "period_ends": obj.get("current_period_end") or (items[0].get("current_period_end") if items else None),
                  "stripe_sub": obj.get("id")}
        if plan:
            fields["plan"] = plan
        update(r[0], **fields)
