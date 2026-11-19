"""
B-roll: short cut-away footage (or pictures) laid over the clip while the
speaker keeps talking, matched to what is being said.

1. `moments()` picks up to three spots and a search phrase for each: the LLM
   when one is configured, otherwise the longest uncommon word of a sentence.
2. A provider finds a match: Pexels (free API key, videos) or Openverse (no key,
   public-domain pictures only, so nothing needs attribution).
3. `overlay()` covers the picture for ~3 s at those spots; the sound is never touched.

Downloads are capped in size and time. Anything that goes wrong (no key, no
match, offline) skips that insert and the clip is made without it.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from recaply.log import get_logger
from recaply.providers.http import get_json
from recaply.providers.llm import LLM, Usage
from recaply.telemetry import Telemetry

log = get_logger("broll")
MAX_INSERTS = 3
INSERT_SEC = 3.0
MIN_GAP = 6.0
MAX_VIDEO_MB, MAX_IMAGE_MB = 40, 12
UA = "Recaply/1.0 (b-roll fetch)"
STOP = set("""about above after again against because before being below between both could does doing down during each
few from further have having here herself himself into itself just more most myself once only other ought ours over own
same should some such than that their theirs them themselves then there these they this those through under until very
was were what when where which while who whom will with would your yours yourself yourselves really actually
gonna wanna going think know like right yeah okay well mean thing things stuff something anything everything""".split())


class Asset:
    def __init__(self, path: str, kind: str, source: str, credit: str = "", query: str = ""):
        self.path, self.kind, self.source, self.credit, self.query = path, kind, source, credit, query


# ---------- 1. where and what ----------

def heuristic_moments(words: list[dict], duration: float, n: int = MAX_INSERTS) -> list[dict]:
    """One concrete-looking word per sentence, spread out over the clip."""
    from recaply.edit.translate import segments
    cands = []
    for seg in segments(words):
        best = None
        for w in seg:
            t = re.sub(r"[^a-z]", "", w["w"].lower())
            if len(t) >= 6 and t not in STOP and (best is None or len(t) > len(best[0])):
                best = (t, w["s"])
        if best:
            cands.append({"t": best[1], "query": best[0]})
    return _spread(cands, duration, n)


def _spread(cands: list[dict], duration: float, n: int) -> list[dict]:
    out: list[dict] = []
    for c in sorted(cands, key=lambda c: c["t"]):
        t = float(c["t"])
        if t < 2.0 or t + INSERT_SEC > duration - 1.5:
            continue
        if out and t - out[-1]["t"] < MIN_GAP:
            continue
        out.append({"t": round(t, 2), "query": str(c["query"])[:60]})
    if len(out) > n:                                        # keep an even spread
        idx = [round(i * (len(out) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
        out = [out[i] for i in idx]
    return out


def moments(words: list[dict], duration: float, llm: LLM | None, tel: Telemetry | None = None, n: int = MAX_INSERTS) -> list[dict]:
    """[{t, query}] spots for B-roll (t = seconds into the clip)."""
    if not words:
        return []
    if llm is not None:
        text = " ".join(f"[{w['s']:.1f}]{w['w']}" if i % 8 == 0 else w["w"] for i, w in enumerate(words))
        prompt = (f"A {duration:.0f}-second clip transcript with a [seconds] marker every few words:\n{text}\n\n"
                  f"Pick up to {n} moments where a short cut-away of stock footage would help (something concrete "
                  f"and visual being talked about: a place, object, animal, activity). At least {MIN_GAP:.0f} s apart, "
                  f"not in the first 2 s. Return {{\"moments\": [{{\"t\": seconds, \"query\": \"2-3 word stock footage search\"}}]}}.")
        try:
            data, usage = llm.complete_json("You are a video editor choosing B-roll. Answer with JSON only.", prompt, max_tokens=800)
            if tel is not None and isinstance(usage, Usage):
                tel.add_tokens(usage.provider, usage.tokens_in, usage.tokens_out)
            cands = [{"t": float(m["t"]), "query": str(m["query"])} for m in data.get("moments", []) if "t" in m and m.get("query")]
            got = _spread(cands, duration, n)
            if got:
                return got
        except Exception as e:  # noqa: BLE001 — fall back to the word heuristic
            log.warning("b-roll moments from the LLM failed", error=str(e)[:200])
    return heuristic_moments(words, duration, n)


# ---------- 2. providers ----------

def _download(url: str, dest: str, max_mb: int) -> bool:
    if not url.lower().startswith(("http://", "https://")):
        return False
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            size = 0
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise ValueError("too big")
                f.write(chunk)
        return size > 100
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, OSError) as e:
        log.warning("b-roll download failed", error=str(e)[:150])
        try:
            os.remove(dest)
        except OSError:
            pass
        return False


class Pexels:
    name = "pexels"

    def __init__(self, key: str, base: str = "https://api.pexels.com"):
        self.key, self.base = key, base.rstrip("/")

    def find(self, query: str, folder: str, tag: str) -> Asset | None:
        data = get_json(f"{self.base}/videos/search?" + urllib.parse.urlencode({"query": query, "per_page": 5, "orientation": "landscape"}),
                        headers={"Authorization": self.key, "User-Agent": UA}, timeout=20, retries=1)
        for v in (data.get("videos") or []) if isinstance(data, dict) else []:
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")
                     and 640 <= int(f.get("width") or 0) <= 1920]
            if not files:
                continue
            f = min(files, key=lambda f: abs(int(f["width"]) - 1280))
            dest = os.path.join(folder, f"{tag}.mp4")
            if _download(f["link"], dest, MAX_VIDEO_MB):
                return Asset(dest, "video", "Pexels", f"Video by {(v.get('user') or {}).get('name', 'a Pexels creator')} on Pexels", query)
        return None


class Openverse:
    name = "openverse"

    def __init__(self, base: str = "https://api.openverse.org"):
        self.base = base.rstrip("/")

    def find(self, query: str, folder: str, tag: str) -> Asset | None:
        data = get_json(f"{self.base}/v1/images/?" + urllib.parse.urlencode({"q": query, "page_size": 5, "license": "cc0,pdm"}),
                        headers={"User-Agent": UA}, timeout=20, retries=1)
        for it in (data.get("results") or []) if isinstance(data, dict) else []:
            url = it.get("url")
            if not url:
                continue
            dest = os.path.join(folder, f"{tag}.img")
            if _download(url, dest, MAX_IMAGE_MB):
                return Asset(dest, "image", "Openverse", f"{it.get('title') or 'Image'} ({(it.get('license') or '').upper()})", query)
        return None


def provider():
    from recaply.settings import settings
    key = os.environ.get("RECAPLY_PEXELS_KEY", "").strip() or getattr(settings, "pexels_key", "")
    base = os.environ.get("RECAPLY_PEXELS_BASE", "") or "https://api.pexels.com"
    if key:
        return Pexels(key, base)
    return Openverse(os.environ.get("RECAPLY_OPENVERSE_BASE", "") or "https://api.openverse.org")


def fetch_assets(spots: list[dict], folder: str, prov=None) -> list[tuple[dict, Asset]]:
    os.makedirs(folder, exist_ok=True)
    prov = prov or provider()
    out = []
    for i, s in enumerate(spots):
        try:
            a = prov.find(s["query"], folder, f"b{i}")
        except Exception as e:  # noqa: BLE001 — offline, rate limited, bad answer: skip this insert
            log.warning("b-roll search failed", query=s["query"], error=str(e)[:150])
            a = None
        if a:
            out.append((s, a))
    return out


# ---------- 3. lay it over ----------

def overlay(src: str, dst: str, inserts: list[tuple[dict, Asset]], size: tuple[int, int], has_audio: bool = True,
            sec: float = INSERT_SEC) -> str:
    """Cover `src` with each asset for `sec` seconds at its spot. Audio is copied from `src` unchanged."""
    from pipeline import ENCODER_ARGS, h264_encoder
    W, H = size
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", src]
    parts, last = [], "[0:v]"
    for k, (spot, a) in enumerate(inserts, 1):
        if a.kind == "video":
            cmd += ["-t", f"{sec:.2f}", "-i", a.path]
            pre = ""
        else:
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{sec:.2f}", "-i", a.path]
            pre = ""
        t0, t1 = spot["t"], spot["t"] + sec
        zoom = ",zoompan=z='min(zoom+0.0012,1.15)':d=1:s=%dx%d:fps=30" % (W, H) if a.kind == "image" else ""
        parts.append(f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{zoom},setsar=1,fps=30,"
                     f"setpts=PTS-STARTPTS+{t0:.3f}/TB{pre}[b{k}]")
        parts.append(f"{last}[b{k}]overlay=eof_action=pass:enable='between(t,{t0:.3f},{t1:.3f})'[v{k}]")
        last = f"[v{k}]"
    enc = h264_encoder()
    encoders = [enc] + (["libx264"] if enc != "libx264" else [])
    tmp = dst + ".part.mp4"
    err = ""
    for e in encoders:
        c = cmd + ["-filter_complex", ";".join(parts), "-map", last]
        if has_audio:
            c += ["-map", "0:a", "-c:a", "copy"]
        c += ["-c:v", e, *ENCODER_ARGS[e], "-movflags", "+faststart", tmp]
        r = subprocess.run(c, capture_output=True, text=True)
        err = r.stderr
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dst)
            return e
        if os.path.exists(tmp):
            os.remove(tmp)
    raise RuntimeError("Adding B-roll failed: " + err.strip()[-300:])


def add(src: str, dst: str, words: list[dict], duration: float, size: tuple[int, int], has_audio: bool,
        folder: str, llm: LLM | None = None, tel: Telemetry | None = None, prov=None) -> dict:
    """
    B-roll end to end. Returns {"file": dst or None, "inserts": [{t, query, source, credit}], "note": str}.
    `file` is None when nothing could be added (the clip stays as it was).
    """
    spots = moments(words, duration, llm, tel)
    if not spots:
        return {"file": None, "inserts": [], "note": "no good spots for B-roll in this clip"}
    got = fetch_assets(spots, folder, prov)
    if not got:
        return {"file": None, "inserts": [], "note": "no B-roll found (offline, or no match for what is said)"}
    overlay(src, dst, got, size, has_audio)
    return {"file": dst, "note": "", "inserts": [{"t": s["t"], "query": s["query"], "source": a.source, "credit": a.credit} for s, a in got]}
