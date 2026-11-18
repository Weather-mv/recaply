"""
app.py — Recaply itself: recaps, clips and reels (the editor is in
editor_routes.py). In the Windows app (desktop.py) it serves the app's window
from 127.0.0.1 and accounts come from the online service (desktop_auth.py);
it can also still run as a self-hosted web app with local accounts (auth.py).

Workflow:
  1. Paste a VOD link -> analysis runs in the background (streams the VOD at
     360p and reads the overlay, or audio-only for loudness mode).
  2. The job page lists every round/moment with its timestamp, kills, clutch
     and hype badges; the best ones are pre-selected.
  3. Pick clips, set how many seconds to include before the round starts,
     and download them in parallel batches in full quality.

The rest lives in modules: desktop_auth / auth + accounts (sign-in, plans),
editor_routes (the video editor), common (shared helpers and work slots),
storage (optional files in Cloudflare R2), webutil (headers), config (settings).
"""

import glob
import json
import os
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from flask import (Flask, render_template, request, redirect, url_for, jsonify,
                   send_from_directory, Response, abort, flash)

import numpy as np

from pipeline import (
    find_highlight_windows, download_clip_section, fmt_ts, SENSITIVITY_PRESETS,
    CLIP_MODES, download_analysis_video, resolve_source, timestamp_url,
    needs_prefetch, prefetch_inputs, resolve_hires, loudness_profile,
)
import accounts
import killer_id
import round_detector
from live import LiveStream, LiveFrameSource
import reels
import editor_routes
import clip_routes
import storage
import webutil
from common import (JOB_ID, JOBS, JOBS_LOCK, ANALYSIS_SLOTS, RENDER_SLOTS, allowed_url, charge_failed,
                    ensure_mp4, fmt_hms, friendly_error, thumbnail_url)
from config import ALLOWED_HOSTS, CLIPS_DIR, DESKTOP, HOST, PORT, env_int

if DESKTOP:
    import desktop_auth as auth
else:
    import auth

# With a storage bucket set up, restore the database and download recap data
# before anything reads them. (Export worker processes skip this.)
if __name__ != "__mp_main__" and storage.ENABLED:
    storage.init(accounts.DB_PATH)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024   # forms and JSON bodies are tiny
# The editor's body limits run first (its uploads are big), then accounts and CSRF.
app.register_blueprint(editor_routes.bp)
app.register_blueprint(clip_routes.bp)      # the any-video clip finder
auth.init_app(app)

SCAN_WORKERS = env_int("RECAPLY_SCAN_WORKERS", os.cpu_count() or 4)
NAME_WORKERS = env_int("RECAPLY_NAME_WORKERS", 6)  # rounds whose kill feed is OCR'd at the same time
RETENTION_HOURS = env_int("RECAPLY_RETENTION_HOURS", 0)   # delete recaps older than this (0 = keep)
DEFAULT_LEAD_SEC = 12      # clip starts this long before the barriers drop
DEFAULT_TAIL_SEC = 3       # ...and runs this long past the end of the round
DEFAULT_PARALLEL = 3       # clips downloaded at the same time
QUALITIES = ("1080", "720", "best")


def set_status(job_id, **kwargs):
    with JOBS_LOCK:
        JOBS[job_id].update(kwargs)


def log(job_id, message):
    with JOBS_LOCK:
        JOBS[job_id]["log"].append(message)


