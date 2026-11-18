"""
slots.py — a limited number of work slots handed out by priority.

Pro jobs (priority 2) are served before Starter (1) and Free (0); jobs with
the same priority are served in the order they asked.
"""

import heapq
import itertools
import threading
from contextlib import contextmanager


class PrioritySlots:
    def __init__(self, n):
        self.free = max(1, int(n))
        self._cv = threading.Condition()
        self._queue = []                      # heap of (-priority, ticket number)
        self._seq = itertools.count()

    @contextmanager
    def take(self, priority=0):
        ticket = (-int(priority or 0), next(self._seq))
        with self._cv:
            heapq.heappush(self._queue, ticket)
            while not (self.free > 0 and self._queue[0] == ticket):
                self._cv.wait()
            heapq.heappop(self._queue)
            self.free -= 1
            self._cv.notify_all()             # the next in line may also fit
        try:
            yield
        finally:
            with self._cv:
                self.free += 1
                self._cv.notify_all()

    def waiting(self):
        with self._cv:
            return len(self._queue)
