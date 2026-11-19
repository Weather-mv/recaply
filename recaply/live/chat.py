"""
Live chat listeners. Each one calls `on_message(user, text)` for every chat
message as it arrives, from its own thread; the channel loop stamps the
message with stream time.

- Twitch: IRC over TLS, anonymous and read-only (a `justinfan` nick, no
  login, the same way any chat overlay reads a channel).
- YouTube: the live chat page's own polling API, read-only.
- Kick: no live listener yet (its chat sits behind a websocket service);
  detection runs on audio alone and the channel says so.

Chat is optional: a listener that can't connect just reports why and the
detector carries on with audio.
"""

from __future__ import annotations

import json
import random
import re
import socket
import ssl
import threading
import time
import urllib.request
from typing import Callable
from urllib.parse import urlparse

from recaply.log import get_logger

log = get_logger("live.chat")
OnMessage = Callable[[str, str], None]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class LiveChat:
    """Base class: a thread that reads chat until stop()."""

    def __init__(self, on_message: OnMessage):
        self.on_message = on_message
        self.connected = False
        self.note = ""
        self.count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._guard, daemon=True, name=type(self).__name__)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _guard(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            try:
                self.run_once()
                delay = 2.0
            except Exception as e:  # noqa: BLE001 — chat must never take the recording down
                self.connected = False
                self.note = str(e)[:150]
                log.warning("chat disconnected", kind=type(self).__name__, error=self.note)
            self._stop.wait(min(60.0, delay) + random.random())
            delay *= 2

    def emit(self, user: str, text: str) -> None:
        self.count += 1
        self.on_message(user, text)

    def run_once(self) -> None:
        raise NotImplementedError


# ---------- Twitch ----------

PRIVMSG = re.compile(r"^(?:@\S+ )?:([^!\s]+)![^ ]* PRIVMSG #\S+ :(.*)$")


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """(user, text) of an IRC PRIVMSG line, or None for anything else."""
    m = PRIVMSG.match(line.rstrip("\r\n"))
    return (m.group(1), m.group(2)) if m else None


def twitch_channel(url: str) -> str:
    u = urlparse(url)
    if "twitch.tv" not in (u.hostname or ""):
        return ""
    first = [p for p in u.path.split("/") if p][:1]
    return first[0].lower() if first and re.fullmatch(r"\w{1,25}", first[0]) else ""


class TwitchChat(LiveChat):
    def __init__(self, channel: str, on_message: OnMessage, host="irc.chat.twitch.tv", port=6697, tls=True):
        super().__init__(on_message)
        self.channel, self.host, self.port, self.tls = channel, host, port, tls

    def run_once(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=20)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        sock.settimeout(60)
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
            self.connected, self.note = True, ""
            buf = b""
            while not self._stop.is_set():
                try:
                    data = sock.recv(8192)
                except socket.timeout:
                    sock.sendall(b"PING :keepalive\r\n")           # quiet channel: check the line is alive
                    continue
                if not data:
                    raise ConnectionError("chat server closed the connection")
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    text = line.decode("utf-8", "replace")
                    if text.startswith("PING"):
                        sock.sendall(("PONG" + text[4:] + "\r\n").encode())
                        continue
                    m = parse_privmsg(text)
                    if m:
                        self.emit(*m)
        finally:
            self.connected = False
            try:
                sock.close()
            except OSError:
                pass


# ---------- YouTube ----------

def parse_actions(resp: dict) -> tuple[list[tuple[str, str]], str | None, float]:
    """(messages, next continuation, seconds to wait) from a get_live_chat response."""
    cont = (resp.get("continuationContents") or {}).get("liveChatContinuation") or {}
    msgs = []
    for a in cont.get("actions") or []:
        r = ((a.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
        if not r:
            continue
        parts = []
        for run in (r.get("message") or {}).get("runs") or []:
            if "text" in run:
                parts.append(run["text"])
            elif "emoji" in run:
                parts.append((run["emoji"].get("shortcuts") or [run["emoji"].get("emojiId", "")])[0])
        user = (r.get("authorName") or {}).get("simpleText") or ""
        if parts:
            msgs.append((user, "".join(parts)))
    nxt, wait = None, 3.0
    for c in cont.get("continuations") or []:
        d = c.get("timedContinuationData") or c.get("invalidationContinuationData") or c.get("reloadContinuationData")
        if d and d.get("continuation"):
            nxt = d["continuation"]
            wait = max(1.0, min(10.0, float(d.get("timeoutMs") or 3000) / 1000.0))
            break
    return msgs, nxt, wait


class YouTubeChat(LiveChat):
    def __init__(self, video_id: Callable[[], str], on_message: OnMessage):
        super().__init__(on_message)
        self._video_id = video_id

    def _open(self, vid: str) -> tuple[str, str, str]:
        req = urllib.request.Request(f"https://www.youtube.com/live_chat?is_popout=1&v={vid}", headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
        ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        cont = re.search(r'"continuation":"([^"]+)"', html)
        if not (key and cont):
            raise RuntimeError("this stream has no readable live chat")
        return key.group(1), ver.group(1) if ver else "2.20240101.00.00", cont.group(1)

    def run_once(self) -> None:
        vid = self._video_id()
        if not vid:
            raise RuntimeError("live video not found yet")
        key, ver, cont = self._open(vid)
        self.connected, self.note = True, ""
        url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key}"
        while cont and not self._stop.is_set():
            body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": ver}}, "continuation": cont}).encode()
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
            msgs, cont, wait = parse_actions(resp)
            for u, t in msgs:
                self.emit(u, t)
            self._stop.wait(wait)
        self.connected = False


def listener_for(url: str, on_message: OnMessage, video_id: Callable[[], str] | None = None) -> tuple[LiveChat | None, str]:
    """(listener, note): a listener for the link's platform, or None and why there isn't one."""
    host = (urlparse(url).hostname or "").lower()
    if "twitch.tv" in host:
        ch = twitch_channel(url)
        return (TwitchChat(ch, on_message), "") if ch else (None, "no Twitch channel in that link")
    if "youtube.com" in host or "youtu.be" in host:
        return (YouTubeChat(video_id or (lambda: ""), on_message), "")
    if "kick.com" in host:
        return None, "live chat for Kick isn't supported yet: using audio only"
    return None, "no live chat for this site: using audio only"
