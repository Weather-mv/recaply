"""
Persistent clip-job records in SQLite (`<data dir>/_clipjobs.db`).

A job survives restarts: its input, options, result and per-clip cut state
are stored here, and stage outputs are in the content cache, so a job that
was running when the app closed is resumed from the queue and skips every
stage it already finished. Each write bumps `version`, which the progress
stream (SSE) watches.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

from recaply.settings import settings

STATES = ("queued", "running", "ready", "failed", "cancelled")
_lock = threading.Lock()
_JSON = ("options", "result", "cuts", "summary")

SCHEMA = """
create table if not exists clip_jobs (
    id text primary key,
    owner integer,
    priority integer not null default 0,
    state text not null,
    task text not null default 'analyze',
    task_args text,
    stage text not null default '',
    progress real not null default 0,
    input text not null,
    title text not null default '',
    options text,
    result text,
    cuts text,
    summary text,
    error text,
    created real not null,
    updated real not null,
    version integer not null default 0
);
create index if not exists clip_jobs_owner on clip_jobs(owner, created);
"""


def _db(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or settings.jobs_db, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init(path: str | None = None) -> None:
    os.makedirs(os.path.dirname(path or settings.jobs_db), exist_ok=True)
    with _lock, _db(path) as con:
        con.execute("pragma journal_mode=wal")
        con.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON + ("task_args",):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = None
    return d


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def create(input_text: str, options: dict, owner=None, priority: int = 0, title: str = "",
           job_id: str | None = None) -> str:
    jid = job_id or new_id()
    now = time.time()
    with _lock, _db() as con:
        con.execute("insert into clip_jobs (id, owner, priority, state, input, title, options, created, updated)"
                    " values (?,?,?,?,?,?,?,?,?)",
                    (jid, owner, int(priority or 0), "queued", input_text, title, json.dumps(options), now, now))
    return jid


def get(job_id: str) -> dict | None:
    with _lock, _db() as con:
        return _row(con.execute("select * from clip_jobs where id=?", (job_id,)).fetchone())


def update(job_id: str, **fields) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v, default=str) if k in _JSON + ("task_args",) and v is not None else v)
    cols += ["updated=?", "version=version+1"]
    vals += [time.time(), job_id]
    with _lock, _db() as con:
        con.execute(f"update clip_jobs set {', '.join(cols)} where id=?", vals)


def set_cut(job_id: str, clip_id: int, info: dict) -> None:
    """Merge one clip's cut state into the job (read-modify-write under the lock)."""
    with _lock, _db() as con:
        r = con.execute("select cuts from clip_jobs where id=?", (job_id,)).fetchone()
        cuts = json.loads(r["cuts"]) if r and r["cuts"] else {}
        cuts[str(clip_id)] = {**cuts.get(str(clip_id), {}), **info}
        con.execute("update clip_jobs set cuts=?, updated=?, version=version+1 where id=?",
                    (json.dumps(cuts), time.time(), job_id))


def version(job_id: str) -> int | None:
    with _lock, _db() as con:
        r = con.execute("select version from clip_jobs where id=?", (job_id,)).fetchone()
        return r["version"] if r else None


def list_jobs(owner=None, limit: int = 50, everyone: bool = False) -> list[dict]:
    with _lock, _db() as con:
        if everyone:
            rows = con.execute("select id, owner, state, stage, progress, title, input, created, error "
                               "from clip_jobs order by created desc limit ?", (limit,)).fetchall()
        else:
            rows = con.execute("select id, owner, state, stage, progress, title, input, created, error "
                               "from clip_jobs where owner is ? order by created desc limit ?",
                               (owner, limit)).fetchall()
    return [dict(r) for r in rows]


def usage_rows(owner=None, everyone: bool = False, since: float = 0.0, limit: int = 5000) -> list[dict]:
    """Per job: id, created, state, title, summary and the source's duration (for the usage dashboard)."""
    q = "select id, created, state, title, input, summary, result from clip_jobs where created>=?"
    args: list = [since]
    if not everyone:
        q += " and owner is ?"
        args.append(owner)
    q += " order by created desc limit ?"
    args.append(limit)
    with _lock, _db() as con:
        rows = con.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in ("id", "created", "state", "title", "input")}
        try:
            d["summary"] = json.loads(r["summary"]) if r["summary"] else {}
            res = json.loads(r["result"]) if r["result"] else {}
        except ValueError:
            d["summary"], res = {}, {}
        d["duration"] = float((res.get("source") or {}).get("duration") or 0)
        d["clips"] = len(res.get("clips") or [])
        out.append(d)
    return out


def unfinished() -> list[dict]:
    """Jobs to resume after a restart (queued or running when the app stopped)."""
    with _lock, _db() as con:
        rows = con.execute("select * from clip_jobs where state in ('queued','running') order by created").fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def delete(job_id: str) -> None:
    with _lock, _db() as con:
        con.execute("delete from clip_jobs where id=?", (job_id,))
