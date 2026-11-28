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
STATUSES = ("draft", "queued", "uploading", "done", "failed", "cancelled")
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
# Added for the Social hub (old databases get them on first use).
_ADDED = (("account_id", "integer"), ("thumbnail", "text"), ("thumb_at", "real"), ("post_group", "text"),
          ("owner", "text"), ("clip_id", "integer"))


def _con() -> sqlite3.Connection:
    from recaply.settings import settings
    con = store._db()
    if settings.jobs_db not in _ready:
        store.init()
        migrate(con)
        _ready.add(settings.jobs_db)
    return con


def migrate(con: sqlite3.Connection) -> None:
    """Create the table, and add the columns newer versions use to a table made by an older one."""
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("pragma table_info(publish_items)").fetchall()}
    for col, typ in _ADDED:
        if col not in have:
            con.execute(f"alter table publish_items add column {col} {typ}")
    con.execute("create index if not exists publish_items_group on publish_items(post_group)")
    con.commit()


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


def resolve_image(job_id: str, name: str) -> str:
    """Absolute path of a thumbnail inside the job's folder (jpg / png only)."""
    from recaply.jobs.runner import job_dir
    base = os.path.realpath(job_dir(job_id))
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, path]) != base or not name.lower().endswith((".jpg", ".jpeg", ".png")):
        raise PublishError("That thumbnail isn't part of the job.")
    return path


def enqueue(job_id: str, file: str, platform: str, title: str, description: str = "", tags: list[str] | None = None,
            run_at: float | None = None, privacy: str = "public", account_id: int | None = None,
            thumbnail: str | None = None, thumb_at: float | None = None, post_group: str | None = None,
            owner=None, status: str = "queued", clip_id: int | None = None) -> int:
    if publish.get(platform) is None:
        raise PublishError(f"Unknown platform '{platform}'.")
    if status not in ("queued", "draft"):
        raise PublishError("A new item is either queued or a draft.")
    if not os.path.isfile(resolve_file(job_id, file)):
        raise PublishError("That file doesn't exist yet: make the short first.")
    if thumbnail and not os.path.isfile(resolve_image(job_id, thumbnail)):
        raise PublishError("That thumbnail doesn't exist.")
    now = time.time()
    with _lock, _con() as con:
        cur = con.execute("insert into publish_items (job_id, file, platform, title, description, tags, privacy, run_at,"
                          " created, updated, status, account_id, thumbnail, thumb_at, post_group, owner, clip_id)"
                          " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (job_id, file, platform, title.strip()[:200] or "Clip", description[:5000],
                           json.dumps([str(t)[:60] for t in (tags or [])][:30]), privacy,
                           float(run_at if run_at else now), now, now, status, account_id, thumbnail or None,
                           thumb_at, post_group, owner, clip_id))
        return int(cur.lastrowid)


def update(item_id: int, **f) -> bool:
    """Edit an item that hasn't gone out yet (text, account, thumbnail, time, draft <-> queued)."""
    allowed = {"title", "description", "tags", "privacy", "run_at", "account_id", "thumbnail", "thumb_at", "status"}
    f = {k: v for k, v in f.items() if k in allowed}
    if "status" in f and f["status"] not in ("queued", "draft"):
        raise PublishError("An item can only be moved between draft and queued.")
    if "tags" in f:
        f["tags"] = json.dumps([str(t)[:60] for t in (f["tags"] or [])][:30])
    if not f:
        return False
    f["updated"] = time.time()
    with _lock, _con() as con:
        return con.execute(f"update publish_items set {', '.join(k + '=?' for k in f)} where id=? and status in "
                           "('draft','queued','failed','cancelled')", [*f.values(), int(item_id)]).rowcount > 0


def delete(item_id: int) -> bool:
    with _lock, _con() as con:
        return con.execute("delete from publish_items where id=? and status in ('draft','queued','failed','cancelled')",
                           (int(item_id),)).rowcount > 0


def in_range(start: float, end: float, limit: int = 2000) -> list[dict]:
    """Items due between two times, plus every draft (the calendar's side list)."""
    with _lock, _con() as con:
        rows = con.execute("select * from publish_items where (run_at between ? and ?) or status='draft' "
                           "order by run_at, id limit ?", (float(start), float(end), int(limit))).fetchall()
    return [_row(r) for r in rows]


def group(post_group: str) -> list[dict]:
    with _lock, _con() as con:
        return [_row(r) for r in con.execute("select * from publish_items where post_group=? order by id",
                                             (post_group,)).fetchall()]


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
        thumb = resolve_image(item["job_id"], item["thumbnail"]) if item.get("thumbnail") else None
        res = pub.publish(Post(path, item["title"], item["description"], item["tags"], item["privacy"],
                               account_id=item.get("account_id"), thumbnail=thumb, thumb_at=item.get("thumb_at")))
        _set(item["id"], status="done", result={"id": res.id, "url": res.url, "note": res.note}, error=None,
             attempts=item["attempts"] + 1)
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
