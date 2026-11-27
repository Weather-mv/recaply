"""Disk clean-up: old finished projects go, running ones and caches still in use stay."""
import os
import time

from recaply import cleanup
from recaply.jobs import store
from recaply.jobs.runner import job_dir
from recaply.settings import settings


def _job(owner, state, age_days, key=None):
    jid = store.create("x", {}, owner=owner)
    store.update(jid, state=state, result={"source": {"kind": "url", "key": key}, "clips": []})
    with store._open() as con:
        con.execute("update clip_jobs set created=?, updated=? where id=?",
                    (time.time() - age_days * 86400,) * 2 + (jid,))
    os.makedirs(job_dir(jid), exist_ok=True)
    with open(os.path.join(job_dir(jid), "clip.mp4"), "wb") as f:
        f.write(b"\0" * 5000)
    return jid


def _cache(key, age_days):
    d = os.path.join(settings.cache_dir, key)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "audio.m4a")
    with open(p, "wb") as f:
        f.write(b"\0" * 3000)
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    os.utime(d, (t, t))
    return d


def test_old_finished_projects_and_unused_caches_are_deleted():
    store.init()
    old = _job(501, "ready", 40, key="kept-key")
    failed = _job(501, "failed", 40)
    fresh = _job(501, "ready", 2)
    running = _job(501, "running", 40)
    used, unused, recent = _cache("used-key", 40), _cache("old-unused", 40), _cache("new-unused", 1)
    _job(501, "ready", 1, key="used-key")                            # a recent project still needs this cache

    p = cleanup.preview(None, 30, everyone=True)
    assert p["projects"] >= 2 and p["project_bytes"] >= 10000 and p["caches"] >= 1
    freed = cleanup.clean(None, 30, everyone=True)
    assert freed["projects"] >= 2 and freed["project_bytes"] >= 10000
    assert store.get(old) is None and store.get(failed) is None and not os.path.isdir(job_dir(old))
    assert store.get(fresh) and store.get(running) and os.path.isdir(job_dir(running))
    assert os.path.isdir(used) and os.path.isdir(recent) and not os.path.isdir(unused)


def test_an_owner_only_clears_their_own_projects():
    store.init()
    mine, theirs = _job(601, "ready", 50), _job(602, "ready", 50)
    cleanup.clean(601, 30)
    assert store.get(mine) is None and store.get(theirs)


def test_the_keep_setting_is_validated_and_saved():
    assert cleanup.set_days(14) == 14 and cleanup.get_days() == 14
    try:
        cleanup.set_days(5)
        raise AssertionError("5 days is not a choice")
    except ValueError:
        pass
    cleanup.set_days(0)
    assert cleanup.get_days() == 0
    u = cleanup.usage()
    assert u["total"] >= u["projects"] and set(u) == {"projects", "cache", "live", "other", "total"}
