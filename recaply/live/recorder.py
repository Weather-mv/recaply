"""
The recorder: streamlink (or yt-dlp when streamlink isn't installed) reads
the live stream, ffmpeg copies it into 5-minute MPEG-TS segments. Nothing is
re-encoded while recording, so it costs almost no CPU.

The producer's command can be replaced per channel (`source_cmd`), which is
also how the tests feed in a synthetic stream.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

from recaply.live.config import ChannelConfig
from recaply.log import get_logger

log = get_logger("live.recorder")
QUALITY = "720p60,720p,480p,best"           # the detector needs sound; the clip pipeline reads this recording


def producer_cmd(cfg: ChannelConfig) -> list[str]:
    """The command that writes the live stream to stdout."""
    if cfg.source_cmd:
        return [a.replace("{url}", cfg.url) for a in cfg.source_cmd]
    if shutil.which("streamlink"):
        return ["streamlink", "--stdout", "--loglevel", "error", "--retry-streams", "3", "--retry-max", "2",
                cfg.url, QUALITY]
    return [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-part", "-o", "-",
            "-f", "best[height<=720]/best", cfg.url]


def segmenter_cmd(folder: str, segment_sec: int, start_index: int) -> list[str]:
    return ["ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-map", "0:v?", "-map", "0:a?", "-c", "copy",
            "-f", "segment", "-segment_time", str(int(segment_sec)), "-segment_start_number", str(start_index),
            "-reset_timestamps", "1", "-segment_format", "mpegts", os.path.join(folder, "seg_%06d.ts")]


class Recorder:
    """One recording attempt: producer | ffmpeg segmenter. Start again after it exits to carry on."""

    def __init__(self, cfg: ChannelConfig, folder: str, start_index: int):
        self.cfg, self.folder, self.start_index = cfg, folder, start_index
        self.procs: list[subprocess.Popen] = []
        self.started = 0.0
        self._log = None

    def start(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        self._log = open(os.path.join(self.folder, "recorder.log"), "ab")
        kw = {"stderr": self._log}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000        # no console window
        prod = subprocess.Popen(producer_cmd(self.cfg), stdout=subprocess.PIPE, **kw)
        seg = subprocess.Popen(segmenter_cmd(self.folder, self.cfg.segment_sec, self.start_index),
                               stdin=prod.stdout, stdout=subprocess.DEVNULL, **kw)
        prod.stdout.close()              # the segmenter owns the pipe now: it sees EOF when the producer ends
        self.procs = [prod, seg]
        self.started = time.time()

    def alive(self) -> bool:
        return bool(self.procs) and self.procs[-1].poll() is None

    def ran_for(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 8
        for p in reversed(self.procs):
            try:
                p.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
        if self._log:
            try:
                self._log.close()
            except OSError:
                pass

    def tail(self, n: int = 400) -> str:
        try:
            with open(os.path.join(self.folder, "recorder.log"), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - n))
                return f.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""
