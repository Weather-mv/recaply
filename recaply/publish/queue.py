"""
The publishing schedule: a SQLite table (in the job database) and a
background scheduler.

An item says "upload this finished file of this job to this platform at this
time". The scheduler checks every few seconds, uploads what is due one item
at a time, and retries network / server errors with a growing wait
(1 min, 5 min, 30 min; three tries in all). Anything else is marked failed
with the platform's own message, and can be retried by hand.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from recaply import publish
from recaply.jobs import store
from recaply.log import get_logger
from recaply.publish.base import Post, PublishError

log = get_logger("publish.queue")
STATUSES = ("queued", "uploading", "done", "failed", "cancelled")
BACKOFF = (60, 300, 1800)
MAX_TRIES = len(BACKOFF)
_lock = threading.Lock()
_ready: set = set()
SCHEMA = """
create table if not exists publish_items (
    id integer primary key autoincrement,
    job_id text not null,
    file text not null,
    platform text not null,
    title text not null,
    description text not null default '',
    tags text not null default '[]',
    privacy text not null default 'public',
    run_at real not null,
    status text not null default 'queued',
    attempts integer not null default 0,
    result text,
    error text,
    created real not null,
    updated real not null
);
create index if not exists publish_items_due on publish_items(status, run_at);
"""


def _con() -> sqlite3.Connection:
    from recaply.settings import settings
    con = store._db()
    if settings.jobs_db not in _ready:
        store.init()
        con.executescript(SCHEMA)
        _ready.add(settings.jobs_db)
    return con


def _row(r) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d


def resolve_file(job_id: str, name: str) -> str:
    """Absolute path of a job's output file; refuses anything outside the job's folder."""
    from recaply.jobs.runner import job_dir
    base = os.path.realpath(job_dir(job_id))
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, path]) != base or not name.lower().endswith(".mp4"):
        raise PublishError("That file isn't part of the job.")
    return path


def enqueue(job_id: str, file: str, platform: str, title: str, description: str = "", tags: list[str] | None = None,
            run_at: float | None = None, privacy: str = "public") -> int:
    if publish.get(platform) is None:
        raise PublishError(f"Unknown platform '{platform}'.")
    if not os.path.isfile(resolve_file(job_id, file)):
        raise PublishError("That file doesn't exist yet: make the short first.")
    now = time.time()
    with _lock, _con() as con:
        cur = con.execute("insert into publish_items (job_id, file, platform, title, description, tags, privacy, run_at,"
                          " created, updated) values (?,?,?,?,?,?,?,?,?,?)",
                          (job_id, file, platform, title.strip()[:200] or "Clip", description[:5000],
                           json.dumps([str(t)[:60] for t in (tags or [])][:20]), privacy,
                           float(run_at if run_at else now), now, now))
        return int(cur.lastrowid)


def list_items(status: str | None = None, limit: int = 200) -> list[dict]:
    q, args = "select * from publish_items", []
    if status:
        q += " where status=?"
        args.append(status)
    q += " order by run_at desc, id desc limit ?"
    args.append(int(limit))
    with _lock, _con() as con:
        return [_row(r) for r in con.execute(q, args).fetchall()]


def get(item_id: int) -> dict | None:
    with _lock, _con() as con:
        r = con.execute("select * from publish_items where id=?", (int(item_id),)).fetchone()
    return _row(r) if r else None


def _set(item_id: int, **f) -> None:
    f["updated"] = time.time()
    if "result" in f and f["result"] is not None:
        f["result"] = json.dumps(f["result"])
    with _lock, _con() as con:
        con.execute(f"update publish_items set {', '.join(k + '=?' for k in f)} where id=?", [*f.values(), int(item_id)])


def cancel(item_id: int) -> bool:
    with _lock, _con() as con:
        return con.execute("update publish_items set status='cancelled', updated=? where id=? and status in ('queued','failed')",
                           (time.time(), int(item_id))).rowcount > 0


def retry(item_id: int, run_at: float | None = None) -> bool:
    with _lock, _con() as con:
        return con.execute("update publish_items set status='queued', attempts=0, error=null, run_at=?, updated=? "
                           "where id=? and status in ('failed','cancelled')",
                           (float(run_at or time.time()), time.time(), int(item_id))).rowcount > 0


def reschedule(item_id: int, run_at: float) -> bool:
    with _lock, _con() as con:
        return con.execute("update publish_items set run_at=?, updated=? where id=? and status='queued'",
                           (float(run_at), time.time(), int(item_id))).rowcount > 0


def reset_stuck() -> int:
    """Items left 'uploading' by a crash go back to the queue (the upload starts over)."""
    with _lock, _con() as con:
        return con.execute("update publish_items set status='queued', updated=? where status='uploading'", (time.time(),)).rowcount


def _claim(now: float) -> dict | None:
    with _lock, _con() as con:
        r = con.execute("select * from publish_items where status='queued' and run_at<=? order by run_at, id limit 1", (now,)).fetchone()
        if not r:
            return None
        if con.execute("update publish_items set status='uploading', updated=? where id=? and status='queued'",
                       (now, r["id"])).rowcount == 0:
            return None
    return _row(r)


def process(item: dict) -> None:
    """Upload one claimed item and record the outcome."""
    pub = publish.get(item["platform"])
    try:
        if pub is None:
            raise PublishError(f"Unknown platform '{item['platform']}'.")
        path = resolve_file(item["job_id"], item["file"])
        res = pub.publish(Post(path, item["title"], item["description"], item["tags"], item["privacy"]))
        _set(item["id"], status="done", result={"id": res.id, "url": res.url}, error=None, attempts=item["attempts"] + 1)
        log.info("published", item=item["id"], platform=item["platform"], url=res.url)
    except PublishError as e:
        tries = item["attempts"] + 1
        if e.retryable and tries < MAX_TRIES:
            _set(item["id"], status="queued", attempts=tries, error=str(e)[:400], run_at=time.time() + BACKOFF[tries - 1])
            log.warning("publish will retry", item=item["id"], in_sec=BACKOFF[tries - 1], error=str(e)[:200])
        else:
            _set(item["id"], status="failed", attempts=tries, error=str(e)[:400])
            log.error("publish failed", item=item["id"], error=str(e)[:200])
    except Exception as e:  # noqa: BLE001 — one bad item must not stop the scheduler
        _set(item["id"], status="failed", attempts=item["attempts"] + 1, error=f"unexpected: {str(e)[:300]}")
        log.error("publish crashed", item=item["id"], error=str(e)[:300])


def run_due(now: float | None = None) -> int:
    """Upload everything due now (one at a time). Returns how many were attempted."""
    n = 0
    while True:
        item = _claim(now if now is not None else time.time())
        if item is None:
            return n
        process(item)
        n += 1


class Scheduler(threading.Thread):
    def __init__(self, every: float = 15.0):
        super().__init__(daemon=True, name="publish-scheduler")
        self.every = every
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        try:
            reset_stuck()
        except Exception:  # noqa: BLE001
            pass
        while not self._stop_evt.wait(self.every):
            try:
                run_due()
            except Exception as e:  # noqa: BLE001
                log.error("scheduler tick failed", error=str(e)[:200])


_sched: Scheduler | None = None


def start_scheduler() -> Scheduler:
    global _sched
    if _sched is None or not _sched.is_alive():
        _sched = Scheduler()
        _sched.start()
    return _sched
