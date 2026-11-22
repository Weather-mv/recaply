"""
The rolling recording buffer: a folder of `seg_NNNNNN.ts` segments written
by the recorder, addressed by *stream time* (seconds of recording so far).

MPEG-TS can be read while it's still being written, so the newest segment
is usable straight away: the detector decodes the last few seconds from it
and a clip can be cut right up to "now". Segments are cut on keyframes, so
their lengths vary; each finished one is measured once with ffprobe.
"""

from __future__ import annotations

import glob
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import numpy as np

from recaply.ingest.audio import decode_pcm, media_duration
from recaply.log import get_logger

log = get_logger("live.buffer")
RATE = 16000


@dataclass
class Seg:
    path: str
    start: float
    dur: float
    done: bool

    @property
    def end(self) -> float:
        return self.start + self.dur


class Buffer:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.lock = threading.RLock()
        self._segs: list[Seg] = []
        self._known: dict[str, float] = {}      # finished segment -> duration
        self._origin = 0.0                      # stream time of the first segment still tracked
        self.finished = False                   # the recorder has stopped: every segment is final

    # ---------- bookkeeping ----------

    def refresh(self) -> list[Seg]:
        """Re-read the folder; measure new finished segments and the growing one."""
        with self.lock:
            files = sorted(glob.glob(os.path.join(self.folder, "seg_*.ts")))
            tracked = {s.path for s in self._segs}
            paths = [s.path for s in self._segs] + [p for p in files if p not in tracked]
            paths = [p for p in dict.fromkeys(paths) if os.path.exists(p) or p in self._known]
            segs, t = [], self._origin
            for i, p in enumerate(paths):
                last = i == len(paths) - 1
                done = self.finished or not last
                if done and p in self._known:
                    d = self._known[p]
                else:
                    try:
                        d = media_duration(p)
                    except Exception:  # noqa: BLE001 — a segment that's just been created has no header yet
                        d = 0.0
                    if done and d > 0:
                        self._known[p] = d
                segs.append(Seg(p, t, d, done))
                t += d
            self._segs = segs
            return list(segs)

    @property
    def length(self) -> float:
        """Seconds of stream recorded so far."""
        with self.lock:
            return self._segs[-1].end if self._segs else self._origin

    @property
    def oldest(self) -> float:
        with self.lock:
            return self._segs[0].start if self._segs else self._origin

    def prune(self, keep_from: float) -> int:
        """Delete segments that end before stream time `keep_from`. Returns how many."""
        n = 0
        with self.lock:
            while len(self._segs) > 1 and self._segs[0].done and self._segs[0].end <= keep_from:
                s = self._segs.pop(0)
                self._origin = s.end
                self._known.pop(s.path, None)
                try:
                    os.remove(s.path)
                    n += 1
                except OSError:
                    pass
        return n

    def size_bytes(self) -> int:
        """Disk used by the segments still tracked."""
        with self.lock:
            paths = [s.path for s in self._segs]
        total = 0
        for p in paths:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def prune_to_size(self, limit_bytes: int, protect_from: float = float("inf")) -> int:
        """Delete the oldest finished segments until the buffer fits `limit_bytes`, but never one that ends after
        stream time `protect_from` (a moment still waiting to be cut) and never the newest. Returns how many."""
        n = 0
        while self.size_bytes() > limit_bytes:
            with self.lock:
                if len(self._segs) <= 1 or not self._segs[0].done or self._segs[0].end > protect_from:
                    break
                s = self._segs.pop(0)
                self._origin = s.end
                self._known.pop(s.path, None)
            try:
                os.remove(s.path)
                n += 1
            except OSError:
                pass
        return n

    def next_index(self) -> int:
        """Number for the next segment file (so a restarted recorder never overwrites)."""
        nums = []
        for p in glob.glob(os.path.join(self.folder, "seg_*.ts")):
            try:
                nums.append(int(os.path.basename(p)[4:-3]))
            except ValueError:
                pass
        return max(nums) + 1 if nums else 0

    # ---------- reading ----------

    def pcm(self, start: float, dur: float) -> np.ndarray:
        """Mono 16 kHz samples of stream time [start, start+dur), silence where nothing was recorded."""
        want = int(round(dur * RATE))
        out = np.zeros(want, np.float32)
        with self.lock:
            segs = list(self._segs)
        for s in segs:
            a, b = max(start, s.start), min(start + dur, s.end if s.dur > 0 else start + dur)
            if b <= a or s.dur <= 0 and s is not segs[-1]:
                continue
            x = decode_pcm(s.path, a - s.start, b - a + 0.05, RATE)
            i = int(round((a - start) * RATE))
            x = x[: max(0, min(len(x), want - i))]
            out[i:i + len(x)] = x
        return out

    def cut(self, t0: float, t1: float, out_path: str) -> str:
        """Write stream time [t0, t1) as an MP4 (re-encoded, so the cut is exact)."""
        with self.lock:
            segs = [s for s in self._segs if s.end > t0 and s.start < t1 and (s.dur > 0 or s is self._segs[-1])]
        if not segs:
            raise RuntimeError("that part of the stream is no longer in the buffer")
        t0 = max(t0, segs[0].start)
        fd, lst = tempfile.mkstemp(suffix=".txt", dir=self.folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for s in segs:
                f.write("file '" + s.path.replace("'", "'\\''") + "'\n")
        try:
            tmp = out_path + ".part.mp4"
            cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                   "-ss", f"{t0 - segs[0].start:.3f}", "-t", f"{max(1.0, t1 - t0):.3f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0 or not os.path.exists(tmp):
                raise RuntimeError("cutting the clip failed: " + (r.stderr or "")[-300:])
            os.replace(tmp, out_path)
        finally:
            try:
                os.remove(lst)
            except OSError:
                pass
        return out_path
