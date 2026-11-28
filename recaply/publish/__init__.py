"""
Publishing (Phase 5): send finished clips to a platform, now or scheduled.

    base       Publisher interface, Post, PublishError
    youtube    YouTube Shorts through the official Data API (OAuth, resumable upload)
    linkedin   LinkedIn video posts (Videos + Posts API) through a connected account
    x          X video posts (v2 chunked media upload) through a connected account
    instagram  Instagram Reels (resumable upload) through a connected account
    webhook    POST the video and its text to any URL (Zapier, Make, Upload-Post, your own server)
    queue      the schedule: SQLite table + a background scheduler with retries

Publishers are looked up by key with `get(key)`. Add a platform by subclassing
`Publisher` and listing it in `PUBLISHERS`.
"""

from __future__ import annotations

from recaply.publish.base import Post, PublishError, Publisher, Result  # noqa: F401


def publishers() -> dict[str, Publisher]:
    from recaply.publish.instagram import InstagramPublisher
    from recaply.publish.linkedin import LinkedInPublisher
    from recaply.publish.webhook import WebhookPublisher
    from recaply.publish.x import XPublisher
    from recaply.publish.youtube import YouTubePublisher
    return {p.key: p for p in (YouTubePublisher(), LinkedInPublisher(), XPublisher(), InstagramPublisher(),
                               WebhookPublisher())}


def get(key: str) -> Publisher | None:
    return publishers().get(key)


def overview() -> list[dict]:
    """For the UI: every platform, whether it's connected and what to do if not."""
    return [{"key": p.key, "label": p.label, **p.status()} for p in publishers().values()]
