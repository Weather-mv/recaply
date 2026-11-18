"""
round_detector.py — find individual Valorant rounds in a VCT broadcast VOD and
score how intense each one was, by reading the broadcast overlay.

Calibrated on the 2026 VCT broadcast overlay. Three things are read from each
frame (sampled at 1 fps, scaled to 640x360 — all coordinates below are in that
space regardless of the source resolution):

  * Side player panels — each team's five players down the left/right edges,
    each with a coloured health bar that disappears when the player dies.
      - Round start = the panels appearing with (almost) all ten bars, which
        happens exactly when the barriers drop. They are not shown during the
        buy phase, replays, desk segments, interviews or player cams.
      - Round end   = the panels disappearing again.
      - Deaths      = a team's count of visible bars dropping and staying down
                      (counts, not rows: late in a round the overlay switches
                      to a "clutch view" that moves the survivors up).
  * Kill feed (top right) — one row per kill, killer on the left tinted in the
    killer's team colour, victim on the right in theirs. Every victim can die
    only once per round, so distinct victims (name + portrait) = kills, with
    exact timing and which team lost the player.
  * The broadcast's own "Red Bull CLUTCH" graphics — a banner in the middle of
    the screen when a clutch is won, and a corner tag on the post-round cam.
"""

import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from config import BASE_DIR

FRAME_W, FRAME_H = 640, 360
FRAME_BYTES = FRAME_W * FRAME_H * 3

# --- side panels ---
ROW_Y = (196, 231, 267, 302, 337)       # health bar rows, top to bottom
LEFT_X = (26, 108)                       # left team bar x-range
RIGHT_X = (532, 614)                     # right team bar x-range
BAR_MIN_PX = 4                           # saturated pixels for "bar present"

# --- kill feed ---
FEED_ROWS = tuple(38 + 12 * k for k in range(6))   # entry centre lines
FEED_X0, FEED_X1 = 440, 614              # coloured part of the entries
VICTIM_CROP_X = (584, 636)              # end of victim name + portrait
FEED_MATCH_DIFF = 26                     # mean abs diff for "same row"
FEED_MEMORY_SEC = 8                      # feed rows stay up ~5s

# --- broadcast clutch graphics (templates cut from a real broadcast) ---
ASSETS = os.path.join(BASE_DIR, "assets")   # the app's assets folder (also in the installed build)
CLUTCH_TEMPLATES = (
    ("center", (70, 110, 265, 375)),     # (y0, y1, x0, x1) search area
    ("corner", (2, 40, 530, 640)),
)
CLUTCH_MATCH = 0.75

# --- scoreboard (top centre): each team's round score ---
SCORE_BOXES = ((3, 18, 262, 292), (3, 18, 348, 378))   # (y0, y1, x0, x1) left, right
SCORE_CHANGED = 0.10     # share of a digit's pixels that differ -> score went up
SCORE_SAME = 0.07        # ...and below this the other score stayed put

START_MIN_ALIVE = 9      # bars visible for the panels to count as a round start
START_CONFIRM_SEC = 2    # ...for this many consecutive seconds
MIN_BUY_GAP_SEC = 20     # a real round is always preceded by a buy phase;
                         # "rounds" sooner than this after the last one are
                         # replays shown with the HUD
HIDDEN_END_SEC = 2       # no alive player's bar visible this long -> round over
DEATH_CONFIRM_SEC = 2    # bar must stay gone this long to count as a death
MAX_DROP_PER_SEC = 3     # bigger single-second drops are treated as occlusion
PANEL_MIN_PX = 20        # widest bar must be this wide for the panels to be up
MIN_ROUND_SEC = 15
CLUTCH_TAG_WINDOW = 25   # corner tag this soon after a round -> that round

TEAL, RED = 0, 1


