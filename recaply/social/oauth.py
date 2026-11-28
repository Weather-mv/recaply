"""
Connecting an account: the OAuth 2.0 sign-in.

`begin(owner, network)` makes a one-time `state` (and a PKCE pair where the
network supports it) and returns the network's sign-in page. The page is
opened in the user's own browser. The network then sends the browser back to
a small callback page served on a fixed local port
(RECAPLY_SOCIAL_CALLBACK_PORT, 47620 by default), separate from the app's own
server, which only answers its own window. That page swaps the code for tokens,
looks up who signed in, and saves the account. The studio polls
`status(state)` to learn when it's done.

Register this redirect URL in each network's developer app:
    http://127.0.0.1:47620/social/callback/<network>
On a hosted server (or behind an https tunnel, for networks that only accept
https) set RECAPLY_SOCIAL_REDIRECT_BASE to the server's public address: the
redirect then lands on the app's own /social/callback/<network> route.
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from recaply.log import get_logger
from recaply.social import accounts, networks
from recaply.social.accounts import NeedsReconnect

log = get_logger("social.oauth")
FLOW_TTL = 600                      # a sign-in must finish within 10 minutes
_flows: dict[str, dict] = {}        # state -> {owner, network, verifier, redirect, created, status, account, error}
_lock = threading.Lock()
_server: dict = {"srv": None, "port": None}


class OAuthError(RuntimeError):
    pass


def callback_port() -> int:
    try:
        return int(os.environ.get("RECAPLY_SOCIAL_CALLBACK_PORT", "47620"))
    except ValueError:
        return 47620


def redirect_uri(network: str) -> str:
    base = os.environ.get("RECAPLY_SOCIAL_REDIRECT_BASE", "").strip().rstrip("/")
    return f"{base or f'http://127.0.0.1:{callback_port()}'}/social/callback/{network}"


# ---------- token requests ----------

def _token_request(net: networks.Network, data: dict) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if net.basic_auth:
        cred = base64.b64encode(f"{net.client_id}:{net.client_secret}".encode()).decode()
        headers["Authorization"] = "Basic " + cred
        data = {**data, "client_id": net.client_id}
    else:
        data = {**data, "client_id": net.client_id, "client_secret": net.client_secret}
    req = urllib.request.Request(net.token_url, data=urllib.parse.urlencode(data).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        if e.code in (400, 401) and data.get("grant_type") == "refresh_token":
            raise NeedsReconnect(f"{net.label} no longer accepts this sign-in. Connect the account again.") from None
        raise OAuthError(f"{net.label} sign-in failed (HTTP {e.code}): {body}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OAuthError(f"network error: {getattr(e, 'reason', e)}") from None


def _normalise(tok: dict) -> dict:
    out = {"access_token": tok["access_token"]}
    if tok.get("refresh_token"):
        out["refresh_token"] = tok["refresh_token"]
    if tok.get("expires_in"):
        out["expires_at"] = time.time() + float(tok["expires_in"]) - 60
    return out


def _instagram_long_lived(net: networks.Network, short: str) -> dict:
    q = urllib.parse.urlencode({"grant_type": "ig_exchange_token", "client_secret": net.client_secret, "access_token": short})
    try:
        with urllib.request.urlopen("https://graph.instagram.com/access_token?" + q, timeout=30) as r:
            return _normalise(json.loads(r.read()))
    except urllib.error.HTTPError as e:
        raise OAuthError(f"Instagram sign-in failed (HTTP {e.code}): {e.read()[:200].decode('utf-8', 'replace')}") from None


def exchange(network: str, code: str, verifier: str | None, redirect: str) -> dict:
    net = networks.get(network)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect}
    if verifier:
        data["code_verifier"] = verifier
    tok = _token_request(net, data)
    if network == "instagram":
        return _instagram_long_lived(net, tok["access_token"])
    return _normalise(tok)


def refresh(network: str, tok: dict) -> dict | None:
    """New token fields, or None when this network gave no way to refresh (LinkedIn, usually)."""
    net = networks.get(network)
    if network == "instagram":
        q = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": tok["access_token"]})
        try:
            with urllib.request.urlopen("https://graph.instagram.com/refresh_access_token?" + q, timeout=30) as r:
                return _normalise(json.loads(r.read()))
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):
                raise NeedsReconnect("Instagram no longer accepts this sign-in. Connect the account again.") from None
            raise OAuthError(f"Instagram refresh failed (HTTP {e.code})") from None
    if not tok.get("refresh_token"):
        return None
    return _normalise(_token_request(net, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}))


# ---------- the flow ----------

def _gc() -> None:
    cut = time.time() - FLOW_TTL
    for k in [k for k, f in _flows.items() if f["created"] < cut]:
        _flows.pop(k, None)


def begin(owner, network: str) -> dict:
    net = networks.get(network)
    if net is None:
        raise OAuthError(f"Unknown network '{network}'.")
    if not net.configured:
        raise OAuthError(f"{net.label} isn't set up yet: add RECAPLY_{net.env}_CLIENT_ID and RECAPLY_{net.env}_CLIENT_SECRET "
                         "to the .env file (see the setup guide).")
    _ensure_server()
    state = secrets.token_urlsafe(24)
    redirect = redirect_uri(network)
    params = {"client_id": net.client_id, "redirect_uri": redirect, "response_type": "code",
              "scope": net.scope_sep.join(net.scopes), "state": state, **net.extra}
    verifier = None
    if net.pkce:
        verifier = secrets.token_urlsafe(64)
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params["code_challenge_method"] = "S256"
    with _lock:
        _gc()
        _flows[state] = {"owner": owner, "network": network, "verifier": verifier, "redirect": redirect,
                         "created": time.time(), "status": "pending", "account": None, "error": None}
    return {"state": state, "url": net.auth_url + "?" + urllib.parse.urlencode(params)}


def status(state: str, owner) -> dict | None:
    with _lock:
        f = _flows.get(state)
        if not f or f["owner"] != owner:
            return None
        return {"status": f["status"], "network": f["network"], "account": f["account"], "error": f["error"]}


def complete(network: str, query: dict) -> tuple[bool, str]:
    """Handle the network's redirect. Returns (ok, message for the browser tab)."""
    state = query.get("state", "")
    with _lock:
        f = _flows.get(state)
        if not f or f["network"] != network or f["status"] != "pending" or time.time() - f["created"] > FLOW_TTL:
            return False, "This sign-in link has expired. Go back to Clipforge and click Connect again."
        f["status"] = "working"
    try:
        if "error" in query or "code" not in query:
            raise OAuthError(query.get("error_description") or query.get("error") or "Sign-in was cancelled.")
        tok = exchange(network, query["code"], f["verifier"], f["redirect"])
        prof = networks.profile(network, tok)
        acc = accounts.save(f["owner"], network, prof, tok, " ".join(networks.get(network).scopes))
        with _lock:
            f.update(status="done", account=acc)
        log.info("account connected", network=network, account=acc["id"])
        return True, f"{networks.get(network).label} account “{prof['name']}” is connected to Clipforge. You can close this tab."
    except Exception as e:  # noqa: BLE001 — every failure is shown to the user
        msg = str(e)[:300] or "Sign-in failed."
        with _lock:
            f.update(status="failed", error=msg)
        log.warning("connect failed", network=network, error=msg[:200])
        return False, msg


