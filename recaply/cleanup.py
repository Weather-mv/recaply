"""
Disk clean-up: delete finished projects after a number of days, and analysis caches nothing uses.

What is deleted:
  - finished projects (ready, failed or cancelled) older than the chosen number of days: their row in
    the job database and their folder in `<data dir>/_clipjobs/<id>` (cuts, shorts, uploads made there);
  - content caches (`<data dir>/_cache/<key>`: audio, loudness, transcripts) that no remaining project
    points at and that haven't been touched for the same number of days.
Never deleted: running or queued projects, Live recordings, and files outside the data folder (a video
opened from the PC in place stays where it is).

The number of days is kept in `<data dir>/_cleanup.json` (0 = never). With a number set, the app runs
the clean-up at start-up and every few hours.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time

from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.log import get_logger
from recaply.settings import settings

log = get_logger("cleanup")
CHOICES = (0, 3, 7, 14, 30, 60, 90)
EVERY_SEC = 6 * 3600
FINISHED = ("ready", "failed", "cancelled")
_lock = threading.Lock()
_timer: threading.Timer | None = None


def _path() -> str:
    return os.path.join(settings.data_dir, "_cleanup.json")


def get_days() -> int:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = int(json.load(f).get("days") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0
    return d if d in CHOICES else 0


def set_days(days: int) -> int:
    days = int(days)
    if days not in CHOICES:
        raise ValueError(f"Pick one of: {', '.join(map(str, CHOICES))} days (0 = never).")
    os.makedirs(settings.data_dir, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"days": days}, f)
    os.replace(tmp, _path())
    return days


def _size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def _cache_root() -> str:
    return settings.cache_dir


def _old_jobs(owner, days: int, everyone: bool) -> list[dict]:
    cutoff = time.time() - days * 86400
    rows = store.list_light(owner=owner, limit=100000, everyone=everyone)
    return [r for r in rows if r["state"] in FINISHED and float(r.get("updated") or r["created"]) < cutoff]


def _unused_caches(days: int) -> list[str]:
    root = _cache_root()
    if not os.path.isdir(root):
        return []
    keep = set()
    for r in store.list_light(owner=None, limit=100000, everyone=True):
        key = ((r.get("result") or {}).get("source") or {}).get("key")
        if key:
            keep.add(str(key))
    cutoff = time.time() - days * 86400
    out = []
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if n in keep or not os.path.isdir(p):
            continue
        try:
            newest = max([os.path.getmtime(p)] + [os.path.getmtime(os.path.join(p, f)) for f in os.listdir(p)])
        except OSError:
            continue
        if newest < cutoff:
            out.append(p)
    return out


def preview(owner, days: int, everyone: bool = False) -> dict:
    """What a clean-up with `days` would delete: {"projects", "project_bytes", "caches", "cache_bytes"}."""
    jobs = _old_jobs(owner, days, everyone)
    caches = _unused_caches(days) if everyone else []
    return {"projects": len(jobs), "project_bytes": sum(_size(job_dir(j["id"])) for j in jobs),
            "caches": len(caches), "cache_bytes": sum(_size(p) for p in caches)}


def clean(owner, days: int, everyone: bool = False) -> dict:
    """Delete finished projects older than `days` (this owner's, or everyone's) and, for everyone, the
    caches nothing uses any more. Returns what was freed (same keys as preview)."""
    with _lock:
        freed = {"projects": 0, "project_bytes": 0, "caches": 0, "cache_bytes": 0}
        for j in _old_jobs(owner, days, everyone):
            folder = job_dir(j["id"])
            freed["project_bytes"] += _size(folder)
            store.delete(j["id"])
            shutil.rmtree(folder, ignore_errors=True)
            freed["projects"] += 1
        if everyone:
            for p in _unused_caches(days):
                freed["cache_bytes"] += _size(p)
                shutil.rmtree(p, ignore_errors=True)
                freed["caches"] += 1
    if freed["projects"] or freed["caches"]:
        log.info("cleaned up", days=days, **freed)
    return freed


_LEGACY_ID = re.compile(r"^[0-9a-f]{10}$")
_LEGACY_DIRS = ("_editor", "_zips")


def legacy_folders() -> list[str]:
    """Files from the previous version that nothing in the app opens any more: Recaply 2 recaps
    (`<data dir>/<10 hex>/`, clips, frames and zips) and the classic editor's `_editor` / `_zips`."""
    root = settings.data_dir
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, n) for n in sorted(os.listdir(root))
            if (_LEGACY_ID.match(n) or n in _LEGACY_DIRS) and os.path.isdir(os.path.join(root, n))]


def clean_legacy() -> dict:
    """Delete the previous version's files. Returns {"folders", "bytes"}."""
    with _lock:
        freed = {"folders": 0, "bytes": 0}
        for p in legacy_folders():
            freed["bytes"] += _size(p)
            shutil.rmtree(p, ignore_errors=True)
            freed["folders"] += 1
    log.info("old-version files deleted", **freed)
    return freed


def usage() -> dict:
    """Bytes used in the data folder: projects, caches, Live buffers, the old version's files, the rest."""
    root = settings.data_dir
    parts = {"projects": os.path.join(root, "_clipjobs"), "cache": _cache_root(), "live": os.path.join(root, "_live")}
    out = {k: _size(p) if os.path.isdir(p) else 0 for k, p in parts.items()}
    legacy = legacy_folders()
    out["legacy"] = sum(_size(p) for p in legacy)
    out["legacy_folders"] = len(legacy)
    out["total"] = _size(root) if os.path.isdir(root) else 0
    out["other"] = max(0, out["total"] - sum(out[k] for k in parts) - out["legacy"])
    return out


def run_scheduled() -> None:
    """Clean up with the saved number of days (when set), then again in EVERY_SEC. Called once at start-up."""
    global _timer
    days = get_days()
    if days:
        try:
            clean(None, days, everyone=True)
        except Exception as e:  # noqa: BLE001 — never let a clean-up stop the app
            log.warning("clean-up failed", error=str(e)[:200])
    _timer = threading.Timer(EVERY_SEC, run_scheduled)
    _timer.daemon = True
    _timer.start()
