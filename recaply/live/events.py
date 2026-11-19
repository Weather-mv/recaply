"""
In-app live events (spike seen, clip captured, clip ready, stream ended...),
kept in the job database so the page, the CLI and other processes all read
the same list.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from recaply.jobs import store

_lock = threading.Lock()
_ready = set()
SCHEMA = """
create table if not exists live_events (
    id integer primary key autoincrement,
    channel text not null,
    kind text not null,
    ts real not null,
    data text not null default '{}'
);
create index if not exists live_events_channel on live_events(channel, id);
"""


def _con() -> sqlite3.Connection:
    from recaply.settings import settings
    con = store._db()
    if settings.jobs_db not in _ready:
        store.init()
        con.executescript(SCHEMA)
        _ready.add(settings.jobs_db)
    return con


def add(channel: str, kind: str, **data) -> int:
    with _lock, _con() as con:
        cur = con.execute("insert into live_events (channel, kind, ts, data) values (?,?,?,?)",
                          (channel, kind, time.time(), json.dumps(data, default=str)))
        con.execute("delete from live_events where id < (select max(id) from live_events) - 2000")
        return int(cur.lastrowid)


def list_events(since_id: int = 0, channel: str | None = None, limit: int = 100) -> list[dict]:
    q, args = "select * from live_events where id > ?", [int(since_id)]
    if channel:
        q += " and channel=?"
        args.append(channel)
    q += " order by id desc limit ?"
    args.append(int(limit))
    with _lock, _con() as con:
        rows = con.execute(q, args).fetchall()
    out = []
    for r in reversed(rows):
        try:
            d = json.loads(r["data"])
        except ValueError:
            d = {}
        out.append({"id": r["id"], "channel": r["channel"], "kind": r["kind"], "ts": r["ts"], **d})
    return out
