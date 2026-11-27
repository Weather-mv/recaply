"""
One channel's loop.

    waiting  the stream isn't live: check again every `poll_sec` (or stop, if wait_live is off)
    recording  the recorder is running; every TICK seconds: measure the new audio,
               score the recent past, report spikes, cut clips that are due
    ended    the stream has been gone for `end_grace` seconds (or max_hours ran out)
    stopped  the user stopped it

A spike at stream time T is cut as [T - pre, T + post] from the buffer once
T + post has been recorded, then handed to the normal clip pipeline in the
background while the recording carries on. Cuts run on their own thread, so a
slow one never holds up recording, scoring or reconnecting; the part of the
buffer a cut still needs is kept until it's done.

A connection that stays open but stops delivering (a frozen stream) is
dropped after STALL_SEC and reconnected like any other drop.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
import time
from typing import Callable

from recaply.live import links, notify
from recaply.live.buffer import Buffer
from recaply.live.chat import listener_for
from recaply.live.config import ChannelConfig
from recaply.live.detect import (SETTLE, LiveFeatures, LiveScorer, Spike, SpikeDetector, chat_is_live,
                                 signal_breakdown)
from recaply.live.recorder import Recorder
from recaply.log import get_logger
from recaply.profiles import get_profile
from recaply.settings import settings

log = get_logger("live.channel")
TICK = 5.0
QUICK_EXIT = 20.0            # a recorder that quits sooner than this found nothing to record
BACKOFF_MIN = 3.0            # after a dropped connection: try again in this many seconds...
BACKOFF_MAX = 30.0           # ...doubling every time it fails again, up to this
STABLE_SEC = 30.0            # a connection that held this long counts as healthy: the backoff starts over
STALL_SEC = 60.0             # recording alive but not growing this long = frozen connection: drop and reconnect
MAX_CHAT = 30000
Submit = Callable[[ChannelConfig, str, dict], str]


def _clock(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class ChannelWorker(threading.Thread):
    def __init__(self, cfg: ChannelConfig, root: str | None = None, submit: Submit | None = None,
                 tick: float | None = None):
        super().__init__(daemon=True, name=f"live-{cfg.name}")
        self.cfg = cfg
        self.root = root or os.path.join(settings.data_dir, "_live", cfg.name,
                                         time.strftime("%Y%m%d-%H%M%S"))
        self.buffer_dir = os.path.join(self.root, "buffer")
        self.clips_dir = os.path.join(self.root, "clips")
        self.tick = tick or TICK
        if submit is None:
            from recaply.live.pipeline import submit_clip
            submit = submit_clip
        self.submit = submit
        self.stop_flag = threading.Event()
        self.state = "starting"
        self.note = ""
        self.spikes = 0
        self.clips: list[dict] = []
        self.last_score = 0.0
        self.reconnects = 0
        self.reconnecting = False
        self._backoff = BACKOFF_MIN
        self._drop_len: float | None = None
        self.started = time.time()
        self.buf: Buffer | None = None
        self.chat = None
        self._chat_msgs: list[dict] = []
        self._chat_lock = threading.Lock()
        self._anchor = (0.0, time.time())            # (buffer length, wall clock) at the last refresh
        self.stalls = 0
        self._cuts: queue.Queue = queue.Queue()        # moments waiting for the cutter thread
        self._cutting: dict[int, float] = {}           # cut id -> its start (stream time): keep the buffer from there
        self._cut_ids = 0
        self._cut_lock = threading.Lock()
        self._cutter: threading.Thread | None = None

    # ---------- public ----------

    def stop(self) -> None:
        self.stop_flag.set()

    def status(self) -> dict:
        return {"name": self.cfg.name, "state": self.state, "note": self.note, "url": self.cfg.url,
                "profile": self.cfg.profile, "recording_sec": round(self.buf.length) if self.buf else 0,
                "spikes": self.spikes, "clips": list(self.clips), "score": round(self.last_score, 2),
                "reconnects": self.reconnects, "reconnecting": self.reconnecting, "stalls": self.stalls,
                "cuts_waiting": len(self._cutting),
                "buffer_mb": round(self.buf.size_bytes() / 1e6, 1) if self.buf else 0.0,
                "buffer_limit_mb": self.cfg.max_buffer_mb, "retention_min": self.cfg.retention_min,
                "chat": ({"connected": self.chat.connected, "messages": self.chat.count, "note": self.chat.note}
                         if self.chat else None),
                "started": self.started, "alive": self.is_alive()}

    # ---------- chat ----------

    def _on_chat(self, user: str, text: str) -> None:
        t0, w0 = self._anchor
        with self._chat_lock:
            self._chat_msgs.append({"t": t0 + (time.time() - w0), "text": text[:300], "user": user[:40]})
            if len(self._chat_msgs) > MAX_CHAT:
                del self._chat_msgs[:MAX_CHAT // 4]

    def _chat_snapshot(self) -> list[dict]:
        with self._chat_lock:
            return list(self._chat_msgs[-6000:])

    def _start_chat(self) -> None:
        if not self.cfg.chat:
            return

        def video_id() -> str:
            from recaply.ingest.sources import resolve_url
            return resolve_url(self.cfg.url).video_id

        lc, why = listener_for(self.cfg.url, self._on_chat, video_id)
        if lc is None:
            self.note = why
            return
        self.chat = lc
        lc.start()

    # ---------- the loop ----------

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            log.error("channel crashed", channel=self.cfg.name, error=str(e)[:300])
            self.state, self.note = "error", str(e)[:200]
            notify.send(self.cfg, "error", title=f"{self.cfg.name}: live mode stopped", text=str(e)[:300])
        finally:
            if self.chat:
                self.chat.stop()
            if self.buf:
                self.buf.finished = True

    def _run(self) -> None:
        cfg = self.cfg
        if links.ended(cfg.url):                    # one YouTube broadcast that is over: it's a video now, not a stream
            self.state, self.note = "ended", links.ENDED_NOTE
            return
        os.makedirs(self.clips_dir, exist_ok=True)
        buf = self.buf = Buffer(self.buffer_dir)
        profile = get_profile(cfg.profile)
        feats = LiveFeatures(buf.pcm)
        scorer = LiveScorer(profile)
        det = SpikeDetector(cfg.threshold, cfg.cooldown, cfg.warmup)
        pending: list[tuple[Spike, dict]] = []
        rec: Recorder | None = None
        went_live = False
        last_data = time.time()
        next_attempt = 0.0
        deadline = self.started + cfg.max_hours * 3600 if cfg.max_hours else None
        self._start_chat()
        self.state = "waiting"
        notify.send(cfg, "waiting", title=f"{cfg.name}: watching", text="Waiting for the stream to go live.")

        while not self.stop_flag.is_set():
            now = time.time()
            if deadline and now >= deadline:
                self.note = f"stopped after {cfg.max_hours:g} h"
                break
            # --- recorder
            if rec is None or not rec.alive():
                if rec is not None:
                    rec.stop()
                    ran = rec.ran_for()
                    quick = ran < QUICK_EXIT and buf.length <= self._anchor[0] + 0.5
                    rec_note = rec.tail() if quick else ""
                    rec = None
                    if went_live:                   # the connection dropped: wait a little longer after every failure
                        self._backoff = BACKOFF_MIN if ran >= STABLE_SEC else min(
                            BACKOFF_MAX, max(BACKOFF_MIN, cfg.end_grace / 3), self._backoff * 2)
                        next_attempt = now + self._backoff
                        self.reconnecting, self._drop_len = True, None
                        self.note = f"connection lost, reconnecting in {self._backoff:.0f}s"
                else:
                    quick, rec_note = False, ""
                if went_live and now - last_data >= cfg.end_grace:
                    self.note = "the stream ended"
                    break
                if not went_live and not cfg.wait_live and next_attempt:
                    self.note = "the channel isn't live" + (f" ({rec_note.splitlines()[-1][:100]})" if rec_note else "")
                    break
                if now >= next_attempt:
                    rec = Recorder(cfg, self.buffer_dir, buf.next_index())
                    try:
                        rec.start()
                    except OSError as e:
                        raise RuntimeError(f"can't start the recorder: {e}") from e
                    if went_live:
                        self.reconnects += 1
                        log.info("reconnecting", channel=cfg.name, attempt=self.reconnects)
                    else:
                        next_attempt = now + float(cfg.poll_sec)
                    if rec_note and not went_live:
                        self.note = f"not live yet ({rec_note.splitlines()[-1][:100]})"
            # --- measure and score
            segs = buf.refresh()
            length = buf.length
            if length > self._anchor[0] + 0.5:
                last_data = now
                if not went_live:
                    went_live = True
                    self.state = "recording"
                    self.note = ""
                    if rec is None or not rec.alive():      # it gave data and quit between two checks: retry soon, not in poll_sec
                        next_attempt = min(next_attempt, now + self._backoff)
                    notify.send(cfg, "recording", title=f"{cfg.name}: recording", text="The stream is live: recording and watching for moments.")
            if self.reconnecting and rec is not None and rec.alive():
                # reconnected = a NEW recorder is running and the recording grew past what it was when the link dropped
                if self._drop_len is None:
                    self._drop_len = length
                elif length > self._drop_len + 1.0:
                    self.reconnecting, self.note, self._drop_len = False, "", None
                    notify.send(cfg, "recording", title=f"{cfg.name}: reconnected", text="The connection is back: recording again.")
            if (went_live and rec is not None and rec.alive() and not self.reconnecting
                    and rec.ran_for() > STALL_SEC and now - last_data > STALL_SEC):
                # the connection is open but nothing new arrived: drop it; the next pass reconnects
                self.stalls += 1
                log.warning("stream stalled, reconnecting", channel=cfg.name, idle=round(now - last_data))
                rec.stop()
            self._anchor = (length, now)
            if went_live and segs:
                try:
                    new = feats.update(length)
                except Exception as e:  # noqa: BLE001 — a bad tick shouldn't end the recording
                    log.warning("measure failed", channel=cfg.name, error=str(e)[:200])
                    new = 0
                if new:
                    chat = self._chat_snapshot()
                    off, fused, sig = scorer.fused(feats.matrix(), chat, chat_is_live(chat, length))
                    horizon = int(length) - SETTLE
                    if len(fused):
                        self.last_score = float(fused[min(len(fused) - 1, max(0, horizon - off - 1))])
                    for sp in det.update(fused, off, horizon):
                        pending.append((sp, self._spike(sp, sig, off)))
            self._cut_due(pending, buf, ended=False)
            keep = length - cfg.retention_min * 60
            protect = self._protect_from(pending)
            keep = min(keep, protect)
            buf.prune(keep)
            buf.prune_to_size(int(cfg.max_buffer_mb * 1e6), protect)
            self.stop_flag.wait(self.tick if rec is not None and rec.alive() else min(self.tick, 2.0))

        # --- wind down
        stopped_by_user = self.stop_flag.is_set()
        if rec is not None:
            rec.stop()
        buf.finished = True
        buf.refresh()
        for sp in det.flush(int(buf.length)):
            pending.append((sp, self._spike(sp, {}, 0)))
        self._cut_due(pending, buf, ended=True)
        self._finish_cuts()
        self.state = "stopped" if stopped_by_user else "ended"
        if not self.note and stopped_by_user:
            self.note = "stopped"
        if went_live:
            notify.send(cfg, "stream_ended", title=f"{cfg.name}: {'stopped' if stopped_by_user else 'stream ended'}",
                        text=f"{len(self.clips)} clip(s) captured from {_clock(buf.length)} of stream.", clips=len(self.clips),
                        recorded=_clock(buf.length))
        elif not stopped_by_user and self.note:
            notify.send(cfg, "stream_ended", title=f"{cfg.name}: stopped", text=self.note)
        if not os.environ.get("RECAPLY_LIVE_KEEP_BUFFER"):
            shutil.rmtree(self.buffer_dir, ignore_errors=True)

    # ---------- spikes and clips ----------

    def clip_now(self, seconds: float) -> dict:
        """Cut the last `seconds` of the stream (what just happened) and hand it to the clip pipeline.
        The cut runs on the cutter thread, so the page gets its answer at once instead of waiting for
        ffmpeg; the clip appears in `clips` (and a "clip captured" note) when it is done. Raises
        RuntimeError with a reason the page can show when there is nothing to cut yet."""
        buf = self.buf
        if buf is None or self.state != "recording" or buf.length < 3:
            raise RuntimeError("Nothing is being recorded right now.")
        seconds = max(5.0, min(float(seconds), 600.0))
        t1 = buf.length
        t0 = max(buf.oldest, t1 - seconds)
        if t1 - t0 < 3:
            raise RuntimeError("Not enough has been recorded yet.")
        info = {"t": round(t1, 1), "clock": _clock(t1), "score": 0.0, "signals": {}, "manual": True,
                "start": round(t0, 1), "end": round(t1, 1)}
        self._queue_cut(Spike(t=int(t1), score=0.0, start=int(t0), end=int(t1)), info, t0, t1)
        return {**info, "queued": True}

    def _spike(self, sp: Spike, sig: dict, off: int) -> dict:
        self.spikes += 1
        info = {"t": sp.t, "clock": _clock(sp.t), "score": sp.score, "signals": signal_breakdown(sig, off, sp.t)}
        notify.send(self.cfg, "spike", title=f"{self.cfg.name}: moment at {info['clock']}",
                    text=f"Score {sp.score:.2f} ({', '.join(f'{k} {v}' for k, v in info['signals'].items())}). Cutting once it has played out.",
                    **{k: info[k] for k in ("t", "clock", "score", "signals")})
        return info

    # ---------- cutting (its own thread) ----------

    def _protect_from(self, pending: list) -> float:
        """Stream time from which the buffer must be kept: moments still waiting to be cut, and cuts in progress."""
        starts = [sp.t - self.cfg.pre for sp, _ in pending]
        with self._cut_lock:
            starts += list(self._cutting.values())
        return min(starts) - 5 if starts else float("inf")

    def _hold(self, t0: float) -> int:
        with self._cut_lock:
            return self._hold_locked(t0)

    def _hold_locked(self, t0: float) -> int:
        self._cut_ids += 1
        self._cutting[self._cut_ids] = t0
        return self._cut_ids

    def _release(self, cid: int) -> None:
        with self._cut_lock:
            self._cutting.pop(cid, None)

    def _cut_due(self, pending: list, buf: Buffer, ended: bool) -> None:
        """Hand every moment whose after-part has been recorded to the cutter thread."""
        for item in list(pending):
            sp, info = item
            if not ended and buf.length < sp.t + self.cfg.post:
                continue
            pending.remove(item)
            t0 = max(buf.oldest, sp.t - self.cfg.pre)
            t1 = min(buf.length, sp.t + self.cfg.post)
            if t1 - t0 < 3:
                continue
            self._queue_cut(sp, info, t0, t1)

    def _queue_cut(self, sp: Spike, info: dict, t0: float, t1: float) -> None:
        """Give one cut to the cutter thread (started when needed); the buffer from t0 is kept until it's done."""
        with self._cut_lock:
            self._cuts.put((sp, info, t0, t1, self._hold_locked(t0)))
            if self._cutter is None or not self._cutter.is_alive():
                self._cutter = threading.Thread(target=self._cut_loop, daemon=True, name=f"live-cut-{self.cfg.name}")
                self._cutter.start()

    def _cut_loop(self) -> None:
        """The cutter: one cut at a time, oldest first; it ends once nothing has come in for a moment."""
        while True:
            try:
                sp, info, t0, t1, cid = self._cuts.get(timeout=2.0)
            except queue.Empty:
                return
            try:
                self._cut_one(sp, info, t0, t1)
            finally:
                self._release(cid)

    def _finish_cuts(self, wait: float = 900.0) -> None:
        """At the end of the stream: let the queued cuts finish before the buffer is deleted."""
        end = time.time() + wait
        while time.time() < end:
            with self._cut_lock:
                busy = bool(self._cutting)
            if not busy:
                return
            if self._cutter is None or not self._cutter.is_alive():
                self._cut_loop()          # the cutter has gone: finish the rest here
                continue
            time.sleep(0.2)

    def _cut_one(self, sp: Spike, info: dict, t0: float, t1: float) -> None:
        buf = self.buf
        if buf is None:
            return
        out = os.path.join(self.clips_dir, f"clip_{int(sp.t):06d}{'_manual' if info.get('manual') else ''}.mp4")
        try:
            started = time.time()
            buf.cut(t0, t1, out)
            log.info("clip cut", channel=self.cfg.name, at=_clock(sp.t), seconds=round(time.time() - started, 1))
        except Exception as e:  # noqa: BLE001
            log.warning("cut failed", channel=self.cfg.name, error=str(e)[:200])
            notify.send(self.cfg, "error", title=f"{self.cfg.name}: couldn't cut the clip at {info['clock']}", text=str(e)[:300])
            return
        rec = {**info, "file": out, "start": round(t0, 1), "end": round(t1, 1)}
        try:
            rec["job"] = self.submit(self.cfg, out, rec)
        except Exception as e:  # noqa: BLE001
            log.error("clip job not started", channel=self.cfg.name, error=str(e)[:200])
            rec["error"] = str(e)[:200]
        self.clips.append(rec)
        notify.send(self.cfg, "clip_captured", title=f"{self.cfg.name}: clip captured",
                    text=(f"The last {int(t1 - t0)} s were cut" if info.get("manual") else f"{_clock(t0)} - {_clock(t1)} cut")
                    + "; ranking and titling it in the background.",
                    job=rec.get("job"), file=os.path.basename(out), t=sp.t, clock=info["clock"])
