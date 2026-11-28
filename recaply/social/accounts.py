"""
Connected social accounts: one row per account, owned by one Clipforge user.

Tokens are stored encrypted (see crypto.py) and never leave the server: the
API returns accounts without them. `access_token(id)` hands a publisher a
fresh access token, refreshing it first when it's about to expire; when the
network refuses the refresh, the account is marked "reconnect".
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from recaply.jobs import store
from recaply.log import get_logger
from recaply.social import crypto

log = get_logger("social.accounts")
_lock = threading.Lock()
_ready: set = set()
SCHEMA = """
create table if not exists social_accounts (
    id integer primary key autoincrement,
    owner text,
    network text not null,
    external_id text not null,
    name text not null default '',
    handle text not null default '',
    avatar text not null default '',
    scopes text not null default '',
    token_enc text not null,
    expires_at real not null default 0,
    status text not null default 'ok',
    error text,
    created real not null,
    updated real not null
);
create unique index if not exists social_accounts_one on social_accounts(owner, network, external_id);
"""
PUBLIC = ("id", "owner", "network", "external_id", "name", "handle", "avatar", "scopes", "expires_at", "status",
          "error", "created", "updated")


class NeedsReconnect(RuntimeError):
    """The network no longer accepts this account's sign-in: the user has to connect it again."""


def _con() -> sqlite3.Connection:
    from recaply.settings import settings
    con = store._db()
    if settings.jobs_db not in _ready:
        store.init()
        con.executescript(SCHEMA)
        _ready.add(settings.jobs_db)
    return con


def _public(r) -> dict:
    return {k: r[k] for k in PUBLIC}


def save(owner, network: str, prof: dict, token: dict, scopes: str = "") -> dict:
    """Add an account, or update its tokens and profile when the same account connects again."""
    now = time.time()
    enc = crypto.encrypt(json.dumps(token))
    expires = float(token.get("expires_at") or 0)
    with _lock, _con() as con:
        r = con.execute("select id from social_accounts where owner is ? and network=? and external_id=?",
                        (owner, network, prof["external_id"])).fetchone()
        if r:
            con.execute("update social_accounts set name=?, handle=?, avatar=?, scopes=?, token_enc=?, expires_at=?,"
                        " status='ok', error=null, updated=? where id=?",
                        (prof["name"], prof.get("handle", ""), prof.get("avatar", ""), scopes, enc, expires, now, r["id"]))
            aid = r["id"]
        else:
            aid = con.execute("insert into social_accounts (owner, network, external_id, name, handle, avatar, scopes,"
                              " token_enc, expires_at, created, updated) values (?,?,?,?,?,?,?,?,?,?,?)",
                              (owner, network, prof["external_id"], prof["name"], prof.get("handle", ""),
                               prof.get("avatar", ""), scopes, enc, expires, now, now)).lastrowid
        return _public(con.execute("select * from social_accounts where id=?", (aid,)).fetchone())


def list_accounts(owner=None, everyone: bool = False) -> list[dict]:
    with _lock, _con() as con:
        if everyone:
            rows = con.execute("select * from social_accounts order by network, name").fetchall()
        else:
            rows = con.execute("select * from social_accounts where owner is ? order by network, name", (owner,)).fetchall()
    return [_public(r) for r in rows]


def get(account_id: int) -> dict | None:
    with _lock, _con() as con:
        r = con.execute("select * from social_accounts where id=?", (int(account_id),)).fetchone()
    return _public(r) if r else None


def remove(account_id: int) -> bool:
    with _lock, _con() as con:
        return con.execute("delete from social_accounts where id=?", (int(account_id),)).rowcount > 0


def _token(account_id: int) -> dict:
    with _lock, _con() as con:
        r = con.execute("select token_enc from social_accounts where id=?", (int(account_id),)).fetchone()
    if not r:
        raise NeedsReconnect("That account was disconnected.")
    try:
        return json.loads(crypto.decrypt(r["token_enc"]))
    except (ValueError, OSError) as e:
        raise NeedsReconnect(f"The saved sign-in can't be read on this PC ({e}). Connect the account again.") from None


def _store_token(account_id: int, token: dict) -> None:
    with _lock, _con() as con:
        con.execute("update social_accounts set token_enc=?, expires_at=?, status='ok', error=null, updated=? where id=?",
                    (crypto.encrypt(json.dumps(token)), float(token.get("expires_at") or 0), time.time(), int(account_id)))


def mark(account_id: int, status: str, error: str | None = None) -> None:
    with _lock, _con() as con:
        con.execute("update social_accounts set status=?, error=?, updated=? where id=?",
                    (status, (error or "")[:300] or None, time.time(), int(account_id)))


def access_token(account_id: int, force: bool = False) -> str:
    """A usable access token: refreshed first when it expires within a few minutes (or `force`)."""
    acc = get(account_id)
    if not acc:
        raise NeedsReconnect("That account was disconnected.")
    tok = _token(account_id)
    exp = float(tok.get("expires_at") or 0)
    margin = 7 * 86400 if acc["network"] == "instagram" else 300     # Instagram tokens are refreshed well before day 60
    if not force and tok.get("access_token") and (exp == 0 or exp - margin > time.time()):
        return tok["access_token"]
    from recaply.social import oauth
    try:
        new = oauth.refresh(acc["network"], tok)
    except NeedsReconnect as e:
        mark(account_id, "reconnect", str(e))
        raise
    if new is None:                                    # nothing to refresh with, but the token still works
        if exp and exp <= time.time():
            mark(account_id, "reconnect", "The sign-in expired.")
            raise NeedsReconnect(f"Your {acc['network']} sign-in expired. Connect the account again.")
        return tok["access_token"]
    tok.update(new)
    _store_token(account_id, tok)
    return tok["access_token"]


def token_data(account_id: int) -> dict:
    """The stored token plus the account's network ids (for publishers that need e.g. the Instagram user id)."""
    acc = get(account_id)
    if not acc:
        raise NeedsReconnect("That account was disconnected.")
    return {**_token(account_id), "external_id": acc["external_id"], "network": acc["network"]}
