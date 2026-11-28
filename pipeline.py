"""
pipeline.py — Core logic for the web-based highlight tool.

Two-phase design (this is the part that avoids downloading the full 5-6hr VOD):

  Phase 1: download ONLY the audio track (small, fast) and run highlight
           detection on it to find candidate timestamps.
  Phase 2: for each detected timestamp, download ONLY that short section of
           video (yt-dlp's --download-sections equivalent) — never the full file.

Detection method: audio energy (loudness) spikes relative to a rolling
baseline. This is a proxy for kills/aces/crowd reactions/caster hype and works
on any video with an audio track. For VCT broadcasts, round_detector.py reads
the overlay instead (streamed at 360p, never saved) to find whole rounds and
rank them by kills, clutches and crowd noise.
"""

import atexit
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import yt_dlp
from yt_dlp.utils import download_range_func


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.environ.get("RECAPLY_COOKIES") or os.path.join(BASE_DIR, "cookies.txt")


_cookies = {"mtime": None, "dir": None, "text": None, "problem": ""}
_cookies_lock = threading.Lock()
COOKIE_COPY_MAX_AGE = 6 * 3600       # per-call copies older than this are removed (a long download keeps its own)
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
COOKIES_HOWTO = ("Export it again with the \"Get cookies.txt LOCALLY\" browser extension (on youtube.com, "
                 "logged in) and replace cookies.txt. Until then Clipforge works without cookies.")


def cookie_copy():
    """
    A private working copy of the user's cookies.txt for one yt-dlp call.
    yt-dlp writes cookies back to its cookie file when it finishes (and
    YouTube rotates the session), which could strip the login cookies, so it
    never gets the user's own file. Every call gets its OWN copy: with one
    shared copy, a call saving its cookies (truncate, then write) while
    another was loading them made yt-dlp fail with "does not look like a
    Netscape format cookies file" (seen with chat replay + audio download
    running side by side).

    A file yt-dlp would reject is not passed on at all: yt-dlp runs without
    cookies and `cookie_problem()` says what to fix.
    """
    try:
        mtime = os.path.getmtime(COOKIES_FILE)
    except OSError:
        _cookies["problem"] = ""
        return None
    with _cookies_lock:
        if _cookies["mtime"] != mtime:
            try:
                with open(COOKIES_FILE, encoding="utf-8-sig", errors="replace") as f:
                    text = f.read()
                clean, problem = netscape_cookies(_as_netscape(text))
            except (OSError, ValueError, AttributeError, TypeError) as e:
                clean, problem = None, f"cookies.txt couldn't be read ({str(e)[:80]})."
            _cookies["text"] = clean
            _cookies["problem"] = f"{problem} {COOKIES_HOWTO}" if problem else ""
            _cookies["mtime"] = mtime
        if _cookies["text"] is None:
            return None
        if not (_cookies["dir"] and os.path.isdir(_cookies["dir"])):
            _cookies["dir"] = tempfile.mkdtemp(prefix="recaply_")      # private to this user (it holds a login)
            atexit.register(shutil.rmtree, _cookies["dir"], True)
        folder = _cookies["dir"]
        now = time.time()
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(p) > COOKIE_COPY_MAX_AGE:
                    os.remove(p)
            except OSError:
                pass
        fd, path = tempfile.mkstemp(prefix="cookies_", suffix=".txt", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_cookies["text"])
        return path


def cookie_problem():
    """Why cookies.txt is being ignored ("" when it's fine or there is none). Shown in the job's notes."""
    path = cookie_copy()                     # (re)reads cookies.txt when it changed
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    return _cookies["problem"]


