"""
One channel's loop.

    waiting  the stream isn't live: check again every `poll_sec` (or stop, if wait_live is off)
    recording  the recorder is running; every TICK seconds: measure the new audio,
               score the recent past, report spikes, cut clips that are due
    ended    the stream has been gone for `end_grace` seconds (or max_hours ran out)
    stopped  the user stopped it

A spike at stream time T is cut as [T - pre, T + post] from the buffer once
T + post has been recorded, then handed to the normal clip pipeline in the
background while the recording carries on.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Callable

from recaply.live import notify
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
        self.started = time.time()
        self.buf: Buffer | None = None
        self.chat = None
        self._chat_msgs: list[dict] = []
        self._chat_lock = threading.Lock()
        self._anchor = (0.0, time.time())            # (buffer length, wall clock) at the last refresh

    # ---------- public ----------

    def stop(self) -> None:
        self.stop_flag.set()

    def status(self) -> dict:
        return {"name": self.cfg.name, "state": self.state, "note": self.note, "url": self.cfg.url,
                "profile": self.cfg.profile, "recording_sec": round(self.buf.length) if self.buf else 0,
                "spikes": self.spikes, "clips": list(self.clips), "score": round(self.last_score, 2),
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
                    quick = rec.ran_for() < QUICK_EXIT and buf.length <= self._anchor[0] + 0.5
                    rec_note = rec.tail() if quick else ""
                    rec = None
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
                    next_attempt = now + (3.0 if went_live else float(cfg.poll_sec))
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
                    notify.send(cfg, "recording", title=f"{cfg.name}: recording", text="The stream is live: recording and watching for moments.")
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
            if pending:
                keep = min(keep, min(s.t - cfg.pre for s, _ in pending) - 5)
            buf.prune(keep)
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

    def _spike(self, sp: Spike, sig: dict, off: int) -> dict:
        self.spikes += 1
        info = {"t": sp.t, "clock": _clock(sp.t), "score": sp.score, "signals": signal_breakdown(sig, off, sp.t)}
        notify.send(self.cfg, "spike", title=f"{self.cfg.name}: moment at {info['clock']}",
                    text=f"Score {sp.score:.2f} ({', '.join(f'{k} {v}' for k, v in info['signals'].items())}). Cutting once it has played out.",
                    **{k: info[k] for k in ("t", "clock", "score", "signals")})
        return info

    def _cut_due(self, pending: list, buf: Buffer, ended: bool) -> None:
        for item in list(pending):
            sp, info = item
            if not ended and buf.length < sp.t + self.cfg.post:
                continue
            pending.remove(item)
            t0 = max(buf.oldest, sp.t - self.cfg.pre)
            t1 = min(buf.length, sp.t + self.cfg.post)
            if t1 - t0 < 3:
                continue
            out = os.path.join(self.clips_dir, f"clip_{int(sp.t):06d}.mp4")
            try:
                buf.cut(t0, t1, out)
            except Exception as e:  # noqa: BLE001
                log.warning("cut failed", channel=self.cfg.name, error=str(e)[:200])
                notify.send(self.cfg, "error", title=f"{self.cfg.name}: couldn't cut the clip at {info['clock']}", text=str(e)[:300])
                continue
            rec = {**info, "file": out, "start": round(t0, 1), "end": round(t1, 1)}
            try:
                rec["job"] = self.submit(self.cfg, out, rec)
            except Exception as e:  # noqa: BLE001
                log.error("clip job not started", channel=self.cfg.name, error=str(e)[:200])
                rec["error"] = str(e)[:200]
            self.clips.append(rec)
            notify.send(self.cfg, "clip_captured", title=f"{self.cfg.name}: clip captured",
                        text=f"{_clock(t0)} - {_clock(t1)} cut; ranking and titling it in the background.",
                        job=rec.get("job"), file=os.path.basename(out), t=sp.t, clock=info["clock"])
