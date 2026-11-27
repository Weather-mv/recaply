"""The job store's fast paths: reads that don't wait for writers, and list views that skip the big result."""
import threading
import time

from recaply.jobs import store


def _result(n_clips=3, words=2000):
    return {"source": {"kind": "file", "key": "k1", "duration": 600.0, "video_id": None, "extractor": None, "title": "T"},
            "source_path": "/x/video.mp4", "profile": {"key": "talk", "label": "Talk", "reason": "r", "clip_min": 1},
            "scorer": "rules", "transcript": "word " * words,
            "clips": [{"id": i, "title": f"Clip {i}", "time": "0:00", "start": i * 10.0, "duration": 30.0, "score": 50 + i,
                       "beats": [{"type": "hook"}] * i, "transcript": "word " * words} for i in range(1, n_clips + 1)]}


def test_the_brief_follows_the_result_and_list_views_read_only_it():
    store.init()
    jid = store.create("/x/video.mp4", {"profile": "talk"}, owner=77, title="Light")
    store.update(jid, state="ready", result=_result())
    (row,) = store.list_light(owner=77, ids=[jid])
    res = row["result"]
    assert res["source"]["duration"] == 600.0 and res["source_path"] == "/x/video.mp4" and res["scorer"] == "rules"
    assert [c["beats_n"] for c in res["clips"]] == [1, 2, 3] and "transcript" not in str(res)
    assert row["state"] == "ready" and row["title"] == "Light" and "log" not in row
    assert store.list_light(owner=77, ids=[]) == [] and store.list_light(owner=78, ids=[jid]) == []
    store.update(jid, result=_result(n_clips=1))                       # a new result refreshes the brief
    assert len(store.list_light(owner=77, ids=[jid])[0]["result"]["clips"]) == 1


def test_reads_do_not_wait_for_a_writer():
    store.init()
    jid = store.create("x", {}, owner=79)
    took = {}

    def read():
        t = time.perf_counter()
        store.get(jid)
        store.list_light(owner=79)
        store.version(jid)
        took["s"] = time.perf_counter() - t

    with store._lock:                                                 # a writer holds the lock...
        th = threading.Thread(target=read)
        th.start()
        th.join(5)
    assert not th.is_alive() and took["s"] < 1.0                      # ...and reads still finish


def test_old_rows_get_a_brief_on_start_up():
    store.init()
    jid = store.create("x", {}, owner=80)
    store.update(jid, result=_result())
    with store._db() as con:
        con.execute("update clip_jobs set brief=null where id=?", (jid,))
    store.init()
    assert store.list_light(owner=80, ids=[jid])[0]["result"]["clips"][0]["id"] == 1
