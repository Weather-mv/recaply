"""
Webhook publisher: POSTs the finished video (multipart) and its text to a URL
you control, so any service can take it from there: Zapier, Make, n8n, an
Upload-Post style poster for TikTok / Instagram, or your own server.

    RECAPLY_PUBLISH_WEBHOOK=https://hooks.example.com/recaply
    RECAPLY_PUBLISH_WEBHOOK_TOKEN=...      (optional, sent as "Authorization: Bearer ...")

Form fields: title, description, tags (JSON list), privacy, and the file as `video`.
A 2xx answer is success; if the reply is JSON with "id" / "url" they are kept.
"""

from __future__ import annotations

import json
import os

from recaply.providers.http import ProviderError, post_multipart
from recaply.publish.base import Post, PublishError, Publisher, Result

MAX_MB = 200


class WebhookPublisher(Publisher):
    key = "webhook"
    label = "Webhook (Zapier / Make / Upload-Post / your own)"

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url if url is not None else os.environ.get("RECAPLY_PUBLISH_WEBHOOK", "")).strip()
        self.token = (token if token is not None else os.environ.get("RECAPLY_PUBLISH_WEBHOOK_TOKEN", "")).strip()

    def status(self) -> dict:
        if not self.url:
            return {"connected": False, "note": "Set RECAPLY_PUBLISH_WEBHOOK to a URL that accepts the video (multipart POST)."}
        return {"connected": True, "note": "Posts the video and its text to your webhook."}

    def publish(self, post: Post, progress=None) -> Result:
        if not self.url.lower().startswith(("http://", "https://")):
            raise PublishError(self.status()["note"])
        if not os.path.isfile(post.file):
            raise PublishError("The video file is gone.")
        if os.path.getsize(post.file) > MAX_MB * 1024 * 1024:
            raise PublishError(f"The video is over {MAX_MB} MB, too big for the webhook.")
        with open(post.file, "rb") as f:
            blob = f.read()
        fields = {"title": post.title, "description": post.description, "tags": json.dumps(post.tags), "privacy": post.privacy}
        headers = {"Authorization": "Bearer " + self.token} if self.token else None
        try:
            out = post_multipart(self.url, fields, {"video": (os.path.basename(post.file), blob, "video/mp4")},
                                 headers=headers, timeout=300, retries=1)
        except ProviderError as e:
            if "bad JSON" in str(e):
                out = {}                             # a 2xx with a non-JSON body still counts as delivered
            else:
                raise PublishError(str(e)[:300], retryable=e.retryable) from None
        if progress:
            progress(1.0)
        out = out if isinstance(out, dict) else {}
        return Result(str(out.get("id") or "sent"), str(out.get("url") or ""))
