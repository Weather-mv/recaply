"""
killer_id.py — read WHO killed WHOM from the kill feed, for 3K / 4K / ACE
badges and named clutches.

The 360p scan knows when each kill happened, but names are unreadable at
360p. So for each round, 720p frames are fetched just after every kill (while
its kill feed row is on screen) and the feed is read with OCR (RapidOCR,
ONNX — no GPU needed). Every victim can only die once per round, so kills =
distinct victims, each credited to the killer most readings agree on.

Rows are located with the same colour logic as the 360p scan, then only the
killer's and the victim's part of each row go through OCR text recognition
(no text-detection pass), and a row already read twice is skipped. Finally
all readings across the VOD are clustered into a roster, so OCR slips
("Jingg9", "1OrsakeN") map back onto the real player.

On HLS (Twitch) each 10-second segment is fetched once, directly; other
sources use a short ffmpeg seek per group of kills.
"""

import re
import subprocess
import threading
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import cv2
import numpy as np

from recaply.profiles.valorant import round_detector as rd

FPS = 2                          # frames per second fetched around kills
LOOK_AFTER = (0.5, 3.0)          # seconds after a kill's first sighting to read
READS_PER_ROW = 2                # OCR a feed row this many times, then skip it
MIN_READ_SCORE = 0.6
SAME_ROW_DIFF = 12               # stricter than the scan's row matching: two
                                 # different victims must never look "read"

_ocr = None
_ocr_lock = threading.Lock()


def ocr_engine():
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            from rapidocr import RapidOCR
            _ocr = RapidOCR(params={
                "Global.log_level": "critical",
                "Global.use_cls": False,
                "Global.use_det": False,       # rows are located by colour
            })
    return _ocr


# ---------- fetching frames ----------

