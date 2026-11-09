"""Durable checkpoints for resumable review jobs."""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from history_agent.inference.model import ChatError
from history_agent.review.jobs import JobRunningError, job_lock
from history_agent.review.storage.checkpoint import CheckpointStore


class Journal(CheckpointStore):
    def __init__(self, root: str | Path, job_id: str | None = None) -> None:
        self.root: Path = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.id: str = job_id or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.id):
            raise ChatError("Invalid batch job ID")
        self.path: Path = self.root / (self.id + ".sqlite3")
        if job_id and not self.path.is_file():
            raise ChatError("Batch job does not exist: " + self.id)
        if not job_id:
            fd: int = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
            )
            os.close(fd)
        super().__init__(self.path)

    def summary(self) -> dict[str, object]:
        states: dict[str, int] = self.state_counts()
        classes: Counter[str] = Counter(
            verdict["classification"] for verdict in self.completed_verdicts()
        )
        return {
            "job_id": self.id,
            "scope": self.get("scope"),
            "comparison": self.get("comparison"),
            "write": self.get("write"),
            "total": sum(states.values()),
            "states": states,
            "classifications": dict(classes),
            "checkpoint": str(self.path),
        }

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            with job_lock(self.path):
                if not self.path.is_file():
                    raise ChatError("Batch job does not exist: " + self.id)
                yield
        except JobRunningError as error:
            raise ChatError(str(error)) from None