# ---------- the callback page ----------

def page(ok: bool, msg: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><title>Clipforge</title>"
            f"<body style='font-family:system-ui,sans-serif;display:grid;place-items:center;height:90vh;margin:0'>"
            f"<div style='max-width:460px;text-align:center'><h2 style='color:{'#0865ff' if ok else '#d33'}'>"
            f"{'Connected' if ok else 'Not connected'}</h2><p style='font-size:17px'>{html.escape(msg)}</p></div>")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        parts = u.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["social", "callback"] or parts[2] not in networks.NETWORKS:
            self.send_response(404)
            self.end_headers()
            return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        ok, msg = complete(parts[2], q)
        body = page(ok, msg).encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _ensure_server() -> None:
    if os.environ.get("RECAPLY_SOCIAL_REDIRECT_BASE", "").strip():
        return                                   # the redirect lands on the app's own /social/callback route
    with _lock:
        if _server["srv"] is not None:
            return
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", callback_port()), _Handler)
        except OSError as e:
            raise OAuthError(f"Can't open the sign-in page on port {callback_port()} ({e}). "
                             "Close whatever uses it or set RECAPLY_SOCIAL_CALLBACK_PORT.") from None
        _server.update(srv=srv, port=srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True, name="social-callback").start()


def stop_server() -> None:
    with _lock:
        srv = _server["srv"]
        _server.update(srv=None, port=None)
    if srv is not None:
        srv.shutdown()
        srv.server_close()
