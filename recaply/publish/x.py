"""
X (Twitter) video posts through the v2 API.

Chunked media upload (initialize -> append parts -> finalize -> wait while
X processes the video), then a post with the media attached. Posting needs
a paid X API plan; on the free plan X answers 403 and the user is told so.
"""

from __future__ import annotations

import os
import uuid

from recaply.publish.base import Post, PublishError, Result
from recaply.publish.social_base import SocialPublisher

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024


def fit(text: str, limit: int = 280) -> str:
    """X counts every link as 23 characters; trim on a word boundary so the post is accepted."""
    text = " ".join((text or "").split()) if "\n" not in (text or "") else (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return (cut[: cut.rfind(" ")] if " " in cut[limit // 2:] else cut).rstrip() + "…"


class XPublisher(SocialPublisher):
    network = "x"

    def _multipart(self, fields: dict, blob: bytes) -> tuple[bytes, str]:
        b = "----clipforge" + uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="media"; filename="blob"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + blob + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        return b"".join(parts), f"multipart/form-data; boundary={b}"

    def publish(self, post: Post, progress=None) -> Result:
        self._begin(post)
        size = os.path.getsize(post.file)
        init = self._call("POST", f"{API}/media/upload/initialize", json_body={
            "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"}).json().get("data") or {}
        mid = init.get("id") or init.get("media_id_string")
        if not mid:
            raise PublishError("X didn't start the upload.", retryable=True)
        with open(post.file, "rb") as f:
            i = 0
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                body, ctype = self._multipart({"segment_index": i}, chunk)
                self._call("POST", f"{API}/media/upload/{mid}/append", body, {"Content-Type": ctype}, timeout=300)
                i += 1
                if progress:
                    progress(0.8 * min(1.0, f.tell() / max(1, size)))
        fin = self._call("POST", f"{API}/media/upload/{mid}/finalize").json().get("data") or {}

        def ready():
            info = (fin if ready.first else
                    (self._call("GET", f"{API}/media/upload?media_id={mid}&command=STATUS").json().get("data") or {}))
            ready.first = False
            p = info.get("processing_info") or {}
            if not p or p.get("state") == "succeeded":
                return True
            if p.get("state") == "failed":
                raise PublishError("X couldn't process the video: " + str((p.get("error") or {}).get("message", "")))
            return False
        ready.first = True
        self._wait(ready, "X's video processing", every=5)
        text = fit(post.description or post.title, self.net.caption_max)
        r = self._call("POST", f"{API}/tweets", json_body={"text": text, "media": {"media_ids": [str(mid)]}}).json()
        tid = (r.get("data") or {}).get("id")
        if not tid:
            raise PublishError("X accepted the video but didn't create the post.", retryable=True)
        if progress:
            progress(1.0)
        return Result(tid, f"https://x.com/i/web/status/{tid}")

    def _error(self, code, body, headers):
        if code == 403 and ("client-not-enrolled" in body or "Client Forbidden" in body or "access level" in body.lower()):
            return PublishError("Your X developer app can't post on its current plan. Posting videos needs a paid X API "
                                "plan (Basic or higher).")
        return super()._error(code, body, headers)
