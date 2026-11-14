"""Renew short Redis leases while a slow history request is in flight."""

import threading
import uuid

from history_agent.history.redis_backend import RELEASE, RENEW


class CacheLease:
    def __init__(self, backend, key, *, seconds=30) -> None:
        self.backend, self.key = backend, key
        self.token = uuid.uuid4().hex
        self.seconds = seconds
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.thread = None

    def acquire(self):
        acquired = self.backend.call(
            "set",
            self.key,
            self.token,
            nx=True,
            px=max(1, int(self.seconds * 1000)),
        )
        if acquired:
            self.thread = threading.Thread(target=self.renew, daemon=True)
            self.thread.start()
        return bool(acquired)

    def renew(self):
        while not self.stopped.wait(self.seconds / 3):
            if not self.backend.call(
                "eval",
                RENEW,
                1,
                self.key,
                self.token,
                max(1, int(self.seconds * 1000)),
            ):
                self.lost.set()
                return

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=0.5)
            self.backend.call("eval", RELEASE, 1, self.key, self.token)
