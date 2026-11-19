"""Twitch VOD chat replay through the public web GraphQL endpoint."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from recaply.ingest.chat import ChatUnavailable
from recaply.providers.http import ProviderError, post_json

GQL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"          # the web player's public client id
QUERY_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
RANGES = 8                                             # parallel time ranges
MAX_PAGES = 400                                        # per range (100 messages each at most)
PAUSE = 0.05


def _query(video_id: str, offset: float | None = None, cursor: str | None = None) -> dict:
    variables: dict = {"videoID": video_id}
    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = int(offset or 0)
    body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
             "extensions": {"persistedQuery": {"version": 1, "sha256Hash": QUERY_HASH}}}]
    res = post_json(GQL, body, headers={"Client-ID": CLIENT_ID}, timeout=30, retries=2)
    return res[0] if isinstance(res, list) and res else res


def parse_page(page: dict) -> tuple[list[dict], str | None, bool]:
    """(messages, last cursor, has next page) from one GraphQL response."""
    video = ((page.get("data") or {}).get("video")) or {}
    comments = video.get("comments") or {}
    edges = comments.get("edges") or []
    out, cursor = [], None
    for e in edges:
        node = e.get("node") or {}
        cursor = e.get("cursor") or cursor
        frags = (node.get("message") or {}).get("fragments") or []
        text = "".join(f.get("text") or "" for f in frags)
        user = (node.get("commenter") or {}).get("displayName") or ""
        if text and node.get("contentOffsetSeconds") is not None:
            out.append({"t": float(node["contentOffsetSeconds"]), "text": text, "user": user})
    has_next = bool((comments.get("pageInfo") or {}).get("hasNextPage"))
    return out, cursor, has_next


def fetch(src, cache, progress=None) -> list[dict]:
    vid = re.sub(r"\D", "", src.video_id or "")
    if not vid:
        raise ChatUnavailable("not a Twitch VOD")
    dur = max(1.0, src.duration)
    edges = [dur * i / RANGES for i in range(RANGES + 1)]
    results: list[list[dict]] = [[] for _ in range(RANGES)]
    done = [0]
    lock = threading.Lock()
    first_error: list[str] = []

    def one(i: int):
        lo, hi = edges[i], edges[i + 1]
        msgs: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            try:
                page = _query(vid, offset=lo if cursor is None else None, cursor=cursor)
            except ProviderError as e:
                first_error.append(str(e)[:150])
                break
            got, cursor, more = parse_page(page)
            msgs += [m for m in got if m["t"] < hi]
            if not more or not got or got[-1]["t"] >= hi:
                break
            time.sleep(PAUSE)
        results[i] = msgs
        with lock:
            done[0] += 1
            if progress:
                progress(done[0] / RANGES)

    with ThreadPoolExecutor(RANGES) as ex:
        list(ex.map(one, range(RANGES)))
    allm = [m for r in results for m in r]
    if not allm and first_error:
        raise ChatUnavailable(f"Twitch refused the request ({first_error[0]})")
    if not allm:
        raise ChatUnavailable("this VOD has no chat replay (chat off, or the VOD is too old)")
    return allm
