"""Parsers for chat exports and replay payloads (pure functions, no network)."""

from __future__ import annotations

import json
from typing import Iterable


def _runs_text(message: dict) -> str:
    out = []
    for run in (message or {}).get("runs") or []:
        if "text" in run:
            out.append(run["text"])
        elif "emoji" in run:
            e = run["emoji"]
            out.append((e.get("shortcuts") or [e.get("emojiId") or ""])[0] if isinstance(e, dict) else "")
    return "".join(out)


def parse_live_chat_lines(lines: Iterable[str]) -> list[dict]:
    """yt-dlp's `live_chat` file: one JSON object per line (replayChatItemAction)."""
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        rc = obj.get("replayChatItemAction") or {}
        try:
            offset = float(rc.get("videoOffsetTimeMsec")) / 1000.0
        except (TypeError, ValueError):
            continue
        for act in rc.get("actions") or []:
            item = ((act.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
            if not item:
                continue
            text = _runs_text(item.get("message"))
            if text:
                out.append({"t": offset, "text": text,
                            "user": (item.get("authorName") or {}).get("simpleText") or ""})
    return out


def parse_file(path: str) -> list[dict]:
    """Auto-detect: TwitchDownloader JSON, yt-dlp live_chat lines, or a plain [{t, text}] list."""
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(1)
        f.seek(0)
        if head == "{":
            try:
                data = json.load(f)
            except ValueError:                     # JSON lines
                f.seek(0)
                return parse_live_chat_lines(f)
        elif head == "[":
            data = json.load(f)
        else:
            return parse_live_chat_lines(f)
    if isinstance(data, dict) and "replayChatItemAction" in data:          # a one-line live_chat file
        return parse_live_chat_lines([json.dumps(data)])
    if isinstance(data, dict) and isinstance(data.get("comments"), list):
        out = []
        for c in data["comments"]:
            msg = c.get("message") or {}
            body = msg.get("body") if isinstance(msg, dict) else None
            if body is None:
                body = "".join(f.get("text", "") for f in (msg.get("fragments") or [])) if isinstance(msg, dict) else ""
            t = c.get("content_offset_seconds")
            if t is None:
                t = c.get("contentOffsetSeconds")
            if body and t is not None:
                out.append({"t": float(t), "text": body, "user": (c.get("commenter") or {}).get("display_name") or ""})
        return out
    if isinstance(data, list):
        return [{"t": float(m.get("t", m.get("time", 0))), "text": str(m.get("text", m.get("message", ""))),
                 "user": str(m.get("user", ""))} for m in data if isinstance(m, dict)]
    return []
