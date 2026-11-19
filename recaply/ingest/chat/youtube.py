"""YouTube chat replay (past live streams and premieres) through yt-dlp's `live_chat` subtitle."""

from __future__ import annotations

import glob
import os

from recaply.ingest.chat import ChatUnavailable
from recaply.ingest.chat.files import parse_live_chat_lines


def fetch(src, cache, progress=None) -> list[dict]:
    import copy

    import yt_dlp
    from pipeline import base_opts
    folder = os.path.join(cache.dir, "chat")
    os.makedirs(folder, exist_ok=True)
    opts = base_opts(skip_download=True, writesubtitles=True, writeautomaticsub=False,
                     subtitleslangs=["live_chat"], outtmpl=os.path.join(folder, "chat.%(ext)s"))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.process_ie_result(copy.deepcopy(src.raw), download=True)
    except Exception as e:  # noqa: BLE001 — yt-dlp raises many things for "no such subtitle"
        raise ChatUnavailable(f"YouTube gave no chat replay ({str(e)[:100]})") from e
    files = glob.glob(os.path.join(glob.escape(folder), "*live_chat*.json*"))
    if not files:
        raise ChatUnavailable("this video has no chat replay")
    with open(files[0], encoding="utf-8", errors="replace") as f:
        msgs = parse_live_chat_lines(f)
    for p in files:
        os.remove(p)
    if progress:
        progress(1.0)
    return msgs
