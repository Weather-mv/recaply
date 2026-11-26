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
    def run(o):
        with yt_dlp.YoutubeDL(o) as ydl:
            ydl.process_ie_result(copy.deepcopy(src.raw), download=True)

    try:
        try:
            run(opts)
        except Exception as e:  # noqa: BLE001
            # A cookie file yt-dlp won't read shouldn't cost the chat: try once more without cookies.
            if "cookie" not in str(e).lower() or "cookiefile" not in opts:
                raise
            run({k: v for k, v in opts.items() if k != "cookiefile"})
    except Exception as e:  # noqa: BLE001 — yt-dlp raises many things for "no such subtitle"
        why = str(e)
        if "cookie" in why.lower():
            why = "cookies.txt couldn't be read"
        raise ChatUnavailable(f"YouTube gave no chat replay ({why[:100]})") from e
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
