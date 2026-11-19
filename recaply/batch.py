"""
Batch processing: start many clip jobs from a list of links / file paths with
the same options. Each line is checked on its own, so one bad line doesn't
sink the rest; the answer says what happened to every line.
"""

from __future__ import annotations

import os

from common import allowed_url
from recaply.ingest.sources import MEDIA_EXTS, looks_like_url
from recaply.jobs import store
from recaply.jobs.queue import get_queue

MAX_BATCH = 50


def parse_inputs(text: str) -> list[str]:
    """Non-empty, de-duplicated lines (blank lines and # comments skipped), at most MAX_BATCH."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out[:MAX_BATCH]


def check(line: str, allow_local: bool) -> str | None:
    """Why a line can't be used, or None when it's fine."""
    if looks_like_url(line):
        if len(line) > 2000 or any(c in line for c in "\r\n\t\0 "):
            return "That link doesn't look right."
        return None if allowed_url(line) else "Only YouTube, Twitch and Kick links are supported."
    if not allow_local:
        return "Only links are accepted here."
    if not (os.path.isfile(line) and line.lower().endswith(MEDIA_EXTS)):
        return "File not found, or not a video."
    return None


def start_batch(lines: list[str], options: dict, owner=None, priority: int = 0, allow_local: bool = False) -> list[dict]:
    """Create and queue a job per good line. Returns [{input, job} or {input, error}] in order."""
    results = []
    for line in lines[:MAX_BATCH]:
        err = check(line, allow_local)
        if err:
            results.append({"input": line, "error": err})
            continue
        title = "" if looks_like_url(line) else os.path.basename(line)
        jid = store.create(line, dict(options), owner=owner, priority=priority, title=title)
        get_queue().submit(jid, "analyze", priority=priority)
        results.append({"input": line, "job": jid})
    return results


def retry_failed(owner=None) -> list[str]:
    """Re-queue every failed analysis of this owner (cached stages are reused). Returns the job ids."""
    ids = []
    for j in store.list_jobs(owner=owner, limit=500):
        if j["state"] == "failed":
            store.update(j["id"], state="queued", error=None, stage="Waiting", progress=0.0)
            get_queue().submit(j["id"], "analyze")
            ids.append(j["id"])
    return ids


def cancel_queued(job_id: str) -> bool:
    """Cancel a job that hasn't started. A running one can't be interrupted."""
    j = store.get(job_id)
    if j and j["state"] == "queued":
        store.update(job_id, state="cancelled", stage="Cancelled")
        return True
    return False