def netscape_cookies(text):
    """
    (clean Netscape text, None) or (None, why not). Keeps the 7-field cookie
    lines (tab-separated; space-separated ones are repaired) including
    `#HttpOnly_` lines, and always writes the header line yt-dlp insists on.
    """
    good, bad = [], 0
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or (s.startswith("#") and not s.startswith("#HttpOnly_")):
            continue
        parts = s.split("\t")
        if len(parts) != 7:
            parts = s.split(None, 6)
        if len(parts) == 7 and parts[4].lstrip("-").isdigit() and parts[0]:
            good.append("\t".join(parts))
        else:
            bad += 1
    if not good:
        what = "has no cookie lines" if not bad else f"isn't in the Netscape cookies.txt format ({bad} unreadable lines)"
        return None, f"Your cookies.txt {what}, so it's being ignored."
    return NETSCAPE_HEADER + "\n" + "\n".join(good) + "\n", None


def _as_netscape(text):
    """
    yt-dlp reads the Netscape cookies.txt format. Browser extensions can also
    export JSON (a list of {domain, name, value, ...}); convert that.
    """
    if not text.lstrip().startswith(("[", "{")):
        return text
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies") or []
    lines = ["# Netscape HTTP Cookie File"]
    for c in data:
        domain = c.get("domain") or ""
        expires = c.get("expirationDate", c.get("expires")) or 0
        try:
            expires = int(float(expires))
        except (TypeError, ValueError):
            expires = 0
        if c.get("session") or expires < 0:
            expires = 0
        lines.append("\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(expires),
            c.get("name") or "",
            c.get("value") or "",
        ]))
    return "\n".join(lines) + "\n"


