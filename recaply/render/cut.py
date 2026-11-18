"""
Cut a clip, frame-accurately, straight from the source: a local file, or the
platform's stream URLs for just that time range (the rest of the video is
never downloaded). ffmpeg seeks each input to the start and re-encodes only
the clip, on the GPU when there is one (NVENC / Quick Sync), falling back to
libx264 on the CPU.

Phase 2's renderer (reframe, captions, brand kit) takes these clips as input.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from recaply.ingest.sources import Source, processed_info

QUALITY_FORMATS = {
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "best": "bv*+ba/b",
}
FORMAT_SORT = ["res", "fps", "vcodec:h264", "acodec:aac"]   # H.264/AAC decode fastest


def clip_inputs(src: Source, quality: str = "1080") -> list[tuple[str, dict]]:
    """[(location, headers)] to read clips from: the file, or the chosen stream URL(s)."""
    if src.kind == "file":
        return [(src.location, {})]
    import yt_dlp
    from pipeline import base_opts
    import copy
    opts = base_opts(skip_download=True, format=QUALITY_FORMATS.get(quality, QUALITY_FORMATS["1080"]),
                     format_sort=FORMAT_SORT)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.process_ie_result(copy.deepcopy(src.raw), download=False)
    fmts = info.get("requested_formats") or [info]
    return [(f["url"], f.get("http_headers") or info.get("http_headers") or {}) for f in fmts]


def cut(inputs: list[tuple[str, dict]], start: float, end: float, out_path: str,
        has_video: bool = True, progress: Callable[[float], None] | None = None) -> str:
    """Write [start, end] of the inputs to `out_path` (H.264 + AAC MP4). Returns the encoder used."""
    from pipeline import ENCODER_ARGS, h264_encoder
    dur = max(0.5, end - start)
    encoders = [h264_encoder()] if has_video else ["none"]
    if encoders[0] not in ("libx264", "none"):
        encoders.append("libx264")
    tmp = out_path + ".part.mp4"
    err = ""
    for enc in encoders:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
        for loc, headers in inputs:
            if loc.startswith("http") and headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            if loc.startswith("http"):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            cmd += ["-ss", f"{start:.3f}", "-i", loc]
        cmd += ["-t", f"{dur:.3f}"]
        last = len(inputs) - 1
        if has_video:
            cmd += ["-map", "0:v:0", "-map", f"{last}:a:0?", "-c:v", enc, *ENCODER_ARGS[enc]]
        else:
            cmd += ["-map", "0:a:0", "-vn"]
        cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", tmp]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None and proc.stderr is not None
        for line in proc.stdout:
            if progress and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / dur))
                except ValueError:
                    pass
        err = proc.stderr.read()
        if proc.wait() == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, out_path)
            return enc
        if os.path.exists(tmp):
            os.remove(tmp)
    raise RuntimeError(f"Cutting the clip failed: {err.strip()[-300:]}")


__all__ = ["clip_inputs", "cut", "processed_info"]
