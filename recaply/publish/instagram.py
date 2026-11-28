"""
Instagram Reels through the Instagram API with Instagram Login.

Create a REELS container with upload_type=resumable, upload the file straight
from this PC to rupload.facebook.com (no public URL needed), wait until
Instagram has processed it, then publish. The cover is picked by time
(thumb_offset) because a custom cover image would need a public URL.
"""

from __future__ import annotations

import os
import urllib.parse

from recaply.publish.base import Post, PublishError, Result
from recaply.publish.social_base import SocialPublisher

VER = "v23.0"
GRAPH = f"https://graph.instagram.com/{VER}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VER}"


class InstagramPublisher(SocialPublisher):
    network = "instagram"

    def _q(self, **params) -> str:
        return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    def publish(self, post: Post, progress=None) -> Result:
        uid = self._begin(post)["external_id"]
        caption = (post.description or post.title or "")[: self.net.caption_max]
        thumb = int(post.thumb_at * 1000) if post.thumb_at is not None else None
        c = self._call("POST", f"{GRAPH}/{uid}/media?" + self._q(media_type="REELS", upload_type="resumable",
                                                                 caption=caption, share_to_feed="true",
                                                                 thumb_offset=thumb)).json()
        cid = c.get("id")
        if not cid:
            raise PublishError("Instagram didn't start the upload.", retryable=True)
        size = os.path.getsize(post.file)
        with open(post.file, "rb") as f:
            data = f.read()
        self.auth_scheme = "OAuth"
        try:
            self._call("POST", c.get("uri") or f"{RUPLOAD}/{cid}", data,
                       {"offset": "0", "file_size": str(size), "Content-Type": "application/octet-stream"}, timeout=600)
        finally:
            self.auth_scheme = "Bearer"
        if progress:
            progress(0.8)

        def ready():
            st = self._call("GET", f"{GRAPH}/{cid}?fields=status_code,status").json()
            code = st.get("status_code")
            if code == "FINISHED":
                return True
            if code in ("ERROR", "EXPIRED"):
                raise PublishError("Instagram couldn't process the video: " + str(st.get("status") or code))
            return False
        self._wait(ready, "Instagram's video processing", timeout=900, every=8)
        mid = self._call("POST", f"{GRAPH}/{uid}/media_publish?" + self._q(creation_id=cid)).json().get("id")
        if not mid:
            raise PublishError("Instagram processed the video but didn't publish it.", retryable=True)
        link = ""
        try:
            link = self._call("GET", f"{GRAPH}/{mid}?fields=permalink").json().get("permalink", "")
        except PublishError:
            pass
        if progress:
            progress(1.0)
        note = "" if not post.thumbnail else "Instagram uses the chosen frame as the cover (custom cover images aren't supported yet)."
        return Result(str(mid), link or "https://www.instagram.com/", note)

    def _error(self, code, body, headers):
        if "Application request limit" in body or "(#4)" in body or "(#9)" in body:
            return PublishError("Instagram's posting limit for now is reached (100 posts a day). It will retry later.",
                                retryable=True)
        return super()._error(code, body, headers)
