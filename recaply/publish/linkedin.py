"""
LinkedIn video posts through the versioned REST API (Videos API + Posts API).

initializeUpload -> PUT each part to its upload URL -> finalizeUpload (with
the parts' ETags) -> optional thumbnail -> create the post on the member's
own profile. The API version is RECAPLY_LINKEDIN_VERSION (YYYYMM).
"""

from __future__ import annotations

import os
import re
import urllib.parse

from recaply.publish.base import Post, PublishError, Result
from recaply.publish.social_base import SocialPublisher

API = "https://api.linkedin.com/rest"
_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def commentary(text: str) -> str:
    """LinkedIn's 'little text' format: reserved characters are escaped, #words become real hashtags."""
    out = _RESERVED.sub(r"\\\1", text or "")
    return re.sub(r"(?<![\w\\])#(\w+)", lambda m: "{hashtag|\\#|" + m.group(1) + "}", out)


class LinkedInPublisher(SocialPublisher):
    network = "linkedin"

    def _headers(self, extra: dict | None = None) -> dict:
        return {"LinkedIn-Version": os.environ.get("RECAPLY_LINKEDIN_VERSION", "202605").strip(),
                "X-Restli-Protocol-Version": "2.0.0", **(extra or {})}

    def publish(self, post: Post, progress=None) -> Result:
        owner = "urn:li:person:" + self._begin(post)["external_id"]
        size = os.path.getsize(post.file)
        init = self._call("POST", f"{API}/videos?action=initializeUpload", headers=self._headers(), json_body={
            "initializeUploadRequest": {"owner": owner, "fileSizeBytes": size, "uploadCaptions": False,
                                        "uploadThumbnail": bool(post.thumbnail)}}).json().get("value") or {}
        video, parts = init.get("video"), init.get("uploadInstructions") or []
        if not video or not parts:
            raise PublishError("LinkedIn didn't start the upload.", retryable=True)
        etags = []
        with open(post.file, "rb") as f:
            for i, p in enumerate(parts):
                f.seek(int(p["firstByte"]))
                chunk = f.read(int(p["lastByte"]) - int(p["firstByte"]) + 1)
                r = self._call("PUT", p["uploadUrl"], chunk, {"Content-Type": "application/octet-stream"}, auth=False,
                               timeout=300)
                etags.append(r.headers.get("ETag", "").strip('"'))
                if progress:
                    progress(0.9 * (i + 1) / len(parts))
        self._call("POST", f"{API}/videos?action=finalizeUpload", headers=self._headers(), json_body={
            "finalizeUploadRequest": {"video": video, "uploadToken": init.get("uploadToken", ""), "uploadedPartIds": etags}})
        note = ""
        if post.thumbnail and init.get("thumbnailUploadUrl"):
            try:
                with open(post.thumbnail, "rb") as f:
                    self._call("PUT", init["thumbnailUploadUrl"], f.read(), {"Content-Type": "application/octet-stream"},
                               auth=False)
            except (OSError, PublishError) as e:
                note = f"Posted, but the thumbnail wasn't added: {str(e)[:160]}"
        r = self._call("POST", f"{API}/posts", headers=self._headers(), json_body={
            "author": owner, "commentary": commentary(post.description or post.title),
            "visibility": "PUBLIC" if post.privacy == "public" else "CONNECTIONS",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "content": {"media": {"title": (post.title or "")[:200], "id": video}},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False})
        urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or r.json().get("id") or ""
        if progress:
            progress(1.0)
        return Result(urn, f"https://www.linkedin.com/feed/update/{urllib.parse.quote(urn, safe=':')}" if urn else "", note)