class FrameSource:
    """720p frames of the stream for arbitrary time windows."""

    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {}
        self.segments = None
        if ".m3u8" in url:
            try:
                self.segments = self._playlist(url)
            except Exception:  # noqa: BLE001 — fall back to ffmpeg seeking
                self.segments = None
        self._cache = {}
        self._lock = threading.Lock()

    def _playlist(self, url):
        req = urllib.request.Request(url, headers=self.headers)
        text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        base = url.rsplit("/", 1)[0] + "/"
        segs, t, dur = [], 0.0, None
        for line in text.splitlines():
            if line.startswith("#EXTINF:"):
                dur = float(re.match(r"#EXTINF:([\d.]+)", line).group(1))
            elif line and not line.startswith("#") and dur is not None:
                segs.append((t, dur, line if line.startswith("http") else base + line))
                t += dur
                dur = None
        return segs or None

    @staticmethod
    def _raw_frames(raw):
        size = 1280 * 720 * 3
        n = len(raw) // size
        return np.frombuffer(raw[: n * size], np.uint8).reshape(n, 720, 1280, 3)

    def _segment_frames(self, idx):
        with self._lock:
            if idx in self._cache:
                return self._cache[idx]
        start, _, seg_url = self.segments[idx]
        req = urllib.request.Request(seg_url, headers=self.headers)
        data = None
        for attempt in range(3):
            try:
                data = urllib.request.urlopen(req, timeout=60).read()
                break
            except Exception:  # noqa: BLE001 — retried
                if attempt == 2:
                    raise
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-skip_loop_filter", "all",
               "-f", "mpegts", "-i", "pipe:0", "-an",
               "-vf", f"fps={FPS},scale=1280:720", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        frames = self._raw_frames(subprocess.run(cmd, input=data, capture_output=True,
                                                 timeout=120).stdout)
        out = [(start + i / FPS, frames[i]) for i in range(len(frames))]
        with self._lock:
            if len(self._cache) > 16:
                self._cache.pop(next(iter(self._cache)))
            self._cache[idx] = out
        return out

    def frames(self, a, b):
        """[(t, 720p frame)] for t in [a, b]."""
        if self.segments:
            out = []
            for i, (s, d, _) in enumerate(self.segments):
                if s + d >= a and s <= b:
                    out += [(t, f) for t, f in self._segment_frames(i) if a <= t <= b]
            return out
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        if self.headers and self.url.startswith("http"):
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())]
        cmd += ["-skip_loop_filter", "all", "-ss", f"{a:.2f}", "-t", f"{b - a + 0.01:.2f}",
                "-i", self.url, "-an", "-vf", f"fps={FPS},scale=1280:720",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        frames = self._raw_frames(subprocess.run(cmd, capture_output=True, timeout=180).stdout)
        return [(a + i / FPS, f) for i, f in enumerate(frames)]


# ---------- reading the feed ----------

def _recognize(img):
    if img.size == 0:
        return "", 0.0
    res = ocr_engine()(img, use_det=False, use_cls=False)
    if not res.txts:
        return "", 0.0
    return res.txts[0].strip(), float(res.scores[0])


def read_rows(frame720, done_crops):
    """
    OCR each kill feed row not yet read READS_PER_ROW times:
    [(killer_text, victim_text, score)]. `done_crops` is this round's list of
    [identity_crop, reads], updated in place.
    """
    small = cv2.resize(frame720, (rd.FRAME_W, rd.FRAME_H), interpolation=cv2.INTER_AREA)
    out = []
    for row, _, crop, vx in rd.read_feed_rows(small):
        hit = next((d for d in done_crops if rd._diff(d[0], crop) < SAME_ROW_DIFF), None)
        if hit is not None and hit[1] >= READS_PER_ROW:
            continue
        yc = rd.FEED_ROWS[row] * 2
        y0, y1 = yc - 11, yc + 11
        ktxt, ks = _recognize(frame720[y0:y1, rd.FEED_X0 * 2:vx * 2])
        vtxt, vs = _recognize(frame720[y0:y1, vx * 2 - 4:rd.VICTIM_CROP_X[0] * 2 + 60])
        if min(ks, vs) < MIN_READ_SCORE or len(ktxt) < 3 or len(vtxt) < 3:
            continue
        if hit is None:
            done_crops.append([crop, 1])
        else:
            hit[1] += 1
        out.append((ktxt, vtxt, min(ks, vs)))
    return out


def _norm(name):
    """Comparable form of an OCR'd name: case, spaces and look-alike
    characters (0/O, 1/l/I, 9/g, 5/S) don't matter."""
    table = str.maketrans({"0": "o", "1": "l", "i": "l", "9": "g", "5": "s", "|": "l"})
    return re.sub(r"[^a-z0-9]", "", name.lower().translate(table))


def _sim(a, b):
    return SequenceMatcher(None, a, b).ratio()


def team_tags(readings):
    """Team tags ("TL", "PRX") = the frequent first words of readings."""
    firsts = Counter(_norm(raw.split(" ", 1)[0]) for raw, _ in readings if " " in raw)
    total = sum(firsts.values()) or 1
    return [t for t, c in firsts.most_common(6) if c >= max(5, 0.05 * total) and t]


def split_reading(raw, tags):
    """(team tag or None, player-name part) of a reading. OCR sometimes
    garbles the tag or glues it onto the name ("TLGSR")."""
    if " " in raw:
        first, rest = raw.split(" ", 1)
        f = _norm(first)
        if len(f) <= 5:
            tag = max(tags, key=lambda t: _sim(t, f), default=None)
            return (tag if tag and _sim(tag, f) >= 0.5 else None), _norm(rest)
    n = _norm(raw)
    for t in sorted(tags, key=len, reverse=True):
        if n.startswith(t) and len(n) > len(t) + 1:
            return t, n[len(t):]
    return None, n


def player_part(raw, tags):
    return split_reading(raw, tags)[1]


def build_roster(readings):
    """
    Cluster every name reading in the VOD by player name. Each team (tag)
    fields five players, so per tag the five most-read names are the roster
    and rarer clusters (OCR slips) merge into the closest of them.
    Returns (roster {player-part: display name}, tags).
    """
    tags = team_tags(readings)
    counts, spelling, tag_votes = Counter(), {}, {}
    for raw, score in readings:
        tag, n = split_reading(raw, tags)
        if len(n) < 2:
            continue
        counts[n] += 1
        spelling.setdefault(n, Counter())[raw] += score
        if tag:
            tag_votes.setdefault(n, Counter())[tag] += 1
    clusters = []            # dicts: rep, count, raw Counter, parts, tags Counter
    for n, c in counts.most_common():
        best = max(clusters, key=lambda cl: _sim(cl["rep"], n), default=None)
        if best is not None and _sim(best["rep"], n) >= 0.75:
            cl = best
        else:
            cl = {"rep": n, "count": 0, "raw": Counter(), "parts": [], "tags": Counter()}
            clusters.append(cl)
        cl["count"] += c
        cl["raw"].update(spelling[n])
        cl["parts"].append(n)
        cl["tags"].update(tag_votes.get(n, {}))

    # Per team: top five clusters are the players.
    players = []
    for tag in tags or [None]:
        mine = [cl for cl in clusters
                if (cl["tags"].most_common(1)[0][0] if cl["tags"] else None) == tag]
        players += [cl for cl in sorted(mine, key=lambda cl: -cl["count"])[:5] if cl["count"] >= 3]
    roster = {}
    for cl in clusters:
        target = cl if cl in players else None
        if target is None:
            near = max(players, key=lambda p: _sim(p["rep"], cl["rep"]), default=None)
            if near is None or _sim(near["rep"], cl["rep"]) < 0.5:
                continue
            target = near
        spellings = target["raw"].most_common()
        display = next((s for s, _ in spellings
                        if " " in s and _norm(s.split(" ", 1)[0]) in tags), spellings[0][0])
        for n in cl["parts"]:
            roster[n] = display
    return roster, tags


def _windows(kill_times):
    wins = []
    for t in sorted(kill_times):
        a, b = t + LOOK_AFTER[0], t + LOOK_AFTER[1]
        if wins and a <= wins[-1][1]:
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b])
    return wins


