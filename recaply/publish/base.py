"""The publisher interface."""

from __future__ import annotations

from dataclasses import dataclass, field


class PublishError(RuntimeError):
    """A publish failed. `retryable` errors (network, 5xx) are tried again by the scheduler."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Post:
    file: str                       # path of the video to upload
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy: str = "public"         # public | unlisted | private
    account_id: int | None = None   # a connected social account (None = the old single YouTube sign-in)
    thumbnail: str | None = None    # path of a cover image, where the network takes one
    thumb_at: float | None = None   # seconds into the video for the cover frame (Instagram)


@dataclass
class Result:
    id: str
    url: str
    note: str = ""                  # something the user should know although it worked (e.g. no thumbnail)


class Publisher:
    key = ""
    label = ""

    def status(self) -> dict:
        """{"connected": bool, "note": str}: what the UI shows next to the platform."""
        raise NotImplementedError

    def publish(self, post: Post, progress=None) -> Result:
        raise NotImplementedError
