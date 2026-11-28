"""
Job queues behind one interface.

- `LocalQueue` (default; the Windows app): tasks run on threads inside the
  app, analyses wait for one of the app's analysis slots (shared with VCT
  round scans, served by plan priority), and unfinished jobs are resumed at
  start-up from the SQLite job store.
- `RQQueue` (server / Docker Compose, RECAPLY_QUEUE=rq): tasks go to Redis
  and run in `rq worker recaply` processes sharing the data volume.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from recaply.jobs import runner, store
from recaply.log import get_logger
from recaply.settings import settings

log = get_logger("queue")


class JobQueue:
    def submit(self, job_id: str, task: str = "analyze", priority: int = 0,
               refund: Callable[[list], None] | None = None) -> None:
        raise NotImplementedError

    def resume(self) -> int:
        """Re-queue jobs that were queued/running when the app stopped. Returns how many."""
        n = 0
        for job in store.unfinished():
            self.submit(job["id"], job.get("task") or "analyze", job.get("priority") or 0)
            n += 1
        return n


class LocalQueue(JobQueue):
    def submit(self, job_id, task="analyze", priority=0, refund=None):
        if refund:
            runner.REFUNDS[job_id] = refund
        store.update(job_id, state="queued", task=task, stage="Waiting for a free slot")
        threading.Thread(target=self._run, args=(job_id, task, priority), daemon=True,
                         name=f"clipjob-{job_id}").start()

    @staticmethod
    def _run(job_id, task, priority):
        try:
            if task == "analyze":
                from common import ANALYSIS_SLOTS
                with ANALYSIS_SLOTS.take(priority):
                    runner.run_task(job_id, task)
            else:
                runner.run_task(job_id, task)
        except Exception as e:  # noqa: BLE001 — never kill the app's thread silently
            log.error("task crashed", job=job_id, task=task, error=str(e)[:300])
            store.update(job_id, state="failed", error=str(e)[:500])


class RQQueue(JobQueue):
    """Redis + RQ: the web process only enqueues, `python -m recaply worker` does the work."""

    name = "recaply"

    def __init__(self, redis_url: str):
        from redis import Redis
        from rq import Queue
        self.conn = Redis.from_url(redis_url, socket_connect_timeout=3)
        self.conn.ping()                    # fail now (and fall back), not on the first upload
        self.q = Queue(self.name, connection=self.conn)

    def submit(self, job_id, task="analyze", priority=0, refund=None):
        store.update(job_id, state="queued", task=task, stage="Queued")
        # priority: paid plans jump the queue; one RQ job per (job, task) so a double click can't run it twice
        self.q.enqueue("recaply.jobs.runner.run_task", job_id, task, job_timeout=6 * 3600,
                       at_front=priority >= 2, job_id=f"{job_id}-{task}-{int(time.time())}",
                       result_ttl=300, failure_ttl=86400)

    def resume(self) -> int:
        return 0        # Redis keeps its own queue across restarts

    def info(self) -> dict:
        from rq import Worker
        return {"backend": "rq", "waiting": len(self.q), "workers": len(Worker.all(queue=self.q)),
                "ready": True}


def local_info() -> dict:
    """What `/api/clips/health` reports for the thread queue."""
    return {"backend": "local", "waiting": len(store.unfinished()), "workers": settings.job_workers, "ready": True}


_queue: JobQueue | None = None
_qlock = threading.Lock()


def get_queue() -> JobQueue:
    global _queue
    with _qlock:
        if _queue is None:
            store.init()
            _queue = LocalQueue()
            if settings.queue_backend == "rq":
                try:
                    _queue = RQQueue(settings.redis_url)
                except Exception as e:  # noqa: BLE001 — no redis package / server down: keep working on threads
                    log.error("redis queue unavailable, running jobs on threads instead", error=str(e)[:200])
        return _queue


def queue_info() -> dict:
    """Backend, waiting jobs and workers, for the UI and `/api/clips/health`."""
    q = get_queue()
    return q.info() if isinstance(q, RQQueue) else local_info()


def run_worker(burst: bool = False) -> int:
    """
    A worker process for the Redis queue (`python -m recaply worker`). Uses RQ's
    SimpleWorker, which doesn't fork, so it also runs on Windows.
    """
    from redis import Redis
    from rq import Queue, SimpleWorker
    store.init()
    conn = Redis.from_url(settings.redis_url)
    conn.ping()
    log.info("worker started", queue=RQQueue.name, redis=settings.redis_url.split("@")[-1])
    SimpleWorker([Queue(RQQueue.name, connection=conn)], connection=conn).work(burst=burst, with_scheduler=False)
    return 0