def read_round(source, rnd):
    """Raw kill feed readings of one round: [(t, killer, victim, score)]."""
    events = sorted(rnd.get("kills", []))
    done_crops = []
    out = []
    for a, b in _windows([t for t, _ in events]):
        for ft, frame in source.frames(a, b):
            for ktxt, vtxt, score in read_rows(frame, done_crops):
                out.append((ft, ktxt, vtxt, score))
    return out


def summarize_round(readings, roster, tags):
    """
    Kills by name for one round: {'kills': [(t, killer, victim)],
    'per_killer': [(killer, n)]}. Each victim counts once, credited to the
    killer most readings agree on.
    """
    votes, first = {}, {}
    for t, ktxt, vtxt, _ in readings:
        killer = roster.get(player_part(ktxt, tags))
        victim = roster.get(player_part(vtxt, tags))
        if not killer or not victim or killer == victim:
            continue
        votes.setdefault(victim, Counter())[killer] += 1
        first[victim] = min(first.get(victim, t), t)
    kills = sorted((first[v], c.most_common(1)[0][0], v) for v, c in votes.items())
    return {"kills": kills, "per_killer": Counter(k for _, k, _ in kills).most_common()}


def summaries(raw):
    """Per-round kills by name from raw readings (None = round not read)."""
    readings = [(txt, sc) for r in raw if r for _, k, v, sc in r for txt in (k, v)]
    roster, tags = build_roster(readings)
    return [summarize_round(r, roster, tags) if r else None for r in raw]


def attribute_rounds(url, headers, rounds, workers=6, progress=None, on_update=None,
                     update_every=6):
    """
    Kill feed names for every round that could hold a 3K+ or a clutch.
    `on_update(summaries)` is called every few rounds with results so far
    (the roster sharpens as more readings come in) and once at the end.
    """
    ocr_engine()
    source = FrameSource(url, headers)
    raw = [None] * len(rounds)
    todo = [i for i, r in enumerate(rounds)
            if max(sum(1 for _, s in r.get("kills", []) if s == side) for side in (0, 1)) >= 3]
    lock = threading.Lock()
    done = [0]

    def job(i):
        try:
            got = read_round(source, rounds[i])
        except Exception:  # noqa: BLE001 — a failed round just gets no names
            got = None
        with lock:
            raw[i] = got
            done[0] += 1
            n = done[0]
            snapshot = list(raw) if on_update and n % update_every == 0 else None
        if progress:
            progress(n, len(todo))
        if snapshot is not None:
            on_update(summaries(snapshot))

    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(job, todo))
    final = summaries(raw)
    if on_update:
        on_update(final)
    return final
