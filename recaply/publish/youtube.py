"""
YouTube Shorts through the official YouTube Data API v3.

Setup (once): create an OAuth client of type "Desktop app" in Google Cloud
Console, enable the YouTube Data API, and put its id and secret in
RECAPLY_YT_CLIENT_ID / RECAPLY_YT_CLIENT_SECRET. Then `recaply publish auth
youtube` opens the consent page and stores a refresh token in
`<data dir>/_publish/youtube.json` (the file is private to your user).

Uploads use the resumable protocol in 8 MiB chunks. A vertical video of at
most 3 minutes is a Short automatically; "#Shorts" is added to the
description to make that explicit. Videos from an API project that hasn't
passed Google's audit are locked to private until it has; the default quota
(10,000 units, an upload costs 1,600) allows about 6 uploads a day.
"""

from __future__ import annotations

import base64
import hashlib
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
from recaply.publish.base import Post, PublishError, Publisher, Result
from recaply.settings import settings

log = get_logger("publish.youtube")
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CHUNK = 8 * 1024 * 1024
PRIVACY = ("public", "unlisted", "private")


def _store_path() -> str:
    return os.path.join(settings.data_dir, "_publish", "youtube.json")


def _read_tokens() -> dict:
    try:
        with open(_store_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_tokens(d: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def disconnect() -> bool:
    try:
        os.remove(_store_path())
        return True
    except OSError:
        return False


def _form(url: str, data: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        raise PublishError(f"Google sign-in failed (HTTP {e.code}): {body}", retryable=e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise PublishError(f"network error: {getattr(e, 'reason', e)}", retryable=True) from None


def shorts_text(post: Post) -> tuple[str, str, list[str]]:
    """(title, description, tags) within YouTube's limits, marked as a Short."""
    title = " ".join(post.title.split())[:100] or "Clip"
    desc = post.description.strip()
    if "#shorts" not in (title + " " + desc).lower():
        desc = (desc + "\n\n#Shorts").strip()
    tags = []
    for t in post.tags:
        t = t.lstrip("#").strip()
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    while sum(len(t) + 1 for t in tags) > 450:        # the API caps the tag list at 500 characters
        tags.pop()
    return title, desc[:4900], tags


class YouTubePublisher(Publisher):
    key = "youtube"
    label = "YouTube Shorts"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 auth_url="https://accounts.google.com/o/oauth2/v2/auth",
                 token_url="https://oauth2.googleapis.com/token",
                 api_base="https://www.googleapis.com"):
        self.client_id = client_id if client_id is not None else os.environ.get("RECAPLY_YT_CLIENT_ID", "").strip()
        self.client_secret = client_secret if client_secret is not None else os.environ.get("RECAPLY_YT_CLIENT_SECRET", "").strip()
        self.auth_endpoint, self.token_url, self.api_base = auth_url, token_url, api_base.rstrip("/")

    # ---------- connection ----------

    def status(self) -> dict:
        if not (self.client_id and self.client_secret):
            return {"connected": False, "note": "Set RECAPLY_YT_CLIENT_ID and RECAPLY_YT_CLIENT_SECRET (a Google 'Desktop app' OAuth client), then run: python -m recaply publish auth youtube"}
        if not _read_tokens().get("refresh_token"):
            return {"connected": False, "note": "Not signed in yet: python -m recaply publish auth youtube"}
        return {"connected": True, "note": "Signed in. Uploads count against your YouTube API quota (about 6 a day by default)."}

    def authorize(self, open_browser=None, timeout: float = 300.0) -> None:
        """Run the loopback sign-in: a local page catches Google's redirect and the code is exchanged for tokens."""
        if not (self.client_id and self.client_secret):
            raise PublishError("Set RECAPLY_YT_CLIENT_ID and RECAPLY_YT_CLIENT_SECRET first.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        got: dict = {}
        done = threading.Event()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] == state and ("code" in q or "error" in q):
                    got.update({k: v[0] for k, v in q.items()})
                    msg = "Clipforge is connected to YouTube. You can close this tab." if "code" in q else "Sign-in was cancelled."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(msg.encode())
                    done.set()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        redirect = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = self.auth_endpoint + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        try:
            (open_browser or _open)(url)
            if not done.wait(timeout):
                raise PublishError("Sign-in timed out.")
        finally:
            srv.shutdown()
        if "code" not in got:
            raise PublishError("Sign-in was cancelled.")
        tok = _form(self.token_url, {"code": got["code"], "client_id": self.client_id, "client_secret": self.client_secret,
                                     "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
        if not tok.get("refresh_token"):
            raise PublishError("Google didn't return a refresh token. Remove Clipforge's access in your Google account and try again.")
        _write_tokens({"refresh_token": tok["refresh_token"], "access_token": tok.get("access_token"),
                       "expires": time.time() + float(tok.get("expires_in", 3000)) - 60})

    def _access_token(self, force: bool = False) -> str:
        if getattr(self, "_acct", None):
            from recaply.social.accounts import NeedsReconnect, access_token
            try:
                return access_token(self._acct, force=force)
            except NeedsReconnect as e:
                raise PublishError(str(e)) from None
        t = _read_tokens()
        if not t.get("refresh_token"):
            raise PublishError("YouTube isn't connected: python -m recaply publish auth youtube")
        if not force and t.get("access_token") and t.get("expires", 0) > time.time():
            return t["access_token"]
        tok = _form(self.token_url, {"client_id": self.client_id, "client_secret": self.client_secret,
                                     "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
        t.update(access_token=tok["access_token"], expires=time.time() + float(tok.get("expires_in", 3000)) - 60)
        _write_tokens(t)
        return t["access_token"]

    # ---------- upload ----------

    def _request(self, url, method, data=None, headers=None, retry_auth=True, timeout=120):
        h = {"Authorization": "Bearer " + self._access_token(), **(headers or {})}
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 308:
                return e                                    # "resume incomplete" is a normal answer for chunks
            if e.code == 401 and retry_auth:
                self._access_token(force=True)
                return self._request(url, method, data, headers, retry_auth=False, timeout=timeout)
            body = e.read()[:500].decode("utf-8", "replace")
            reason = ""
            try:
                reason = json.loads(body)["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise PublishError("YouTube's daily upload quota is used up. It resets at midnight Pacific time.") from None
            if e.code in (403, 400) and "uploadLimitExceeded" in body:
                raise PublishError("This channel reached YouTube's upload limit for now.") from None
            raise PublishError(f"YouTube said HTTP {e.code}: {reason or body[:200]}",
                               retryable=e.code in (429, 500, 502, 503, 504)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise PublishError(f"network error: {getattr(e, 'reason', e)}", retryable=True) from None

    def publish(self, post: Post, progress=None) -> Result:
        self._acct = post.account_id
        if self._acct:
            if not (self.client_id and self.client_secret):
                raise PublishError("YouTube isn't set up: add RECAPLY_YT_CLIENT_ID and RECAPLY_YT_CLIENT_SECRET.")
        elif not self.status()["connected"]:
            raise PublishError(self.status()["note"])
        if not os.path.isfile(post.file):
            raise PublishError("The video file is gone.")
        size = os.path.getsize(post.file)
        title, desc, tags = shorts_text(post)
        meta = {"snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "20"},
                "status": {"privacyStatus": post.privacy if post.privacy in PRIVACY else "public",
                           "selfDeclaredMadeForKids": False}}
        start = self._request(f"{self.api_base}/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", "POST",
                              json.dumps(meta).encode(),
                              {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
                               "X-Upload-Content-Length": str(size)})
        session = start.headers.get("Location")
        if not session:
            raise PublishError("YouTube didn't start an upload session.", retryable=True)
        sent = 0
        with open(post.file, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                rng = f"bytes {sent}-{end}/{size}" if chunk else f"bytes */{size}"
                r = self._request(session, "PUT", chunk, {"Content-Length": str(len(chunk)), "Content-Range": rng,
                                                          "Content-Type": "video/mp4"}, timeout=300)
                code = getattr(r, "status", None) or getattr(r, "code", 0)
                if code in (200, 201):
                    body = json.loads(r.read() or b"{}")
                    vid = body.get("id")
                    if not vid:
                        raise PublishError("YouTube accepted the upload but returned no video id.", retryable=True)
                    note = self._set_thumbnail(vid, post.thumbnail) if post.thumbnail else ""
                    if progress:
                        progress(1.0)
                    return Result(vid, f"https://youtu.be/{vid}", note)
                if code != 308:
                    raise PublishError(f"unexpected answer from YouTube (HTTP {code})", retryable=True)
                sent += len(chunk)
                if progress:
                    progress(min(0.99, sent / max(1, size)))
                if not chunk:
                    raise PublishError("YouTube didn't finish the upload.", retryable=True)

    def _set_thumbnail(self, vid: str, path: str) -> str:
        """Custom thumbnail after the upload. A failure here doesn't fail the post (the video is already up)."""
        try:
            with open(path, "rb") as f:
                data = f.read()
            mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            self._request(f"{self.api_base}/upload/youtube/v3/thumbnails/set?videoId={urllib.parse.quote(vid)}", "POST",
                          data, {"Content-Type": mime, "Content-Length": str(len(data))})
            return ""
        except (OSError, PublishError) as e:
            log.warning("thumbnail not set", video=vid, error=str(e)[:200])
            return f"Video posted, but the thumbnail wasn't set: {str(e)[:160]} (custom thumbnails need a verified channel)."


def _open(url: str) -> None:
    import webbrowser
    if not webbrowser.open(url):
        print("Open this page to sign in:\n" + url)