def base_opts(**extra):
    """yt-dlp options shared by every call."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    # YouTube needs a JS runtime to unlock all formats; yt-dlp only looks for
    # deno by default, but node works too.
    runtimes = {name: {} for name in ("deno", "node") if shutil.which(name)}
    if runtimes:
        opts["js_runtimes"] = runtimes
    # YouTube asks some IPs to "confirm you're not a bot"; a cookies.txt
    # exported from a logged-in browser gets past that.
    cookies = cookie_copy()
    if cookies:
        opts["cookiefile"] = cookies
    opts.update(extra)
    return opts


# ---------- Phase 1: audio-only fetch + analysis ----------

def download_audio(url: str, out_dir: str) -> str:
    """Download best-audio-only stream and convert to wav. Returns wav path."""
    base = os.path.join(out_dir, f"audio_{uuid.uuid4().hex[:8]}")
    ydl_opts = base_opts(**{
        "format": "bestaudio/best",
        "outtmpl": base + ".%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }],
        # Mono 16kHz is plenty for loudness analysis and keeps a 6hr VOD's
        # wav at ~700MB instead of ~3.8GB.
        "postprocessor_args": {"extractaudio": ["-ac", "1", "-ar", "16000"]},
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    wav_path = base + ".wav"
    if not os.path.exists(wav_path):
        raise RuntimeError("Audio download/convert failed — no wav produced.")
    return wav_path


def get_video_title(url: str) -> str:
    with yt_dlp.YoutubeDL(base_opts(skip_download=True)) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", "video")


def compute_energy_profile(wav_path: str, window_sec: float = 1.0):
    from pydub import AudioSegment          # loaded on first use: keeps the app's start fast
    # Force mono: multi-channel samples come back interleaved, which would
    # double every timestamp.
    audio = AudioSegment.from_wav(wav_path).set_channels(1)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    sr = audio.frame_rate

    window_size = int(window_sec * sr)
    n_windows = len(samples) // window_size
    trimmed = samples[: n_windows * window_size]
    windows = trimmed.reshape(n_windows, window_size)

    rms = np.sqrt(np.mean(windows ** 2, axis=1, dtype=np.float64) + 1e-9)
    times = np.arange(n_windows) * window_sec
    return times, rms


def find_highlight_windows(times, rms, sensitivity=1.8, min_gap_sec=20.0,
                            pre_pad=8.0, post_pad=5.0, smooth_sec=3.0,
                            window_sec=1.0, max_clips=25, max_clip_sec=None):
    smooth_n = max(1, int(smooth_sec / window_sec))
    kernel = np.ones(smooth_n) / smooth_n
    smoothed = np.convolve(rms, kernel, mode="same")

    baseline = np.median(smoothed)
    spread = np.std(smoothed)
    threshold = baseline + sensitivity * spread

    from scipy.signal import find_peaks     # ~2 s to import, so only when a recap needs it
    peak_idx, _ = find_peaks(
        smoothed, height=threshold,
        distance=max(1, int(min_gap_sec / window_sec)),
    )

    events = [{"time": float(times[i]), "score": float(smoothed[i] - baseline)} for i in peak_idx]
    events.sort(key=lambda e: e["time"])

    clips = []
    for e in events:
        start = max(0.0, e["time"] - pre_pad)
        end = e["time"] + post_pad
        # Merge spikes that overlap the previous clip (same round), unless
        # that would stretch the clip past max_clip_sec.
        merged_len = end - clips[-1]["start"] if clips else 0
        if (clips and start <= clips[-1]["end"] + 2.0
                and (max_clip_sec is None or merged_len <= max_clip_sec)):
            clips[-1]["end"] = max(clips[-1]["end"], end)
            if e["score"] > clips[-1]["score"]:
                clips[-1]["score"] = e["score"]
                clips[-1]["peak_time"] = e["time"]
        else:
            clips.append({"start": start, "end": end, "peak_time": e["time"], "score": e["score"]})

    clips.sort(key=lambda c: c["score"], reverse=True)
    clips = clips[:max_clips]
    clips.sort(key=lambda c: c["start"])
    return clips


# ---------- Round mode: stream the VOD at 360p to read the broadcast overlay ----------

# H.264 first: it decodes several times faster than AV1/VP9, and decoding is
# the bottleneck of the scan.
ANALYSIS_FORMAT = ("b[height<=360][height>=300][vcodec^=avc1]/"
                   "bv*[height<=360][height>=300][vcodec^=avc1]+ba[acodec^=mp4a]/"
                   "bv*[height<=360][height>=300][vcodec^=avc1]+ba/"
                   "b[height<=360][height>=300]/"
                   "bv*[height<=360][height>=300]+ba/"
                   "bv*[height<=480]+ba/b[height<=480]/b")


def resolve_source(url: str) -> dict:
    """
    Extract the VOD's info once: title, duration, the 360p stream URL(s) for
    analysis, and the raw info that clip downloads reuse (so each clip
    doesn't re-query YouTube/Twitch). A live (or just-ended, still
    processing) stream comes back as {"live": True, ...} without stream URLs:
    those are read piece by piece by live.py instead.
    """
    with yt_dlp.YoutubeDL(base_opts(skip_download=True)) as ydl:
        raw = ydl.extract_info(url, download=False, process=False)
        # A channel's /live link points at the video that's live right now.
        for _ in range(3):
            if raw.get("_type") not in ("url", "url_transparent") or not raw.get("url"):
                break
            raw = ydl.extract_info(raw["url"], download=False, process=False)
    if raw.get("live_status") in ("is_live", "post_live"):
        return {
            "live": True,
            "live_status": raw["live_status"],
            "title": raw.get("title") or "Live stream",
            "id": raw.get("id"),
            "extractor": (raw.get("extractor_key") or raw.get("extractor") or "").lower(),
            "webpage_url": raw.get("webpage_url") or url,
            "duration": 0.0,
        }
    with yt_dlp.YoutubeDL(base_opts(skip_download=True, format=ANALYSIS_FORMAT)) as ydl:
        info = ydl.process_ie_result(copy.deepcopy(raw), download=False)
    fmts = info.get("requested_formats") or [info]
    inputs = [f["url"] for f in fmts]
    headers = fmts[0].get("http_headers") or info.get("http_headers") or {}
    return {
        "title": info.get("title") or "video",
        "duration": float(info.get("duration") or 0),
        "inputs": inputs,
        "headers": headers,
        "raw": raw,
        "id": info.get("id"),
        "extractor": (info.get("extractor_key") or info.get("extractor") or "").lower(),
        "webpage_url": info.get("webpage_url") or url,
    }


HIRES_FORMAT = ("b[height=720][fps<=30][vcodec^=avc1]/bv*[height=720][fps<=30][vcodec^=avc1]/"
                "b[height=720][vcodec^=avc1]/bv*[height=720][vcodec^=avc1]/"
                "b[height=720]/bv*[height=720]/bv*[height<=1080]/b")
AUDIO_FORMAT = "ba[acodec^=mp4a]/ba/b[height<=360]/b"


def _pick(src: dict, fmt: str) -> dict:
    with yt_dlp.YoutubeDL(base_opts(skip_download=True, format=fmt)) as ydl:
        info = ydl.process_ie_result(copy.deepcopy(src["raw"]), download=False)
    f = (info.get("requested_formats") or [info])[0]
    return {"url": f["url"], "headers": f.get("http_headers") or info.get("http_headers") or {}}


def resolve_hires(src: dict) -> dict:
    """The 720p (30fps where offered) stream used to read names in the kill feed."""
    return _pick(src, HIRES_FORMAT)


def resolve_audio(src: dict) -> dict:
    """The audio-only stream (for loudness mode)."""
    return _pick(src, AUDIO_FORMAT)


def loudness_profile(src: dict, out_dir: str, workers: int = 8, progress=None):
    """
    Per-second loudness of the whole VOD, fast: HLS audio is decoded in
    parallel time chunks straight from the stream; a plain-HTTP audio file
    (YouTube) is fetched with parallel ranges and decoded locally.
    """
    aud = resolve_audio(src)
    duration = int(src["duration"]) or 0
    local = None
    url = aud["url"]
    if ".m3u8" not in url and url.startswith("http"):
        local = os.path.join(out_dir, f"audio_{uuid.uuid4().hex[:8]}.bin")
        fast_fetch(url, aud["headers"], local,
                   progress=(lambda f: progress(0.7 * f)) if progress else None)
        url = local
    chunk = max(300, duration // (workers * 3) or 300)
    starts = list(range(0, max(duration, 1), chunk))
    parts = [None] * len(starts)
    done = [0]
    lock = threading.Lock()

    def job(i):
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        if url.startswith("http") and aud["headers"]:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in aud["headers"].items())]
        cmd += ["-ss", str(starts[i]), "-t", str(chunk), "-i", url, "-vn",
                "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
        raw = b""
        for _attempt in range(3):
            raw = subprocess.run(cmd, capture_output=True).stdout
            if len(raw) >= 2 * 8000 * min(chunk, duration - starts[i]) * 0.95:
                break
        samples = np.frombuffer(raw, np.int16).astype(np.float32)
        n = len(samples) // 8000
        rms = (np.sqrt(np.mean(samples[: n * 8000].reshape(n, 8000) ** 2, axis=1) + 1e-9)
               if n else np.zeros(0))
        want = min(chunk, duration - starts[i])
        parts[i] = np.concatenate([rms, np.zeros(max(0, want - len(rms)))])[:want]
        with lock:
            done[0] += 1
            if progress:
                progress((0.7 if local else 0.0) + (0.3 if local else 1.0) * done[0] / len(starts))

    try:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(job, range(len(starts))))
    finally:
        if local and os.path.exists(local):
            os.remove(local)
    rms = np.concatenate(parts) if parts else np.zeros(0)
    return np.arange(len(rms), dtype=float), rms


def timestamp_url(src: dict, seconds: float) -> str:
    """Link that opens the VOD at `seconds`."""
    s = int(max(0, seconds))
    if "youtube" in src.get("extractor", ""):
        return f"https://www.youtube.com/watch?v={src['id']}&t={s}s"
    if "twitch" in src.get("extractor", ""):
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{src['webpage_url'].split('?')[0]}?t={h}h{m}m{sec}s"
    sep = "&" if "?" in src.get("webpage_url", "") else "?"
    return f"{src.get('webpage_url', '')}{sep}t={s}"


def needs_prefetch(src: dict) -> bool:
    """
    YouTube throttles long open-ended requests (what ffmpeg makes) to about
    playback speed, but serves short byte-range requests at full speed. So for
    plain-HTTP (non-HLS) sources, fetch the 360p file with parallel ranges
    first. HLS (Twitch) is already split into segments and streams fast.
    """
    return any(u.startswith("http") and ".m3u8" not in u for u in src["inputs"])


def fast_fetch(url: str, headers: dict, path: str, conns: int = 8,
               piece: int = 8 << 20, progress=None) -> str:
    """Download `url` to `path` with `conns` parallel byte-range requests."""
    m = re.search(r"[?&]clen=(\d+)", url)
    size = int(m.group(1)) if m else 0
    if not size:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            size = int(r.headers.get("Content-Length") or 0)
    if not size:
        raise RuntimeError("Unknown file size; can't fetch in parallel.")
    with open(path, "wb") as f:
        f.truncate(size)
    ranges = [(a, min(a + piece, size) - 1) for a in range(0, size, piece)]
    done = [0]
    lock = threading.Lock()

    def get(rng):
        a, b = rng
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={**headers, "Range": f"bytes={a}-{b}"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                if len(data) != b - a + 1:
                    raise IOError("short read")
                break
            except Exception:  # noqa: BLE001 — retried
                if attempt == 4:
                    raise
        with lock:
            with open(path, "r+b") as f:
                f.seek(a)
                f.write(data)
            done[0] += len(data)
            if progress:
                progress(done[0] / size)

    with ThreadPoolExecutor(conns) as ex:
        list(ex.map(get, ranges))
    return path


def prefetch_inputs(src: dict, out_dir: str, progress=None) -> list:
    """Fetch every input of `src` locally; returns the local paths."""
    paths = []
    total = len(src["inputs"])
    for i, url in enumerate(src["inputs"]):
        path = os.path.join(out_dir, f"analysis_{uuid.uuid4().hex[:8]}_{i}.bin")
        fast_fetch(url, src["headers"], path,
                   progress=(lambda f, i=i: progress((i + f) / total)) if progress else None)
        paths.append(path)
    return paths


# ---------- Fallback: download a low-res copy when the stream can't be read directly ----------

def download_analysis_video(url: str, out_dir: str, progress=None) -> str:
    """
    Download a 360p copy (with audio) of the whole VOD for overlay analysis.
    360p is the lowest resolution where the HUD's health bars stay readable;
    a 6hr VOD is roughly 1-2GB and is deleted once the rounds are found.
    """
    base = os.path.join(out_dir, f"analysis_{uuid.uuid4().hex[:8]}")
    state = {"last": 0}

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = int(d.get("downloaded_bytes", 0) * 100 / total)
                if pct >= state["last"] + 10:
                    state["last"] = pct - pct % 10
                    progress(pct)

    ydl_opts = base_opts(**{
        "format": ("b[height<=360][height>=300]/"
                   "bv*[height<=360][height>=300]+ba/"
                   "bv*[height<=480]+ba/b[height<=480]/b"),
        "outtmpl": base + ".%(ext)s",
        "merge_output_format": "mp4",
        # A multi-hour VOD is thousands of fragments; on Windows a fragment
        # occasionally fails or gets briefly locked (e.g. by antivirus). Retry
        # hard, and skip one that stays broken — a few missing seconds don't
        # matter for finding rounds.
        "concurrent_fragment_downloads": 4,
        "fragment_retries": 20,
        "retries": 20,
        "file_access_retries": 10,
        "skip_unavailable_fragments": True,
        "progress_hooks": [hook],
    })
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = ydl.prepare_filename(info)
            break
        except yt_dlp.utils.YoutubeDLError:
            if attempt == 2:
                raise
            # yt-dlp resumes from the .part file on the next attempt.
    if not os.path.exists(path):
        path = base + ".mp4"
    if not os.path.exists(path):
        raise RuntimeError("Analysis video download failed — no file produced.")
    return path


def loudness_per_second(video_path: str, sr: int = 8000):
    """Per-second RMS loudness of a local video's audio track."""
    cmd = ["ffmpeg", "-v", "error", "-i", video_path, "-vn", "-ac", "1",
           "-ar", str(sr), "-f", "s16le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, np.int16).astype(np.float32)
    n = len(samples) // sr
    if n == 0:
        return np.zeros(0)
    windows = samples[: n * sr].reshape(n, sr)
    return np.sqrt(np.mean(windows ** 2, axis=1) + 1e-9)


# ---------- Phase 2: section-only clip download ----------

CLIP_FORMATS = {
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "best": "bv*+ba/b",
}
# At the same resolution and frame rate, prefer H.264 video and AAC audio so
# most clips only need a quick remux, not a re-encode.
CLIP_SORT = ["res", "fps", "vcodec:h264", "acodec:aac"]


def download_clip_section(url: str, start: float, end: float, out_path: str,
                           accurate_cuts: bool = False, raw_info: dict = None,
                           quality: str = "1080", on_convert=None) -> str:
    """
    Downloads ONLY the [start, end] time range from the source, not the
    whole video, then converts it to a universally playable MP4 at
    `out_path`. Uses yt-dlp's download_ranges hook, which fetches just that
    slice rather than the full file. Pass `raw_info` (from resolve_source) to
    skip re-extracting the video page for every clip. `on_convert(fraction)`
    is called while converting. Returns how it was converted ("remux" or the
    encoder name).
    """
    # YouTube rarely serves combined audio+video above 360p, so grab the
    # separate streams and let ffmpeg merge them. MKV takes any codec pair;
    # the converter below turns it into a proper MP4.
    base, _ = os.path.splitext(out_path)
    src_base = base + ".src"
    for old in glob.glob(glob.escape(src_base) + ".*"):
        os.remove(old)
    ydl_opts = base_opts(**{
        "format": CLIP_FORMATS.get(quality, CLIP_FORMATS["1080"]),
        "format_sort": CLIP_SORT,
        "outtmpl": src_base + ".%(ext)s",
        "merge_output_format": "mkv",
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": accurate_cuts,
        "concurrent_fragment_downloads": 4,
        "fragment_retries": 10,
        "retries": 10,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if raw_info is not None:
            try:
                ydl.process_ie_result(copy.deepcopy(raw_info), download=True)
            except yt_dlp.utils.YoutubeDLError:
                # Stream URLs expire after a few hours; re-extract.
                ydl.download([url])
        else:
            ydl.download([url])
    produced = [p for p in glob.glob(glob.escape(src_base) + ".*")
                if not p.endswith((".part", ".ytdl")) and ".f" not in os.path.basename(p)[len(os.path.basename(src_base)):]]
    if not produced:
        raise RuntimeError("Clip download failed — nothing was saved.")
    src = max(produced, key=os.path.getsize)
    try:
        how = convert_to_mp4(src, out_path, progress=on_convert)
    finally:
        for p in glob.glob(glob.escape(src_base) + ".*"):
            os.remove(p)
    return how


# ---------- MP4 converter ----------
# Every clip ends up as H.264 video + AAC audio in an MP4 with the index at
# the front ("faststart"): plays in any browser, phone, editor or player.

MP4_VIDEO_CODECS = {"h264"}
MP4_AUDIO_CODECS = {"aac"}
# Quality-targeted, with a bitrate ceiling near what YouTube itself uses for
# H.264 1080p60 so busy gameplay doesn't balloon the file size.
ENCODER_ARGS = {
    "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "25", "-b:v", "5M", "-maxrate", "7M",
                   "-bufsize", "14M", "-pix_fmt", "yuv420p"],
    "h264_qsv":   ["-preset", "medium", "-global_quality", "25", "-maxrate", "7M", "-bufsize", "14M",
                   "-pix_fmt", "nv12"],
    "libx264":    ["-preset", "veryfast", "-crf", "23", "-maxrate", "7M", "-bufsize", "14M",
                   "-pix_fmt", "yuv420p"],
}
_encoder = None
_encoder_lock = threading.Lock()


def h264_encoder() -> str:
    """The fastest H.264 encoder that works on this machine (GPU first)."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            _encoder = "libx264"
            for enc in ("h264_nvenc", "h264_qsv"):
                test = subprocess.run(
                    ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
                     "-i", "color=c=black:s=1280x720:r=30", "-frames:v", "10",
                     "-c:v", enc, "-f", "null", "-"], capture_output=True)
                if test.returncode == 0:
                    _encoder = enc
                    break
        return _encoder


_http_chunked = None


def http_input_args() -> list[str]:
    """
    ffmpeg input options for reading a stream URL (put before its -i).
    YouTube serves one open-ended request at roughly playback speed, so a
    40 s clip took ~20 s to read; asking in 2 MB ranges over one kept-alive
    connection reads the same bytes in under a second. Older ffmpeg builds
    without -request_size just get the reconnect options.
    """
    global _http_chunked
    if _http_chunked is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "protocol=http"],
                                 capture_output=True, text=True, timeout=10).stdout
            _http_chunked = "-request_size" in out
        except (OSError, subprocess.SubprocessError):
            _http_chunked = False
    args = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    if _http_chunked:
        args += ["-multiple_requests", "1", "-request_size", "2000000", "-initial_request_size", "200000"]
    return args


def probe_media(path: str):
    """(video codec, audio codec, duration seconds) of a media file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
    return video, audio, float(info.get("format", {}).get("duration") or 0)


def convert_to_mp4(src: str, dst: str, progress=None) -> str:
    """
    Converts `src` to a compatible MP4 at `dst`. Streams that are already
    H.264/AAC are copied as-is (a remux takes a second); anything else is
    re-encoded, on the GPU when one is available. Returns "remux" or the
    video encoder used.
    """
    video, audio, duration = probe_media(src)
    if video is None:
        raise RuntimeError("Downloaded clip has no video stream.")
    copy_video = video in MP4_VIDEO_CODECS
    copy_audio = audio is None or audio in MP4_AUDIO_CODECS
    encoders = ["copy"] if copy_video else [h264_encoder()]
    if encoders[0] not in ("copy", "libx264"):
        encoders.append("libx264")  # fall back to CPU if the GPU chokes
    tmp = dst + ".part.mp4"
    for enc in encoders:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", src,
               "-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c:v", "copy"] if enc == "copy" else ["-c:v", enc, *ENCODER_ARGS[enc]]
        cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "160k"]
        cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if progress and duration and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration))
                except ValueError:
                    pass
        err = proc.stderr.read()
        if proc.wait() == 0 and os.path.exists(tmp):
            os.replace(tmp, dst)
            if progress:
                progress(1.0)
            return "remux" if enc == "copy" and copy_audio else ("remux+aac" if enc == "copy" else enc)
        if os.path.exists(tmp):
            os.remove(tmp)
    raise RuntimeError(f"MP4 conversion failed: {err.strip()[-300:]}")


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}h{m:02d}m{s:02d}s"


# Clip length presets: seconds before/after the spike and the merge cap.
# A Valorant round is ~2-3 min (30s buy + up to 100s action + 45s spike) and
# the loudest moment (clutch/ace/defuse) is usually near its end, so "round"
# reaches far back and only a little forward.
CLIP_MODES = {
    "round":  {"pre_pad": 90.0, "post_pad": 15.0, "max_clip_sec": 210.0},
    "moment": {"pre_pad": 8.0,  "post_pad": 5.0,  "max_clip_sec": 60.0},
}


SENSITIVITY_PRESETS = {
    "high": 1.3,    # more clips, lower bar
    "medium": 1.8,
    "low": 2.4,     # fewer clips, only the biggest spikes
}
