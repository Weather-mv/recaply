"""
From a captured moment to a finished clip: the cut file becomes a normal clip
job (the same analyze -> rank -> title pipeline as any video), optionally
followed by a rendered short, and a "clip ready" notification when it's done.
"""

from __future__ import annotations

import os
import threading
import time

from recaply.jobs import store
from recaply.jobs.queue import get_queue
from recaply.live import notify
from recaply.live.config import ChannelConfig
from recaply.log import get_logger

log = get_logger("live.pipeline")
POLL = 2.0
MAX_WAIT = 3600.0
LIVE_PRIORITY = 3            # above every plan's priority (slots.py: 0 free, 1 starter, 2 pro)


def submit_clip(cfg: ChannelConfig, path: str, info: dict) -> str:
    """Create and queue the job for one captured moment. Returns the job id."""
    opts = {"profile": cfg.profile, "count": cfg.count, "max_len": min(180.0, cfg.pre + cfg.post),
            "transcriber": cfg.transcriber, "llm": cfg.llm}
    title = f"{cfg.name} · live clip at {info.get('clock', '')}".strip(" ·")
    # a live moment is short and wanted now: it goes ahead of long video analyses waiting for a slot
    jid = store.create(path, {k: v for k, v in opts.items() if v is not None}, title=title, owner=cfg.owner,
                       priority=LIVE_PRIORITY)
    get_queue().submit(jid, "analyze", priority=LIVE_PRIORITY)
    threading.Thread(target=follow, args=(cfg, jid, info), daemon=True, name=f"live-follow-{jid}").start()
    return jid


def _wait(jid: str, task: str | None = None) -> dict | None:
    """Poll until the job is ready/failed (for `task` when given). None on timeout."""
    end = time.time() + MAX_WAIT
    while time.time() < end:
        j = store.get(jid)
        if j is None:
            return None
        if j["state"] in ("ready", "failed", "cancelled") and (task is None or j.get("task") == task):
            return j
        time.sleep(POLL)
    return None


def follow(cfg: ChannelConfig, jid: str, info: dict) -> None:
    """Wait for the job (and the short, if asked) and send the "clip ready" notification."""
    try:
        job = _wait(jid, "analyze")
        if job is None or job["state"] != "ready":
            err = (job or {}).get("error") or "the clip analysis didn't finish"
            notify.send(cfg, "error", title=f"{cfg.name}: clip analysis failed", text=err, job=jid, **info)
            return
        clips = (job.get("result") or {}).get("clips") or []
        best = clips[0] if clips else None
        short = None
        if cfg.auto_shorts and best:
            store.update(jid, task_args={"ids": [best["id"]], "options": {}})
            get_queue().submit(jid, "shorts")
            time.sleep(POLL)
            job = _wait(jid, "shorts") or job
            short = (job.get("cuts") or {}).get(f"s{best['id']}")
        title = (best or {}).get("title") or job.get("title") or f"{cfg.name} live clip"
        text = f"{title}"
        if best:
            text += f"  (score {best.get('score', 0):.2f}, {best.get('duration', 0):.0f}s)"
        notify.send(cfg, "clip_ready", title=f"Clip ready · {cfg.name}", text=text, job=jid,
                    clip_title=title, score=(best or {}).get("score"), clips=len(clips),
                    file=os.path.basename(info.get("file", "")),
                    short=(short or {}).get("file") if short and short.get("state") == "done" else None, **{
                        k: v for k, v in info.items() if k in ("t", "clock", "signals")})
    except Exception as e:  # noqa: BLE001 — a notification problem must not matter to the recording
        log.error("follow failed", job=jid, error=str(e)[:300])
