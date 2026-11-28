"""
Shared plumbing for the publishers that post through a connected social
account (LinkedIn, X, Instagram): authorised HTTP calls with one token
refresh on 401, and the network's errors turned into PublishError
(rate limits, 5xx and network errors are retried by the scheduler).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from recaply.publish.base import Post, PublishError, Publisher


class Reply:
    def __init__(self, status: int, headers, body: bytes):
        self.status, self.headers, self.body = status, headers, body

    def json(self) -> dict:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class SocialPublisher(Publisher):
    network = ""
    auth_scheme = "Bearer"

    def __init__(self):
        from recaply.social import networks
        self.net = networks.get(self.network)
        self.key, self.label = self.net.key, self.net.label
        self._acct: int | None = None

    def status(self) -> dict:
        if not self.net.configured:
            return {"connected": False, "note": f"Add RECAPLY_{self.net.env}_CLIENT_ID and RECAPLY_{self.net.env}_CLIENT_SECRET, "
                                                "then connect an account on the Social page."}
        from recaply.social import accounts
        n = sum(1 for a in accounts.list_accounts(everyone=True) if a["network"] == self.key)
        return {"connected": n > 0, "note": self.net.note if n else "Connect an account on the Social page."}

    # ---------- auth ----------

    def _token(self, force: bool = False) -> str:
        from recaply.social.accounts import NeedsReconnect, access_token
        if not self._acct:
            raise PublishError(f"Pick a connected {self.label} account to post to.")
        try:
            return access_token(self._acct, force=force)
        except NeedsReconnect as e:
            raise PublishError(str(e)) from None
        except Exception as e:  # noqa: BLE001 — e.g. the network is down while refreshing
            raise PublishError(f"Couldn't refresh the {self.label} sign-in: {str(e)[:200]}", retryable=True) from None

    def _begin(self, post: Post) -> dict:
        """Checks before an upload; returns the account being posted as."""
        from recaply.social import accounts
        self._acct = post.account_id
        if not self.net.configured:
            raise PublishError(self.status()["note"])
        acc = accounts.get(self._acct) if self._acct else None
        if not acc or acc["network"] != self.network:
            raise PublishError(f"Pick a connected {self.label} account to post to.")
        if not post.file or not os.path.isfile(post.file):
            raise PublishError("The video file is gone.")
        return acc

    # ---------- HTTP ----------

    def _call(self, method: str, url: str, data: bytes | None = None, headers: dict | None = None,
              json_body=None, auth: bool = True, timeout: float = 120, _retry: bool = True) -> Reply:
        h = dict(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode()
            h.setdefault("Content-Type", "application/json")
        if auth:
            h["Authorization"] = f"{self.auth_scheme} {self._token()}"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Reply(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:800]
            if e.code == 401 and auth and _retry:
                self._token(force=True)
                return self._call(method, url, data, headers, None, auth, timeout, _retry=False)
            raise self._error(e.code, body.decode("utf-8", "replace"), e.headers) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise PublishError(f"network error: {getattr(e, 'reason', e)}", retryable=True) from None

    def _error(self, code: int, body: str, headers) -> PublishError:
        msg = body[:300]
        try:
            d = json.loads(body)
            msg = (d.get("detail") or d.get("message") or (d.get("error") or {}).get("message")
                   or d.get("title") or (d.get("errors") or [{}])[0].get("message") or msg)
        except (ValueError, AttributeError, IndexError, TypeError):
            pass
        if code == 429:
            return PublishError(f"{self.label} rate limit reached: {msg}", retryable=True)
        if code == 401:
            return PublishError(f"{self.label} no longer accepts this sign-in. Reconnect the account on the Social page.")
        if code == 403:
            return PublishError(f"{self.label} refused the post: {msg}")
        return PublishError(f"{self.label} said HTTP {code}: {msg}", retryable=code >= 500)

    @staticmethod
    def _wait(check, what: str, timeout: float = 600, every: float = 5):
        """Poll `check()` until it returns something truthy (it raises PublishError on failure)."""
        end = time.time() + timeout
        while time.time() < end:
            out = check()
            if out:
                return out
            time.sleep(every)
        raise PublishError(f"{what} took too long.", retryable=True)
