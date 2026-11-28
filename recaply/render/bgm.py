"""
Background music with sidechain ducking.

Put music files in `assets/bgm/` (mp3, wav, m4a, ogg, flac or aac; use tracks you may use, e.g. royalty-free).
A short can use one by name, or "auto" to pick one per clip. The track is looped under the whole short and
ffmpeg's `sidechaincompress` lowers it while somebody is talking (the voice drives the compressor), then
brings it back up in the pauses, so the voice always stays on top.

    tracks()                                   # [{"name", "size_mb"}], for the UI
    path = pick("auto", clip_id)               # a track path or None
    mix(video, dst, path, level=0.3)           # video copied untouched, audio = voice + ducked music
"""

from __future__ import annotations

import os
import subprocess

from config import BASE_DIR

EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
DEFAULT_LEVEL = 0.32          # music level before ducking (1.0 = as recorded)
DUCK = {"threshold": 0.01, "ratio": 20, "attack": 15, "release": 450}


def bgm_dir() -> str:
    return os.environ.get("RECAPLY_BGM_DIR") or os.path.join(BASE_DIR, "assets", "bgm")


def tracks() -> list[dict]:
    d = bgm_dir()
    try:
        names = sorted(n for n in os.listdir(d) if n.lower().endswith(EXTS) and os.path.isfile(os.path.join(d, n)))
    except OSError:
        return []
    return [{"name": n, "size_mb": round(os.path.getsize(os.path.join(d, n)) / 1e6, 1)} for n in names]


def pick(choice: str, clip_id: int = 0) -> str | None:
    """The track file for a choice: "" / "none" -> None, "auto" -> one per clip, else that file name (never a path)."""
    choice = (choice or "").strip()
    if not choice or choice.lower() == "none":
        return None
    names = [t["name"] for t in tracks()]
    if not names:
        return None
    if choice.lower() == "auto":
        return os.path.join(bgm_dir(), names[int(clip_id) % len(names)])
    return os.path.join(bgm_dir(), choice) if choice in names else None


def mix(video: str, dst: str, track: str, level: float = DEFAULT_LEVEL, has_audio: bool = True) -> None:
    """Write `dst`: the video's picture copied as is and its sound mixed with `track` looped and ducked under speech."""
    from recaply.ingest.audio import media_duration
    dur = media_duration(video)
    fade = max(0.1, min(1.2, dur / 4))
    lvl = max(0.02, min(1.0, float(level)))
    music = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={lvl:.3f}"
    if has_audio:
        d = DUCK
        graph = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[voice][key];{music}[m];"
                 f"[m][key]sidechaincompress=threshold={d['threshold']}:ratio={d['ratio']}:attack={d['attack']}:"
                 f"release={d['release']}:makeup=1[duck];"
                 f"[voice][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                 f"afade=t=out:st={max(0.0, dur - fade):.2f}:d={fade:.2f},alimiter=limit=0.95[a]")
    else:                                  # nothing to duck for: the music alone, a little quieter
        graph = f"{music},volume=0.8,afade=t=in:d=0.5,afade=t=out:st={max(0.0, dur - fade):.2f}:d={fade:.2f}[a]"
    tmp = dst + ".part.mp4"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", video, "-stream_loop", "-1", "-i", track,
           "-filter_complex", graph, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-t", f"{dur:.3f}", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError(f"Adding the music failed: {r.stderr.strip()[-300:]}")
    os.replace(tmp, dst)