def save_job(job_id):
    with JOBS_LOCK:
        data = json.dumps(JOBS[job_id])
    path = os.path.join(CLIPS_DIR, job_id, "job.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    storage.put_bytes(path, data)


def load_saved_jobs():
    for job_id in os.listdir(CLIPS_DIR):
        path = os.path.join(CLIPS_DIR, job_id, "job.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, ValueError):
            continue
        if job.get("live_state") in LIVE_ACTIVE:
            job["live_state"] = "stopped"
            job["live_round"] = False
            if job.get("candidates"):
                job["state"] = "ready"
        if job.get("state") == "analyzing":
            job["state"] = "error"
            job["error"] = "Server restarted during analysis."
        if job.get("names_state") == "running":
            job["names_state"] = "off"
        for d in job.get("downloads", {}).values():
            if d["state"] in ("queued", "downloading", "converting"):
                d["state"] = "failed"
                d["error"] = "Server restarted."
        for r in job.get("reels", {}).values():
            if r["state"] in ("queued", "rendering"):
                r["state"] = "failed"
                r["error"] = "Server restarted."
        JOBS[job_id] = job


# Resolved sources (with expiring stream URLs) live only in memory.
SOURCES = {}


# ---------- analysis ----------

# Per-job analysis data kept in memory so rounds can be re-scored when the
# kill feed names come in.
ROUND_DATA = {}


def run_rounds_analysis(job_id, url, read_names=True):
    """Round mode: read the VCT overlay to find whole rounds, rank by intensity."""
    job_dir = os.path.join(CLIPS_DIR, job_id)
    t0 = time.time()
    local_paths = []
    try:
        set_status(job_id, stage="Fetching video info")
        src = resolve_source(url)
        if src.get("live"):
            # Followed outside the analysis slot: it can run for hours and
            # only takes a slot while it's scanning.
            return lambda: follow_live(job_id, url, read_names)
        SOURCES[job_id] = src
        set_status(job_id, title=src["title"], duration=src["duration"])
        log(job_id, f"{src['title']} ({fmt_ts(src['duration'])})")

        chunk =int(max(180, min(600, src["duration"] / (SCAN_WORKERS * 4)))) or 600

        def progress(done, total):
            set_status(job_id, progress=done / total,
                       stage=f"Scanning gameplay ({done}/{total} chunks)")

        inputs, headers = src["inputs"], src["headers"]
        if needs_prefetch(src):
            # YouTube throttles ffmpeg's long reads; parallel ranges are fast.
            set_status(job_id, stage="Fetching 360p copy", progress=0.0)
            log(job_id, "Fetching a 360p copy with parallel connections...")
            local_paths = prefetch_inputs(
                src, job_dir, progress=lambda f: set_status(
                    job_id, progress=f, stage=f"Fetching 360p copy ({int(f * 100)}%)"))
            inputs, headers = local_paths, None
            log(job_id, f"Fetched in {time.time() - t0:.0f}s.")

        set_status(job_id, stage="Scanning gameplay", progress=0.0)
        log(job_id, f"Reading the overlay second by second ({SCAN_WORKERS} parallel streams)...")
        try:
            times, feats, loud = round_detector.scan(
                inputs, src["duration"], headers=headers,
                workers=SCAN_WORKERS, chunk_sec=chunk, progress=progress)
        except Exception as e:  # noqa: BLE001 — fall back to download
            if local_paths:
                raise
            log(job_id, f"Direct streaming failed ({e}); downloading a 360p copy instead...")
            set_status(job_id, stage="Downloading 360p copy", progress=0.0)
            local_paths = [download_analysis_video(
                url, job_dir, progress=lambda pct: set_status(job_id, progress=pct / 100))]
            times, feats, loud = round_detector.scan(
                local_paths, src["duration"], workers=SCAN_WORKERS,
                chunk_sec=chunk, progress=progress)
        for path in local_paths:
            if os.path.exists(path):
                os.remove(path)
        local_paths = []
        log(job_id, f"Scanned {fmt_ts(len(times))} of video in {time.time() - t0:.0f}s.")

        set_status(job_id, stage="Finding rounds")
        rounds = round_detector.segment_rounds(times, feats)
        if not rounds:
            set_status(job_id, state="ready", candidates=[], stage="Done", names_state="off")
            log(job_id, "No live rounds found. This mode needs a VCT broadcast "
                        "(side player panels with health bars on screen). "
                        "Try Loudness spikes mode.")
            save_job(job_id)
            return

        audio = round_audio(rounds, loud)
        ROUND_DATA[job_id] = {"rounds": rounds, "audio": audio}
        candidates = [candidate(src, n, rnd, a, None)
                      for n, (rnd, a) in enumerate(zip(rounds, audio), 1)]
        preselect(candidates, 10)
        n_clutch = sum(1 for c in candidates if c["clutch"])
        log(job_id, f"Found {len(rounds)} rounds, {n_clutch} clutches, in "
                    f"{time.time() - t0:.0f}s. Pick the ones to download.")
        set_status(job_id, state="ready", candidates=candidates, stage="Done", progress=1.0,
                   names_state="running" if read_names else "off", names_progress=0.0)
        save_job(job_id)
        if read_names:
            read_player_names(job_id, src, rounds, audio)
    except Exception as e:  # noqa: BLE001
        fail(job_id, e)
    finally:
        for path in local_paths:
            if os.path.exists(path):
                os.remove(path)


# ---------- live streams ----------

LIVE_WINDOW_SEC = 1800     # footage scanned per pass while catching up
LIVE_MIN_NEW_SEC = 60      # wait for this much new footage before scanning again
LIVE_POLL_SEC = 20
LIVE_SETTLE_SEC = 30       # a round is listed this long after it ends (winner,
                           # clutch tag and the scoreboard come in afterwards)
LIVE_ACTIVE = ("connecting", "catching_up", "live")
STOP_LIVE = set()


def live_wait(job_id):
    """Sleep between checks, waking at once when Stop is pressed."""
    for _ in range(LIVE_POLL_SEC):
        if job_id in STOP_LIVE:
            return
        time.sleep(1)


def follow_live(job_id, url, read_names):
    """
    Round mode for a YouTube live stream: scan everything broadcast so far
    (in 30-minute passes, so rounds show up early), then keep checking for
    new footage and list each round once it's over. Also finishes a stream
    that has just ended and is still being processed into a VOD.
    """
    job_dir = os.path.join(CLIPS_DIR, job_id)
    STOP_LIVE.discard(job_id)
    set_status(job_id, live=True, live_state="connecting", stage="Connecting to the live stream")
    feats, loud = [], np.zeros(0)
    names_raw = {}
    done = 0            # pieces scanned
    idle = 0            # checks in a row without new footage
    path = None
    try:
        stream = LiveStream(url)
        if not stream.readable:
            raise RuntimeError("This stream has ended and is now a regular video. "
                               "Start a new recap from the same link to analyse it.")
        src = {"title": stream.title, "id": stream.id, "extractor": "youtube",
               "webpage_url": stream.webpage_url, "stream": stream}
        SOURCES[job_id] = src
        seg = stream.seg
        set_status(job_id, title=stream.title, video_url=stream.webpage_url)
        log(job_id, f"{stream.title} (live, read from the start in {seg:g}s pieces)")
        frames = LiveFrameSource(stream, job_dir) if read_names else None

        while job_id not in STOP_LIVE and job_id in JOBS:
            head = stream.head()
            if head is None or idle >= 6:
                # Unreachable or not growing: the URL expired or the stream ended.
                stream.refresh()
                idle = 0
                if not stream.readable:
                    break
                head = stream.head()
                if head is None:
                    live_wait(job_id)
                    continue
            ended = stream.live_status != "is_live"
            new = head - done
            with JOBS_LOCK:
                JOBS[job_id].update(live_head=head * seg, live_checked=time.time())
            if new <= 0 or (new * seg < LIVE_MIN_NEW_SEC and not ended):
                if ended:
                    break
                idle += new <= 0
                live_wait(job_id)
                continue
            idle = 0
            n = min(new, max(1, int(LIVE_WINDOW_SEC // seg)))
            catching_up = head - done > n or not feats
            a, b = done * seg, (done + n) * seg
            first_pass = not feats
            set_status(job_id, live_state="catching_up" if head - done > 2 * LIVE_MIN_NEW_SEC / seg else "live")

            def fetch_progress(f):
                if first_pass:
                    set_status(job_id, progress=0.3 * f,
                               stage=f"Fetching 360p of the live stream ({int(f * 100)}%)")

            path = os.path.join(job_dir, f"live_{done}.mp4")
            stream.fetch_av("scan", done, done + n, path, progress=fetch_progress)
            if job_id in STOP_LIVE:
                break
            secs = int(round(b - a))

            def scan_progress(k, total):
                if first_pass:
                    set_status(job_id, progress=0.3 + 0.7 * k / total,
                               stage=f"Scanning gameplay ({k}/{total} chunks)")

            with ANALYSIS_SLOTS.take(JOBS[job_id].get("priority", 0)):
                _, f_new, l_new = round_detector.scan(
                    [path], secs, workers=SCAN_WORKERS,
                    chunk_sec=max(60, -(-secs // SCAN_WORKERS)), progress=scan_progress)
            os.remove(path)
            path = None
            feats += f_new
            loud = np.concatenate([loud, l_new])
            done += n
            set_status(job_id, live_state="catching_up" if head - done > 2 * LIVE_MIN_NEW_SEC / seg else "live")
            if catching_up:
                log(job_id, f"Scanned {fmt_ts(a)}–{fmt_ts(b)} of the broadcast.")
            live_update(job_id, src, feats, loud, names_raw, frames, final=False)
            save_job(job_id)

        stopped = job_id in STOP_LIVE
        if feats:
            live_update(job_id, src, feats, loud, names_raw, frames, final=not stopped)
        set_status(job_id, live_state="stopped" if stopped else "ended", live_round=False,
                   state="ready", stage="Done", progress=1.0, names_state="off")
        log(job_id, f"Stopped following at {fmt_ts(done * seg)}." if stopped
            else "The stream has ended. Every round is listed.")
    except Exception as e:  # noqa: BLE001
        with JOBS_LOCK:
            have = bool(JOBS.get(job_id, {}).get("candidates"))
        if have:
            # Keep the rounds found so far.
            set_status(job_id, live_state="error", live_round=False, names_state="off",
                       live_error=friendly_error(e)[:400])
            log(job_id, f"Following the stream stopped: {e}")
        else:
            fail(job_id, e)
    finally:
        STOP_LIVE.discard(job_id)
        if path and os.path.exists(path):
            os.remove(path)
        if job_id in JOBS:
            save_job(job_id)


def live_update(job_id, src, feats, loud, names_raw, frames, final):
    """Re-segment the footage so far and publish the rounds that are over."""
    times = np.arange(len(feats), dtype=float)
    rounds = round_detector.segment_rounds(times, feats)
    edge = len(feats)
    done_rounds = rounds if final else [r for r in rounds if r["end_idx"] < edge - LIVE_SETTLE_SEC]
    in_round = len(done_rounds) < len(rounds)
    audio = round_audio(done_rounds, loud)
    ROUND_DATA[job_id] = {"rounds": done_rounds, "audio": audio}

    def publish(stage=None):
        summaries = killer_id.summaries([names_raw.get(r["start_idx"]) for r in done_rounds]) \
            if names_raw else [None] * len(done_rounds)
        with JOBS_LOCK:
            job = JOBS[job_id]
            old = {c["id"]: c for c in job["candidates"]}
            fresh = [candidate(src, n, r, a, s)
                     for n, (r, a, s) in enumerate(zip(done_rounds, audio, summaries), 1)]
            # Resuming rescans from the start: keep rounds listed earlier
            # that the rescan hasn't reached yet.
            kept = [] if final else [c for c in job["candidates"] if c["id"] > len(fresh)]
            top = {c["id"] for c in sorted(fresh, key=lambda c: c["score"], reverse=True)[:10]}
            for c in fresh:
                if c["id"] in old:
                    c["selected"] = old[c["id"]].get("selected", False) or c["multikill"] >= 4
                else:
                    c["selected"] = c["id"] in top or c["clutch"] or c["multikill"] >= 4
            new_ids = [c["id"] for c in fresh if c["id"] not in old]
            job.update(candidates=fresh + kept, state="ready", stage=stage or "Following the stream",
                       progress=1.0, duration=float(edge), live_edge=float(edge), live_round=in_round,
                       names_version=job.get("names_version", 0) + 1,
                       names_state="off" if frames is None else job.get("names_state", "off"))
        return new_ids

    new_ids = publish()
    if new_ids and not final:
        log(job_id, "New round" + ("s" if len(new_ids) > 1 else "") + ": " +
            ", ".join(f"Round {i}" for i in new_ids))
    if frames is None:
        return
    todo = [r for r in done_rounds if r["start_idx"] not in names_raw
            and max(sum(1 for _, s in r.get("kills", []) if s == side) for side in (0, 1)) >= 3]
    if not todo:
        return
    set_status(job_id, names_state="running", names_progress=0.0)
    got = [0]

    def one(r):
        if job_id in STOP_LIVE:
            return
        try:
            names_raw[r["start_idx"]] = killer_id.read_round(frames, r)
        except Exception:  # noqa: BLE001 — that round just gets no names
            names_raw[r["start_idx"]] = []
        got[0] += 1
        set_status(job_id, names_progress=got[0] / len(todo))

    killer_id.ocr_engine()
    with ThreadPoolExecutor(NAME_WORKERS) as ex:
        list(ex.map(one, todo))
    set_status(job_id, names_state="done")
    publish()


def candidate(src, n, rnd, audio, names):
    stats = round_detector.score_round(rnd, audio=audio, names=names)
    return {
        "id": n,
        "start": rnd["start"],
        "end": rnd["end"],
        "label": f"Round {n}",
        "time": fmt_ts(rnd["start"]),
        "link": timestamp_url(src, rnd["start"]),
        "score": stats["score"],
        "kills": stats["kills"],
        "tags": round_detector.tags(stats),
        "clutch": stats["clutch_won"] or stats["broadcast_clutch"],
        "multikill": stats.get("top_frags", 0),
        "winner": stats.get("winner"),
        "info": round_detector.describe(stats),
        "killfeed": [[round(t - rnd["start"]), k, v] for t, k, v in (names or {}).get("kills", [])],
    }


def read_player_names(job_id, src, rounds, audio):
    """
    Second pass, while the user is already browsing the list: OCR the kill
    feed at 720p to see who got each kill, then re-score the rounds (3K / 4K
    / ACE, named clutches). Results stream into the job as they come.
    """
    t0 = time.time()
    try:
        hires = resolve_hires(src)
    except Exception as e:  # noqa: BLE001
        set_status(job_id, names_state="error", names_error=str(e))
        log(job_id, f"Could not open the 720p stream for player names: {e}")
        save_job(job_id)
        return
    log(job_id, "Reading player names from the kill feed (720p, OCR)...")

    def on_progress(done, total):
        set_status(job_id, names_progress=done / max(1, total))

    def on_update(summaries):
        with JOBS_LOCK:
            job = JOBS[job_id]
            old = {c["id"]: c for c in job["candidates"]}
            fresh = []
            for n, (rnd, a, names) in enumerate(zip(rounds, audio, summaries), 1):
                c = candidate(src, n, rnd, a, names)
                c["selected"] = old.get(n, {}).get("selected", False) or c["multikill"] >= 4
                fresh.append(c)
            job["candidates"] = fresh
            job["names_version"] = job.get("names_version", 0) + 1

    try:
        killer_id.attribute_rounds(hires["url"], hires["headers"], rounds,
                                   workers=NAME_WORKERS, progress=on_progress,
                                   on_update=on_update)
        with JOBS_LOCK:
            cands = JOBS[job_id]["candidates"]
        n_multi = sum(1 for c in cands if c["multikill"] >= 3)
        n_ace = sum(1 for c in cands if c["multikill"] >= 5)
        set_status(job_id, names_state="done", names_progress=1.0)
        log(job_id, f"Player names read in {time.time() - t0:.0f}s: "
                    f"{n_multi} rounds with a 3K or better, {n_ace} aces.")
    except Exception as e:  # noqa: BLE001
        set_status(job_id, names_state="error", names_error=str(e))
        log(job_id, f"Reading player names failed: {e}")
    save_job(job_id)


def round_audio(rounds, loud):
    """
    Sound cues per round: how loud its peak got compared with the other
    rounds (caster/crowd reaction), and how many seconds it stayed that loud.
    """
    if len(loud) == 0:
        return [{} for _ in rounds]
    smooth = np.convolve(loud, np.ones(3) / 3, mode="same")
    segs = [smooth[int(r["start"]):int(r["end"]) + 3] for r in rounds]
    peaks = np.array([float(s.max()) if len(s) else 0.0 for s in segs])
    in_round = np.concatenate([s for s in segs if len(s)]) if any(len(s) for s in segs) else smooth
    loud_level = np.percentile(in_round, 90)
    spread = peaks.std() or 1.0
    out = []
    for p, s in zip(peaks, segs):
        out.append({
            "peak_z": float((p - np.median(peaks)) / spread),
            "roar_sec": int((s > loud_level).sum()),
        })
    return out


def run_spikes_analysis(job_id, url, sensitivity_key, pre_pad, post_pad, max_clip_sec):
    job_dir = os.path.join(CLIPS_DIR, job_id)
    t0 = time.time()
    try:
        set_status(job_id, stage="Fetching video info")
        src = resolve_source(url)
        if src.get("live"):
            raise RuntimeError("Live streams work in Clutches & intense rounds mode. "
                               "Loudness spikes needs the finished video.")
        SOURCES[job_id] = src
        set_status(job_id, title=src["title"], duration=src["duration"])
        log(job_id, f"{src['title']} ({fmt_ts(src['duration'])})")

        set_status(job_id, stage="Measuring loudness", progress=0.0)
        log(job_id, "Reading the audio track in parallel (no video)...")
        times, rms = loudness_profile(
            src, job_dir, workers=24,
            progress=lambda f: set_status(job_id, progress=f,
                                          stage=f"Measuring loudness ({int(f * 100)}%)"))
        sensitivity = SENSITIVITY_PRESETS.get(sensitivity_key, 1.8)
        clips = find_highlight_windows(
            times, rms, sensitivity=sensitivity, pre_pad=pre_pad, post_pad=post_pad,
            max_clips=200, max_clip_sec=max_clip_sec)

        candidates = []
        for n, c in enumerate(clips, 1):
            candidates.append({
                "id": n,
                "start": c["start"],
                "end": c["end"],
                "label": f"Moment {n}",
                "time": fmt_ts(c["peak_time"]),
                "link": timestamp_url(src, c["start"]),
                "score": round(c["score"], 1),
                "kills": None,
                "tags": [],
                "clutch": False,
                "multikill": 0,
                "info": f"loudness spike {round(c['score'], 1)}",
                "killfeed": [],
            })
        preselect(candidates, 10)
        log(job_id, f"Found {len(candidates)} loud moments in {time.time() - t0:.0f}s. "
                    f"Pick the ones to download.")
        set_status(job_id, state="ready", candidates=candidates, stage="Done", progress=1.0,
                   names_state="off")
        save_job(job_id)
    except Exception as e:  # noqa: BLE001
        fail(job_id, e)


def preselect(candidates, n):
    """Tick the top-n by score, plus every clutch and 4K/ace."""
    top = {c["id"] for c in sorted(candidates, key=lambda c: c["score"], reverse=True)[:n]}
    for c in candidates:
        c["selected"] = c["id"] in top or c["clutch"] or c.get("multikill", 0) >= 4


def fail(job_id, e):
    set_status(job_id, state="error", error=friendly_error(e), stage="Failed")
    log(job_id, f"ERROR: {e}")
    log(job_id, traceback.format_exc())
    save_job(job_id)


# ---------- batch clip downloads ----------

# yt-dlp gives no progress while it fetches a time range, so the UI shows
# an estimate: download seconds per second of clip, learned as clips finish.
DOWNLOAD_RATE = {"sec_per_sec": 0.6}


def download_batch(job_id, ids, lead, tail, parallel, quality, refund=None):
    with JOBS_LOCK:
        job = JOBS[job_id]
        url = job["url"]
        by_id = {c["id"]: c for c in job["candidates"]}
        todo = []
        for cid in ids:
            c = by_id.get(cid)
            d = job["downloads"].get(str(cid))
            if c is None or (d and d["state"] in ("queued", "downloading", "converting")):
                continue
            start = max(0.0, c["start"] - lead)
            end = c["end"] + tail
            fname = f"{c['label'].replace(' ', '_').lower()}_{fmt_ts(start)}_{quality}.mp4"
            if d and d["state"] == "done" and d["file"] == fname:
                continue
            job["downloads"][str(cid)] = {"state": "queued", "file": fname,
                                          "start": start, "end": end}
            todo.append((cid, start, end, fname))
    save_job(job_id)
    raw = SOURCES.get(job_id, {}).get("raw")
    job_dir = os.path.join(CLIPS_DIR, job_id)
    stream = None
    if todo and job.get("live"):
        # While the stream is live (or still being processed) clips are cut
        # from its pieces; once it's a regular video, the normal path works.
        stream = SOURCES.get(job_id, {}).get("stream")
        if stream is None or not stream.readable:
            try:
                stream = LiveStream(url)
            except Exception:  # noqa: BLE001 — fall back to the VOD path
                stream = None
        if stream is not None and not stream.readable:
            stream = None

    def one(item):
        cid, start, end, fname = item
        d = JOBS[job_id]["downloads"][str(cid)]
        with JOBS_LOCK:
            d["state"] = "downloading"
            d["started"] = time.time()
            d["expected"] = round(max(3.0, DOWNLOAD_RATE["sec_per_sec"] * (end - start) * max(1, parallel) ** 0.5), 1)
            d.pop("progress", None)
        t = time.time()

        def on_convert(frac):
            with JOBS_LOCK:
                if d["state"] != "converting":
                    # learn how fast range downloads are for the next estimate
                    took = time.time() - t
                    rate = took / max(1.0, end - start) / max(1, parallel) ** 0.5
                    DOWNLOAD_RATE["sec_per_sec"] = round(0.6 * DOWNLOAD_RATE["sec_per_sec"] + 0.4 * rate, 3)
                d["state"] = "converting"
                d["progress"] = round(frac, 3)

        try:
            if stream is not None:
                how = stream.clip(start, end, os.path.join(job_dir, fname),
                                  quality=quality, on_convert=on_convert)
            else:
                how = download_clip_section(url, start, end, os.path.join(job_dir, fname),
                                            raw_info=raw, quality=quality, on_convert=on_convert)
            size = os.path.getsize(os.path.join(job_dir, fname))
            with JOBS_LOCK:
                d.update(state="done", size_mb=round(size / 1e6, 1),
                         seconds=round(time.time() - t), converted=how)
            storage.upload(os.path.join(job_dir, fname))
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                d.update(state="failed", error=friendly_error(e)[:400])
            if refund:
                refund([f"dl:{job_id}:{cid}"])
        save_job(job_id)

    if todo:
        log(job_id, f"Downloading {len(todo)} clips, {parallel} at a time...")
        t0 = time.time()
        with ThreadPoolExecutor(max(1, parallel)) as ex:
            list(ex.map(one, todo))
        with JOBS_LOCK:
            done = [JOBS[job_id]["downloads"][str(cid)] for cid, *_ in todo]
        remuxed = sum(1 for d in done if str(d.get("converted", "")).startswith("remux"))
        encoded = sum(1 for d in done if d.get("state") == "done" and not str(d.get("converted", "")).startswith("remux"))
        log(job_id, f"Batch finished in {time.time() - t0:.0f}s. MP4 (H.264 + AAC): "
                    f"{remuxed} repackaged, {encoded} re-encoded.")


@app.before_request
def webhook_body_limit():
    if request.path == "/billing/webhook":
        request.max_content_length = 1024 * 1024


webutil.install(app, webutil.csp(storage.origin()), auth.current_user)


def get_job(job_id):
    """The signed-in user's job for a URL segment, or a 404 (also for other people's
    recaps). Also stops `..` reaching the file system."""
    if not JOB_ID.match(job_id or "") or job_id not in JOBS or not auth.can_access(JOBS[job_id].get("owner")):
        abort(404)
    return JOBS[job_id]


def my_job(job_id):
    """Like get_job for ids from a request body: the job, or None."""
    job = JOBS.get(job_id) if JOB_ID.match(job_id or "") else None
    return job if job and auth.can_access(job.get("owner")) else None


def run_queued(job_id, target, args):
    """Run an analysis once a slot is free, so a busy server queues instead of thrashing."""
    set_status(job_id, stage="Waiting for a free slot")
    with ANALYSIS_SLOTS.take(JOBS[job_id].get("priority", 0)):
        after = target(*args)
    if callable(after):
        after()     # long-running follow-up (a live stream) that takes slots as it needs them


def busy(job):
    return job.get("state") == "analyzing" or job.get("names_state") == "running" or (
        job.get("live_state") in LIVE_ACTIVE) or any(
        d.get("state") in ("queued", "downloading", "converting") for d in job.get("downloads", {}).values()) or any(
        r.get("state") in ("queued", "rendering") for r in job.get("reels", {}).values())


def drop_jobs(ids):
    with JOBS_LOCK:
        for k in ids:
            JOBS.pop(k, None)
            SOURCES.pop(k, None)
            ROUND_DATA.pop(k, None)
    for k in ids:
        storage.delete_tree(os.path.join(CLIPS_DIR, k))


def delete_user_jobs(uid):
    with JOBS_LOCK:
        mine = [k for k, v in JOBS.items() if v.get("owner") == uid]
    STOP_LIVE.update(mine)
    drop_jobs(mine)


auth.ON_DELETE.append(delete_user_jobs)


def cleanup_loop():
    """Delete recaps (and their clips) and editor projects older than RECAPLY_RETENTION_HOURS."""
    P = editor_routes
    while True:
        cutoff = time.time() - RETENTION_HOURS * 3600
        with JOBS_LOCK:
            old = [k for k, v in JOBS.items() if v.get("created", 0) < cutoff and not busy(v)]
        drop_jobs(old)
        with P.PROJ_LOCK:
            stale = [k for k, p in P.PROJECTS.items() if (p.get("updated") or 0) < cutoff and not P.project_busy(p)]
            for k in stale:
                P.PROJECTS.pop(k, None)
        for k in stale:
            storage.delete_tree(P.proj_dir(k))
        time.sleep(600)


# ---------- routes ----------

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /$\nAllow: /pricing\nDisallow: /\n", mimetype="text/plain")


@app.route("/")
def landing():
    """The product website (in the Windows app, straight to the dashboard)."""
    if DESKTOP:
        return redirect(url_for("app_home"))
    return render_template("landing.html")


@app.route("/app")
def app_home():
    """The dashboard: start a recap and see your recent ones."""
    with JOBS_LOCK:
        recent = sorted(
            ({"id": k, "title": v.get("title") or v.get("url"), "state": v.get("state"),
              "created": v.get("created", 0), "mode": v.get("mode", "rounds"),
              "count": len(v.get("candidates") or []),
              "clutches": sum(1 for c in v.get("candidates") or [] if c.get("clutch")),
              "aces": sum(1 for c in v.get("candidates") or [] if c.get("multikill", 0) >= 5),
              "length": fmt_hms(v["duration"]) if v.get("duration") else "",
              "thumb": thumbnail_url(v.get("video_url") or v.get("url", "")),
              "live": v.get("live_state") in LIVE_ACTIVE,
              "platform": "twitch" if "twitch.tv" in (v.get("url") or "") else "youtube"}
             for k, v in JOBS.items() if auth.can_access(v.get("owner"))),
            key=lambda j: j["created"], reverse=True)[:12]
    for j in clip_routes.recent_jobs():
        recent.append({**j, "count": 0, "clutches": 0, "aces": 0, "length": "", "thumb": "", "live": False,
                       "mode": "clips", "platform": ""})
    recent = sorted(recent, key=lambda j: j["created"], reverse=True)[:12]
    return render_template("index.html", recent=recent, any_site="*" in ALLOWED_HOSTS,
                           profiles=clip_routes.profile_choices(), desktop_app=DESKTOP)


@app.route("/start", methods=["POST"])
def start():
    url = request.form.get("url", "").strip()
    if not url:
        return redirect(url_for("app_home"))
    ent = auth.ent()
    if not ent["can_create"]:
        flash(ent["reason"], "error")
        return redirect(url_for("app_home" if DESKTOP else "auth.pricing"))
    if not allowed_url(url):
        flash("Only YouTube and Twitch VOD links are supported.", "error")
        return redirect(url_for("app_home"))
    mode = "spikes" if request.form.get("detect") == "spikes" else "rounds"
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "state": "analyzing", "stage": "Starting", "progress": 0.0, "log": [],
            "candidates": [], "downloads": {}, "title": "", "url": url, "mode": mode,
            "created": time.time(), "owner": auth.uid(), "priority": ent["priority"],
            "default_lead": DEFAULT_LEAD_SEC if mode == "rounds" else 0,
            "default_tail": DEFAULT_TAIL_SEC if mode == "rounds" else 0,
        }
    os.makedirs(os.path.join(CLIPS_DIR, job_id), exist_ok=True)

    if mode == "rounds":
        read_names = request.form.get("names", "on") == "on"
        target, args = run_rounds_analysis, (job_id, url, read_names)
    else:
        sensitivity = request.form.get("sensitivity", "medium")
        preset = CLIP_MODES.get(request.form.get("clip_mode", "round"), CLIP_MODES["round"])
        try:
            pre_pad = min(180.0, max(0.0, float(request.form.get("pre_pad") or preset["pre_pad"])))
            post_pad = min(60.0, max(0.0, float(request.form.get("post_pad") or preset["post_pad"])))
        except ValueError:
            flash("Seconds before/after must be numbers.", "error")
            return redirect(url_for("app_home"))
        max_clip_sec = max(preset["max_clip_sec"], pre_pad + post_pad)
        target = run_spikes_analysis
        args = (job_id, url, sensitivity, pre_pad, post_pad, max_clip_sec)
    threading.Thread(target=run_queued, args=(job_id, target, args), daemon=True).start()
    return redirect(url_for("job_page", job_id=job_id))


@app.route("/job/<job_id>")
def job_page(job_id):
    get_job(job_id)
    return render_template("results.html", job_id=job_id)


@app.route("/api/job/<job_id>/status")
def job_status(job_id):
    get_job(job_id)
    with JOBS_LOCK:
        return jsonify({**JOBS[job_id], "now": time.time()})


@app.route("/api/job/<job_id>/download", methods=["POST"])
def job_download(job_id):
    get_job(job_id)
    body = request.get_json(force=True, silent=True) or {}
    try:
        ids = [int(i) for i in body.get("ids", [])][:500]
        lead = min(120.0, max(0.0, float(body.get("lead", DEFAULT_LEAD_SEC))))
        tail = min(60.0, max(0.0, float(body.get("tail", DEFAULT_TAIL_SEC))))
        parallel = min(8, max(1, int(body.get("parallel", DEFAULT_PARALLEL))))
    except (TypeError, ValueError):
        return jsonify({"error": "bad request"}), 400
    quality = body.get("quality", "1080")
    if quality not in QUALITIES:
        quality = "1080"
    if not ids:
        return jsonify({"error": "no clips selected"}), 400
    # Each round counts once against the plan (downloading it again is free).
    with JOBS_LOCK:
        busy_ids = {int(k) for k, d in JOBS[job_id]["downloads"].items()
                    if d.get("state") in ("queued", "downloading", "converting")}
        known = {c["id"] for c in JOBS[job_id]["candidates"]}
    ids = [i for i in dict.fromkeys(ids) if i in known and i not in busy_ids]
    ok, info = auth.charge([f"dl:{job_id}:{i}" for i in ids])
    if not ok:
        return charge_failed(info)
    parallel = min(parallel, auth.ent()["parallel"])
    threading.Thread(target=download_batch,
                     args=(job_id, ids, lead, tail, parallel, quality, auth.refunder()), daemon=True).start()
    return jsonify({"ok": True, "queued": len(ids)})


@app.route("/api/job/<job_id>/live", methods=["POST"])
def job_live(job_id):
    """Stop following a live stream, or resume following it (rescans from the start)."""
    job = get_job(job_id)
    action = (request.get_json(force=True, silent=True) or {}).get("action")
    if not job.get("live"):
        return jsonify({"error": "not a live stream"}), 400
    if action == "stop":
        STOP_LIVE.add(job_id)
        return jsonify({"ok": True})
    if action == "resume":
        with JOBS_LOCK:
            if job.get("live_state") in LIVE_ACTIVE or job.get("state") == "analyzing":
                return jsonify({"ok": True})
            job.update(live_state="connecting", live_error=None)
        threading.Thread(target=follow_live, args=(job_id, job["url"], True), daemon=True).start()
        return jsonify({"ok": True})
    return jsonify({"error": "bad request"}), 400


@app.route("/api/job/<job_id>/timestamps.txt")
def job_timestamps(job_id):
    job = get_job(job_id)
    lines = [job.get("title", ""), ""]
    for c in sorted(job["candidates"], key=lambda c: c["start"]):
        if request.args.get("all") or c.get("selected"):
            lines.append(f"{c['time']}  {c['label']}  {c['info']}  {c['link']}")
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/api/job/<job_id>/zip")
def job_zip(job_id):
    job = get_job(job_id)
    job_dir = os.path.join(CLIPS_DIR, job_id)
    # ?ids=3,7,12 zips just those clips (the current selection); otherwise every finished clip
    wanted = {i for i in request.args.get("ids", "").split(",") if i.strip()}
    done = sorted(((int(k), d["file"]) for k, d in job["downloads"].items()
                   if d["state"] == "done" and (not wanted or k in wanted)), key=lambda x: x[0])
    files = [f for _, f in done]
    files = [f for f in files if storage.fetch(os.path.join(job_dir, f))]
    if not files:
        return "no finished clips to zip", 400
    for f in files:
        ensure_mp4(os.path.join(job_dir, f))
    # Selection zips are one-off; Windows keeps a file locked while it's being sent,
    # so clear out older ones (a few minutes old) here instead of right after sending.
    for old in glob.glob(os.path.join(job_dir, "clips_*.zip")):
        try:
            if time.time() - os.path.getmtime(old) > 300:
                os.remove(old)
        except OSError:
            pass
    zip_name = f"clips_{uuid.uuid4().hex[:8]}.zip" if wanted else "clips.zip"
    zip_path = os.path.join(job_dir, zip_name)
    # Videos don't compress; store them so zipping is instant.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(os.path.join(job_dir, f), f)
    resp = send_from_directory(job_dir, zip_name, as_attachment=True,
                               download_name=f"recaply_{job_id}{f'_{len(files)}clips' if wanted else ''}.zip")
    return resp


@app.route("/clips/<job_id>/<path:filename>")
def serve_clip(job_id, filename):
    """A downloaded clip or reel (?dl=1 saves it instead of playing it)."""
    get_job(job_id)
    if not filename.lower().endswith(".mp4") or "/" in filename or "\\" in filename:
        abort(404)
    job_dir = os.path.join(CLIPS_DIR, job_id)
    path = os.path.join(job_dir, filename)
    if os.path.exists(path) and ensure_mp4(path):
        storage.upload(path)        # an old clip was converted to H.264: replace the stored one
    return editor_routes.send_media(job_dir, filename, filename if request.args.get("dl") else None)


# ---------- reels (9:16) ----------

def render_reel(job_id, key):
    """Render one queued reel (waits for a free encoder slot)."""
    job = JOBS[job_id]
    job_dir = os.path.join(CLIPS_DIR, job_id)
    with RENDER_SLOTS.take(job.get("priority", 0)):
        with JOBS_LOCK:
            r = job["reels"][key]
            src = os.path.join(job_dir, job["downloads"][str(r["cid"])]["file"])
            r.update(state="rendering", progress=0.0, started=time.time())
        t = time.time()

        def progress(f):
            with JOBS_LOCK:
                r["progress"] = round(f, 3)

        try:
            if not storage.fetch(src):
                raise RuntimeError("The clip's file is missing. Download the round again.")
            ensure_mp4(src)
            enc = reels.make_reel(src, os.path.join(job_dir, r["file"]), r["layout"], r.get("opts"),
                                  r.get("style"), r.get("start", 0), r.get("end"), r.get("text", ""),
                                  r.get("size", "1080"), progress=progress, watermark=r.get("watermark", False))
            size = os.path.getsize(os.path.join(job_dir, r["file"]))
            with JOBS_LOCK:
                r.update(state="done", progress=1.0, size_mb=round(size / 1e6, 1),
                         seconds=round(time.time() - t), encoder=enc)
            storage.upload(os.path.join(job_dir, r["file"]))
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                r.update(state="failed", error=str(e)[:300])
    save_job(job_id)


@app.route("/reels")
def reels_page():
    return render_template("reels.html", layouts=reels.LAYOUTS, defaults=reels.DEFAULTS,
                           split_base=reels.SPLIT_BASE, style_defaults=reels.STYLE_DEFAULTS,
                           text_max=reels.TEXT_MAX, text_lines=reels.TEXT_LINES,
                           killfeed=reels.KILLFEED, kf_width=reels.KF_WIDTH,
                           has_font=bool(reels.font_file()))


@app.route("/api/reels")
def reels_status():
    """Every downloaded clip (grouped by recap, newest first) with its reels."""
    out = []
    with JOBS_LOCK:
        for job_id, job in sorted(JOBS.items(), key=lambda kv: kv[1].get("created", 0), reverse=True):
            if not auth.can_access(job.get("owner")):
                continue
            by_id = {str(c["id"]): c for c in job.get("candidates") or []}
            clips = []
            for cid, d in job.get("downloads", {}).items():
                if d.get("state") != "done":
                    continue
                c = by_id.get(cid, {})
                clips.append({
                    "cid": int(cid), "file": d["file"], "size_mb": d.get("size_mb"),
                    "label": c.get("label") or f"Clip {cid}", "time": fmt_hms(c.get("start", 0)),
                    "kills": c.get("kills"), "tags": (c.get("tags") or [])[:3],
                    "duration": round(d.get("end", 0) - d.get("start", 0)),
                    "reels": {r["layout"]: {k: r.get(k) for k in
                                            ("state", "file", "progress", "size_mb", "size", "start", "end",
                                             "text", "error")}
                              for r in job.get("reels", {}).values() if r["cid"] == int(cid)},
                })
            if clips:
                clips.sort(key=lambda c: c["cid"])
                out.append({"id": job_id, "title": job.get("title") or job.get("url"),
                            "live": job.get("live_state") in LIVE_ACTIVE, "clips": clips})
    return jsonify({"jobs": out})


@app.route("/api/reels", methods=["POST"])
def reels_make():
    body = request.get_json(force=True, silent=True) or {}
    layout = body.get("layout")
    size = body.get("size") if body.get("size") in reels.SIZES else "1080"
    # items: [{job, cid, start, end, text}] (trim and caption are per clip)
    items = []
    try:
        for it in (body.get("items") or [])[:200]:
            if isinstance(it, dict):
                start = max(0.0, float(it.get("start") or 0))
                end = float(it["end"]) if it.get("end") else None
                if end is not None and end <= start:
                    end = None
                items.append((str(it.get("job")), int(it.get("cid")), start, end,
                              reels.clean_text(it.get("text"))))
            else:
                items.append((str(it[0]), int(it[1]), 0.0, None, ""))
    except (TypeError, ValueError, KeyError, IndexError):
        return jsonify({"error": "bad request"}), 400
    if layout not in reels.LAYOUTS or not items:
        return jsonify({"error": "bad request"}), 400
    opts = reels.clean_opts(layout, body.get("opts"))
    style = reels.clean_style(body.get("style"))
    watermark = auth.ent()["watermark"]
    queued = []
    with JOBS_LOCK:
        for job_id, cid, start, end, text in items:
            job = my_job(job_id)
            d = job and job.get("downloads", {}).get(str(cid))
            if not d or d.get("state") != "done":
                continue
            key = f"{cid}-{layout}"
            old = job.setdefault("reels", {}).get(key)
            if old and old["state"] in ("queued", "rendering"):
                continue
            fname = f"reel_{layout}{'_720' if size == '720' else ''}_{d['file']}"
            if old and old.get("file") != fname:
                # A different output size makes a new file; drop the old one.
                storage.delete(os.path.join(CLIPS_DIR, job_id, old["file"]))
            job["reels"][key] = {"cid": cid, "layout": layout, "opts": opts, "style": style, "size": size,
                                 "start": start, "end": end, "text": text, "watermark": watermark,
                                 "state": "queued", "progress": 0.0, "file": fname}
            queued.append((job_id, key))
    for job_id, key in queued:
        threading.Thread(target=render_reel, args=(job_id, key), daemon=True).start()
    return jsonify({"ok": True, "queued": len(queued), "watermark": watermark})


@app.route("/api/job/<job_id>/frame/<int:cid>.jpg")
def clip_frame(job_id, cid):
    """A still from a downloaded clip, for the reel layout preview."""
    job = get_job(job_id)
    d = job.get("downloads", {}).get(str(cid))
    if not d or d.get("state") != "done":
        abort(404)
    job_dir = os.path.join(CLIPS_DIR, job_id)
    src, jpg = os.path.join(job_dir, d["file"]), os.path.join(job_dir, f"frame_{cid}.jpg")
    stale = os.path.exists(jpg) and os.path.exists(src) and os.path.getmtime(jpg) < os.path.getmtime(src)
    if stale or not storage.fetch(jpg):
        if not storage.fetch(src) or not reels.frame_jpg(src, jpg):
            abort(404)
        storage.upload(jpg)
    resp = send_from_directory(job_dir, f"frame_{cid}.jpg")
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@app.route("/api/reels/zip")
def reels_zip():
    """?keys=job:cid:layout,... zips those finished reels."""
    files = []
    for key in request.args.get("keys", "").split(",")[:200]:
        try:
            job_id, cid, layout = key.split(":")
        except ValueError:
            continue
        job = my_job(job_id)
        r = job and job.get("reels", {}).get(f"{cid}-{layout}")
        if r and r.get("state") == "done" and storage.fetch(os.path.join(CLIPS_DIR, job_id, r["file"])):
            files.append((os.path.join(CLIPS_DIR, job_id, r["file"]), r["file"]))
    if not files:
        return "no finished reels to zip", 400
    zip_dir = os.path.join(CLIPS_DIR, "_zips")
    os.makedirs(zip_dir, exist_ok=True)
    for old in glob.glob(os.path.join(zip_dir, "*.zip")):
        try:
            if time.time() - os.path.getmtime(old) > 300:
                os.remove(old)
        except OSError:
            pass
    name = f"reels_{uuid.uuid4().hex[:8]}.zip"
    with zipfile.ZipFile(os.path.join(zip_dir, name), "w", zipfile.ZIP_STORED) as z:
        for path, arc in files:
            z.write(path, arc)
    return send_from_directory(zip_dir, name, as_attachment=True,
                               download_name=f"recaply_{len(files)}reels.zip")


# Worker processes (editor exports draw frames in a process pool) import this
# file as "__mp_main__": they must not load jobs or start background threads.
if __name__ != "__mp_main__":
    load_saved_jobs()
    editor_routes.load_projects()
    clip_routes.resume_jobs()
    if RETENTION_HOURS > 0:
        threading.Thread(target=cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    # Jobs live in this process's memory, so run ONE process with many threads.
    host, port = HOST, PORT
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        from waitress import serve
    except ImportError:
        print(f"Recaply (dev server) is running. Open http://{shown}:{port} in your browser.")
        app.run(host=host, port=port, debug=False, threaded=True)
    else:
        print(f"Recaply is running. Open http://{shown}:{port} in your browser.")
        serve(app, host=host, port=port, threads=env_int("RECAPLY_THREADS", 16),
              channel_timeout=300, ident="Recaply")