def _color_masks(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sat = (s > 110) & (v > 110)
    return sat & (h >= 55) & (h <= 100), sat & ((h >= 160) | (h <= 8))


def _feed_masks(hsv):
    # The kill feed's colours are paler than the health bars (mint teal).
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sat = (s > 70) & (v > 120)
    return sat & (h >= 70) & (h <= 95), sat & ((h >= 170) | (h <= 10))


_templates = None


def _load_templates():
    global _templates
    if _templates is None:
        _templates = {}
        for name, _ in CLUTCH_TEMPLATES:
            img = cv2.imread(os.path.join(ASSETS, f"clutch_{name}.png"), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                _templates[name] = img
    return _templates


def frame_features(frame):
    """Everything the detector needs from one 640x360 BGR frame."""
    # Side panels. Team colours vary per match (teal/green or red/pink).
    bars, side_color = [], []
    for x0, x1 in (LEFT_X, RIGHT_X):
        hsv = cv2.cvtColor(frame[ROW_Y[0] - 6:ROW_Y[-1] + 6, x0:x1], cv2.COLOR_BGR2HSV)
        teal, red = _color_masks(hsv)
        colored = teal | red
        for y in ROW_Y:
            y -= ROW_Y[0] - 6
            line = colored[y - 1:y + 2].any(axis=0)
            # A health bar is a thin line: nothing coloured just above/below
            # it. This rejects player-cam strips and coloured scenery.
            off = colored[y - 5] | colored[y + 4]
            bars.append(int((line & ~off).sum()))
        side_color.append(RED if red.sum() > teal.sum() else TEAL)

    feed = _read_feed(frame)

    gray = None
    clutch = []
    for name, (y0, y1, x0, x1) in CLUTCH_TEMPLATES:
        tpl = _load_templates().get(name)
        if tpl is None:
            continue
        if gray is None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = cv2.matchTemplate(gray[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED).max()
        if score >= CLUTCH_MATCH:
            clutch.append(name)

    score = []
    for y0, y1, x0, x1 in SCORE_BOXES:
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        score.append(np.packbits((hsv[..., 2] > 170) & (hsv[..., 1] < 80)))

    return {"bars": bars, "colors": side_color, "feed": feed, "clutch": clutch, "score": score}


def _read_feed(frame):
    """Kill feed rows visible in this frame: (victim colour, identity crop)."""
    return [(color, crop) for _, color, crop, _ in read_feed_rows(frame)]


def read_feed_rows(frame):
    """
    Kill feed rows in a 640x360 frame: (row index, victim colour, identity
    crop, x where the victim's segment starts).
    """
    y0, y1 = FEED_ROWS[0] - 6, FEED_ROWS[-1] + 6
    region = frame[y0:y1, FEED_X0 - 30:VICTIM_CROP_X[0] + 30]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    teal, red = _feed_masks(hsv)
    ox = FEED_X0 - 30
    entries = []
    for row, yc in enumerate(FEED_ROWS):
        r = slice(yc - y0 - 3, yc - y0 + 4)
        cols = {TEAL: teal[r].any(axis=0), RED: red[r].any(axis=0)}
        # Victim segment: the last ~20px before the victim portrait.
        vx0, vx1 = 592 - ox, 612 - ox
        vt, vr = cols[TEAL][vx0:vx1].mean(), cols[RED][vx0:vx1].mean()
        if max(vt, vr) < 0.6:
            continue
        victim = RED if vr > vt else TEAL
        # Real feed rows also have a killer segment in the other colour.
        kx0, kx1 = 545 - ox, 572 - ox
        if cols[1 - victim][kx0:kx1].mean() < 0.4:
            continue
        # Where the victim's segment begins (the killer's part is left of it).
        x = vx1
        while x > 0 and cols[victim][max(0, x - 4):x].any():
            x -= 1
        # Identity = right-aligned victim name + portrait, in colour.
        crop = frame[yc - 6:yc + 7, VICTIM_CROP_X[0]:VICTIM_CROP_X[1]]
        entries.append((row, victim, crop.copy(), x + ox))
    return entries


# ---------- scanning ----------

def _ffmpeg_cmd(inputs, headers, ss, dur, audio_path):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    for n, inp in enumerate(inputs):
        if headers and inp.startswith("http"):
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        if n == 0:
            # Decoding is the bottleneck: skip B-frames and the deblocking
            # filter — 1 fps sampling doesn't need them. (Video input only:
            # an audio-only input rejects these options.)
            cmd += ["-skip_loop_filter", "all", "-skip_frame", "noref", "-flags2", "fast"]
        if ss:
            cmd += ["-ss", str(ss)]
        if dur:
            cmd += ["-t", str(dur)]
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"] \
            if inp.startswith("http") else []
        cmd += ["-i", inp]
    a_idx = len(inputs) - 1
    cmd += ["-map", "0:v:0", "-vf", f"fps=1,scale={FRAME_W}:{FRAME_H}",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    cmd += ["-map", f"{a_idx}:a:0?", "-ac", "1", "-ar", "8000", "-f", "s16le", "-y", audio_path]
    return cmd


def _scan_chunk(inputs, headers, ss, dur, tmp_dir):
    """Decode one time range: per-second frame features + audio loudness."""
    audio_path = os.path.join(tmp_dir, f"a_{int(ss)}.raw")
    cmd = _ffmpeg_cmd(inputs, headers, ss, dur, audio_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=FRAME_BYTES * 2)
    err = []
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    feats = []
    try:
        while dur is None or len(feats) < dur:
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            feats.append(frame_features(np.frombuffer(buf, np.uint8).reshape(FRAME_H, FRAME_W, 3)))
    finally:
        proc.stdout.close()
        proc.wait()
        t.join()
    loud = np.zeros(0)
    if os.path.exists(audio_path):
        samples = np.fromfile(audio_path, np.int16).astype(np.float32)
        os.remove(audio_path)
        n = len(samples) // 8000
        if n:
            loud = np.sqrt(np.mean(samples[: n * 8000].reshape(n, 8000) ** 2, axis=1) + 1e-9)
    if proc.returncode not in (0, None) and not feats:
        raise RuntimeError(f"ffmpeg failed at {ss}s: {err[0][-300:] if err else ''}")
    return feats, loud


def scan(inputs, duration, headers=None, workers=8, chunk_sec=600, progress=None):
    """
    Scan a whole VOD in parallel time chunks, straight from the stream URL(s)
    (or a local file). Returns (times, feats, loudness_per_second).
    """
    inputs = [inputs] if isinstance(inputs, str) else list(inputs)
    starts = list(range(0, int(duration), chunk_sec)) if duration else [0]
    lengths = [min(chunk_sec, int(duration) - s) for s in starts] if duration else [None]
    results = [None] * len(starts)
    done = [0]
    lock = threading.Lock()
    tmp_dir = tempfile.mkdtemp(prefix="vscan_")

    def job(i):
        feats, loud, error = [], np.zeros(0), None
        for _attempt in range(3):
            try:
                got, got_loud = _scan_chunk(inputs, headers, starts[i], lengths[i], tmp_dir)
            except Exception as e:      # noqa: BLE001 — retried, then re-raised
                error = e
                continue
            if len(got) > len(feats):
                feats, loud = got, got_loud
            # A short read means the stream dropped; retry the chunk.
            if lengths[i] is None or len(feats) >= lengths[i] - 3:
                break
        if not feats and error is not None:
            raise error
        if lengths[i]:
            # Pad a slightly short chunk so every later timestamp stays right.
            empty = {"bars": [0] * 10, "colors": [TEAL, RED], "feed": [], "clutch": [],
                     "score": None}
            feats = (feats + [empty] * lengths[i])[:lengths[i]]
            loud = np.concatenate([loud, np.zeros(max(0, lengths[i] - len(loud)))])[:lengths[i]]
        results[i] = (feats, loud)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(starts))

    try:
        with ThreadPoolExecutor(max(1, workers)) as ex:
            list(ex.map(job, range(len(starts))))
    finally:
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    feats = [f for r in results for f in r[0]]
    loud = np.concatenate([r[1] for r in results]) if results else np.zeros(0)
    return np.arange(len(feats), dtype=float), feats, loud


# ---------- rounds ----------

def segment_rounds(times, feats):
    """Split the timeline into live rounds with death times per team."""
    bars = np.array([f["bars"] for f in feats], dtype=np.int32).reshape(-1, 10)
    present = bars >= BAR_MIN_PX
    counts = np.stack([present[:, :5].sum(axis=1), present[:, 5:].sum(axis=1)], axis=1)
    widest = bars.max(axis=1) if len(bars) else np.zeros(0)
    n = len(times)

    rounds = []
    last_end = -1e9
    i = 0
    while i < n:
        confirmed = (
            i + START_CONFIRM_SEC <= n
            and (counts[i:i + START_CONFIRM_SEC].sum(axis=1) >= START_MIN_ALIVE).all()
            and times[i] - last_end >= MIN_BUY_GAP_SEC
        )
        if not confirmed:
            i += 1
            continue
        rnd, end_idx = _track_round(times, counts, widest, i)
        if rnd["end"] - rnd["start"] >= MIN_ROUND_SEC:
            rnd["start_idx"], rnd["end_idx"] = i, end_idx
            rounds.append(rnd)
            last_end = rnd["end"]
        i = end_idx + 1

    for k, rnd in enumerate(rounds):
        # Team colours: majority vote over the round's frames.
        cols = np.array([feats[j]["colors"] for j in range(rnd["start_idx"], rnd["end_idx"])])
        rnd["colors"] = [int(np.round(cols[:, 0].mean())), int(np.round(cols[:, 1].mean()))] \
            if len(cols) else [TEAL, RED]
        if rnd["colors"][0] == rnd["colors"][1]:
            rnd["colors"][1] = 1 - rnd["colors"][0]
        rnd["kills"] = _read_kills(times, feats, rnd)
        # Clutch graphics: centre banner during the round, corner tag on the
        # post-round cam (before the next round starts).
        nxt = rounds[k + 1]["start_idx"] if k + 1 < len(rounds) else len(feats)
        tag_end = min(nxt, rnd["end_idx"] + CLUTCH_TAG_WINDOW)
        rnd["broadcast_clutch"] = any(
            "center" in feats[j]["clutch"] for j in range(rnd["start_idx"], min(rnd["end_idx"] + 3, len(feats)))
        ) or any("corner" in feats[j]["clutch"] for j in range(rnd["end_idx"], tag_end))

    for k, rnd in enumerate(rounds):
        nxt = rounds[k + 1] if k + 1 < len(rounds) else None
        rnd["winner"] = _round_winner(feats, rnd, nxt)
    return rounds


def _score_masks(feats, lo, hi):
    """Pixel-majority of the two score digit masks over frames [lo, hi)."""
    got = [f["score"] for f in feats[max(0, lo):hi] if f.get("score") is not None]
    if not got:
        return None
    out = []
    for side in (0, 1):
        stack = np.array([np.unpackbits(g[side]) for g in got])
        out.append(stack.mean(axis=0) > 0.5)
    return out


def _round_winner(feats, rnd, nxt):
    """
    0/1 = which side won, from which team's score went up between this round
    and the next; None when it can't be told (last round of a map, where both
    scores reset). Falls back to "the other team was wiped out".
    """
    winner = None
    if nxt is not None:
        before = _score_masks(feats, rnd["start_idx"] + 1, rnd["start_idx"] + 8)
        after = _score_masks(feats, nxt["start_idx"] + 1, nxt["start_idx"] + 8)
        if before is not None and after is not None:
            # Changed pixels relative to the digit's own size.
            diff = [float((before[s] != after[s]).sum())
                    / max(1, int(before[s].sum()), int(after[s].sum())) for s in (0, 1)]
            if diff[0] >= SCORE_CHANGED and diff[1] < SCORE_SAME:
                winner = 0
            elif diff[1] >= SCORE_CHANGED and diff[0] < SCORE_SAME:
                winner = 1
    if winner is None:
        lost = [sum(1 for _, s in rnd["deaths"] if s == side) for side in (0, 1)]
        if lost[0] >= 5 > lost[1]:
            winner = 1
        elif lost[1] >= 5 > lost[0]:
            winner = 0
    return winner


def _track_round(times, counts, widest, start):
    """
    Follow one round from `start` until the panels go away, tracking how many
    players each team has alive. Returns (round dict, index where it ended).
    """
    alive = [5, 5]                      # everyone spawns alive
    pending = [None, None]              # (first_seen_idx, count) awaiting confirm
    deaths = []                         # (time, side) side 0=left 1=right
    last_visible = start
    hidden_run = 0

    k = start
    while k < len(times):
        obs = counts[k]
        # Every remaining bar vanishing at once is a camera cut (round over,
        # replay, player cam), not a double wipe.
        # A few stray coloured pixels on a camera cut must not read as
        # "everyone else died": the panels only count as up if at least one
        # bar is clearly there.
        if (obs.sum() == 0 or widest[k] < PANEL_MIN_PX
                or obs.sum() < sum(alive) - MAX_DROP_PER_SEC):
            hidden_run += 1
            if (obs.sum() == 0 or widest[k] < PANEL_MIN_PX) and hidden_run >= HIDDEN_END_SEC:
                break
            k += 1
            continue
        hidden_run = 0
        last_visible = k
        for side in (0, 1):
            seen = int(obs[side])
            if seen >= alive[side]:
                pending[side] = None    # extra bars = noise; teams never grow
                continue
            if pending[side] is None:
                pending[side] = (k, seen)
                continue
            first_k, first_seen = pending[side]
            if k - first_k + 1 >= DEATH_CONFIRM_SEC:
                new_alive = max(seen, first_seen)
                for _ in range(alive[side] - new_alive):
                    deaths.append((float(times[first_k]), side))
                alive[side] = new_alive
                pending[side] = None
        k += 1

    deaths.sort()
    rnd = {
        "start": float(times[start]),
        "end": float(times[last_visible]),
        "deaths": deaths,
    }
    return rnd, k


def _diff(a, b):
    """Mean abs difference of two feed crops, allowing a 1-2px misalignment
    (rows slide in/up with an animation)."""
    core = a[2:-2, 3:-3].astype(np.int16)
    h, w = core.shape[:2]
    best = 1e9
    for dy in range(5):
        for dx in range(7):
            win = b[dy:dy + h, dx:dx + w].astype(np.int16)
            best = min(best, float(np.abs(core - win).mean()))
    return best


def _read_kills(times, feats, rnd):
    """
    Kill events from the kill feed: [(time, victim_side)].

    Feed rows stay up ~5s and slide around as new ones arrive, so each row
    is matched against everything seen in the last FEED_MEMORY_SEC; only rows
    that match nothing are new kills. A new row must show up in a second
    frame too (filters rows caught mid-animation).
    """
    color_to_side = {rnd["colors"][0]: 0, rnd["colors"][1]: 1}
    tracks = []                         # [side, last_crop, first_t, last_t, seen]
    lo = rnd["start_idx"]
    hi = min(rnd["end_idx"] + 2, len(feats))
    for j in range(lo, hi):
        t = float(times[j])
        for vcol, crop in feats[j]["feed"]:
            side = color_to_side.get(vcol)
            if side is None:
                continue
            live = [tr for tr in tracks if tr[0] == side and t - tr[3] <= FEED_MEMORY_SEC
                    and tr[3] < t]
            best = min(live, key=lambda tr: _diff(tr[1], crop), default=None)
            if best is not None and _diff(best[1], crop) < FEED_MATCH_DIFF:
                best[1], best[3], best[4] = crop, t, best[4] + 1
            else:
                tracks.append([side, crop, t, t, 1])
    kills = {0: [], 1: []}
    for side, _, first_t, _, seen in tracks:
        if seen >= 2:
            kills[side].append((first_t, side))
    # A team can only lose five players.
    return sorted(kills[0][:5] + kills[1][:5])


def score_round(rnd, audio=None, names=None):
    """
    Intensity score plus the stats behind it. `names` (from killer_id) adds
    who got each kill: 3K / 4K / ACE and the clutch player's name.
    """
    audio = audio or {}
    feed = rnd.get("kills", [])
    # The health bars give the death count per team (the feed can double a
    # row or show a revived player twice); the feed gives exact timing, which
    # decides kill order in trades and so whether a clutch happened. If the
    # bars saw nothing for a team (overlay hidden), fall back to the feed.
    events = []
    for side in (0, 1):
        f = [(t, s) for t, s in feed if s == side]
        b = [(t, s) for t, s in rnd["deaths"] if s == side]
        if len(f) == len(b) or not b:
            events += f
        else:
            events += b
    events.sort()
    death_times = [t for t, _ in events]
    kills = len(death_times)

    # Biggest cluster of kills within 6 seconds (multi-kill / big trade fight).
    burst = 0
    for a in range(kills):
        b = a
        while b + 1 < kills and death_times[b + 1] - death_times[a] <= 6:
            b += 1
        burst = max(burst, b - a + 1)

    # Clutch: a side down to its last player against 2+ opponents. It's won
    # if that side wins the round (kills, defuse or spike) — the scoreboard
    # says who won. Without a known winner: won if the last player took
    # everyone else down and survived.
    winner = rnd.get("winner")
    bc = rnd.get("broadcast_clutch", False)
    alive = [5, 5]
    situations = {}                     # lone side -> (vs, kills after)
    for idx, (_, side) in enumerate(events):
        alive[side] -= 1
        for lone in (0, 1):
            if lone in situations or alive[lone] != 1 or alive[1 - lone] < 2:
                continue
            later = events[idx + 1:]
            kills_after = sum(1 for d in later if d[1] != lone)
            survived = not any(d[1] == lone for d in later)
            if winner is not None:
                won = winner == lone
            else:
                won = kills_after >= alive[1 - lone] and survived
            situations[lone] = (alive[1 - lone], kills_after, won)
    start_t = {}                        # lone side -> when it got down to one
    alive = [5, 5]
    for t, side in events:
        alive[side] -= 1
        for lone in (0, 1):
            if lone not in start_t and alive[lone] == 1 and alive[1 - lone] >= 2:
                start_t[lone] = t
    clutch, clutch_kills, clutch_won, clutch_side = 0, 0, False, None
    for lone, (vs, k, won) in situations.items():
        # Keep the one that matters most: a won clutch, else most kills.
        if (won, vs if won else k) > (clutch_won, clutch if clutch_won else clutch_kills):
            if won or k > 0:
                clutch, clutch_kills, clutch_won, clutch_side = vs, k, won, lone
    if bc and not clutch_won:
        # The broadcast called a clutch the overlay counts missed (e.g. the
        # panels were covered); trust it.
        clutch_won = True

    # Who did it (kill feed names).
    top_frags, top_player, clutch_player = 0, None, None
    if names and names.get("kills"):
        per = {}
        for _, killer, _ in names["kills"]:
            per[killer] = per.get(killer, 0) + 1
        top_player, top_frags = max(per.items(), key=lambda kv: kv[1])
        if clutch_side is not None:
            # Kills after the clutch began whose victim was on the other side
            # (matched to the nearest death by time) belong to the clutcher.
            after = {}
            for t, killer, _ in names["kills"]:
                if t < start_t.get(clutch_side, 1e18) - 1:
                    continue
                near = min(events, key=lambda e: abs(e[0] - t), default=None)
                if near and abs(near[0] - t) <= 3 and near[1] != clutch_side:
                    after[killer] = after.get(killer, 0) + 1
            if after:
                clutch_player = max(after.items(), key=lambda kv: kv[1])[0]

    peak_z = audio.get("peak_z", 0.0)
    roar = audio.get("roar_sec", 0)
    score = (
        kills
        + 1.5 * max(0, burst - 1)
        + (2.0 * clutch_kills + (1.5 * clutch + 1.0 if clutch_won else 0.0) if clutch else 0.0)
        + (4.0 if bc else 0.0)
        + {3: 2.0, 4: 5.0}.get(top_frags, 9.0 if top_frags >= 5 else 0.0)
        + 1.5 * max(0.0, min(peak_z, 3.0))
        + 0.25 * min(roar, 12)
    )
    return {
        "score": round(score, 1),
        "kills": kills,
        "burst": burst,
        "feed_kills": len(feed),
        "clutch": clutch,
        "clutch_won": clutch_won,
        "clutch_kills": clutch_kills,
        "winner": winner,
        "top_frags": top_frags,
        "top_player": top_player,
        "clutch_player": clutch_player,
        "broadcast_clutch": bc,
        "hype": round(peak_z, 1),
    }


def tags(stats):
    """Short badges for the UI."""
    out = []
    tf, who = stats.get("top_frags", 0), stats.get("top_player")
    if tf >= 3:
        label = "ACE" if tf >= 5 else f"{tf}K"
        out.append(f"{label} · {who}" if who else label)
    if stats["clutch"] >= 2:
        cp = stats.get("clutch_player")
        if stats["clutch_won"]:
            label = f"1v{stats['clutch']} won"
        else:
            k = stats["clutch_kills"]
            label = f"1v{stats['clutch']} ({k} kill{'s' if k > 1 else ''})"
        out.append(f"{label} · {cp}" if cp else label)
    if stats["broadcast_clutch"]:
        out.append("CLUTCH")
    if stats["burst"] >= 3:
        out.append(f"{stats['burst']} kills in 6s")
    if stats["hype"] >= 1.5:
        out.append("crowd hype")
    return out


def describe(stats):
    return " · ".join([f"{stats['kills']} kills"] + tags(stats))
