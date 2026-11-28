"""
Each social network's sign-in settings, text limits and profile lookup.

Client ids and secrets come from the environment (.env), one pair per
network: RECAPLY_<NET>_CLIENT_ID / RECAPLY_<NET>_CLIENT_SECRET. YouTube keeps
the names it already had (RECAPLY_YT_CLIENT_ID / RECAPLY_YT_CLIENT_SECRET).
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field

from recaply.providers.http import get_json


@dataclass(frozen=True)
class Network:
    key: str
    label: str
    env: str                               # RECAPLY_<env>_CLIENT_ID / _CLIENT_SECRET
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    scope_sep: str = " "
    pkce: bool = True
    basic_auth: bool = False               # send the client id/secret as HTTP Basic (X) instead of in the form
    extra: dict = field(default_factory=dict)
    caption_max: int = 2200
    title_max: int = 0                     # 0 = the network has no separate title
    hashtag_max: int = 30
    setup_url: str = ""
    note: str = ""

    @property
    def client_id(self) -> str:
        return os.environ.get(f"RECAPLY_{self.env}_CLIENT_ID", "").strip()

    @property
    def client_secret(self) -> str:
        return os.environ.get(f"RECAPLY_{self.env}_CLIENT_SECRET", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "configured": self.configured, "caption_max": self.caption_max,
                "title_max": self.title_max, "hashtag_max": self.hashtag_max, "setup_url": self.setup_url,
                "note": self.note, "env": f"RECAPLY_{self.env}_CLIENT_ID / RECAPLY_{self.env}_CLIENT_SECRET"}


NETWORKS: dict[str, Network] = {n.key: n for n in (
    Network("youtube", "YouTube", "YT",
            "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
            ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"),
            extra={"access_type": "offline", "prompt": "consent"},
            caption_max=5000, title_max=100, hashtag_max=15,
            setup_url="https://console.cloud.google.com/apis/credentials",
            note="Uploads count against your YouTube API quota (about 6 a day by default)."),
    Network("linkedin", "LinkedIn", "LINKEDIN",
            "https://www.linkedin.com/oauth/v2/authorization", "https://www.linkedin.com/oauth/v2/accessToken",
            ("openid", "profile", "w_member_social"), pkce=False,
            caption_max=3000, title_max=200, hashtag_max=30,
            setup_url="https://www.linkedin.com/developers/apps",
            note="Posts to your own profile. LinkedIn asks you to sign in again every 60 days."),
    Network("x", "X (Twitter)", "X",
            "https://x.com/i/oauth2/authorize", "https://api.x.com/2/oauth2/token",
            ("tweet.read", "tweet.write", "users.read", "media.write", "offline.access"), basic_auth=True,
            caption_max=280, hashtag_max=10,
            setup_url="https://developer.x.com/en/portal/dashboard",
            note="Posting videos needs a paid X API plan (Basic or higher)."),
    Network("instagram", "Instagram", "INSTAGRAM",
            "https://www.instagram.com/oauth/authorize", "https://api.instagram.com/oauth/access_token",
            ("instagram_business_basic", "instagram_business_content_publish"), scope_sep=",", pkce=False,
            caption_max=2200, hashtag_max=30,
            setup_url="https://developers.facebook.com/apps",
            note="Needs an Instagram Business or Creator account. Posts as a Reel."),
)}


def get(key: str) -> Network | None:
    return NETWORKS.get(key)


# ---------- who signed in ----------

def profile(net: str, token: dict) -> dict:
    """{'external_id', 'name', 'handle', 'avatar'} for the account that just signed in."""
    at = token["access_token"]
    bearer = {"Authorization": "Bearer " + at}
    if net == "youtube":
        d = get_json("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", bearer)
        items = d.get("items") or []
        if not items:
            raise ValueError("This Google account has no YouTube channel yet. Create one on youtube.com first.")
        sn = items[0]["snippet"]
        return {"external_id": items[0]["id"], "name": sn.get("title") or "YouTube channel",
                "handle": sn.get("customUrl") or "", "avatar": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", "")}
    if net == "linkedin":
        d = get_json("https://api.linkedin.com/v2/userinfo", bearer)
        return {"external_id": d["sub"], "name": d.get("name") or "LinkedIn member", "handle": "", "avatar": d.get("picture", "")}
    if net == "x":
        d = get_json("https://api.x.com/2/users/me?user.fields=profile_image_url", bearer)["data"]
        return {"external_id": d["id"], "name": d.get("name") or d.get("username", ""), "handle": "@" + d.get("username", ""),
                "avatar": d.get("profile_image_url", "")}
    if net == "instagram":
        q = urllib.parse.urlencode({"fields": "user_id,username,name,profile_picture_url", "access_token": at})
        d = get_json("https://graph.instagram.com/me?" + q)
        return {"external_id": str(d.get("user_id") or d.get("id")), "name": d.get("name") or d.get("username", ""),
                "handle": "@" + d.get("username", ""), "avatar": d.get("profile_picture_url", "")}
    raise ValueError(f"unknown network {net}")
