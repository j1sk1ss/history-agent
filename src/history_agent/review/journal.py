"""Durable checkpoints for resumable review jobs."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from history_agent.inference.model import ChatError, compact
from history_agent.review import patterns
from history_agent.review.jobs import JobRunningError, job_lock


class Journal:
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
        self.db: sqlite3.Connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items(
                warning_id TEXT PRIMARY KEY, checker TEXT NOT NULL,
                ordinal INTEGER NOT NULL, warning TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                verdict TEXT, baseline TEXT);
            CREATE TABLE IF NOT EXISTS checker_cache(
                checker TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        with self.db:
            patterns.initialize(self.db)

    def get(self, key: str, default: object = None) -> object:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: object) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                (key, compact(value)),
            )

    def update(
        self, warning_id: str, state: str, *,
        verdict: dict[str, object] | None = None,
        baseline: dict[str, object] | None = None,
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE items SET state=?, verdict=COALESCE(?,verdict), "
                "baseline=COALESCE(?,baseline) WHERE warning_id=?",
                (
                    state,
                    compact(verdict) if verdict is not None else None,
                    compact(baseline) if baseline is not None else None,
                    warning_id,
                ),
            )

    def summary(self) -> dict[str, object]:
        states: dict[str, int] = dict(
            self.db.execute("SELECT state,COUNT(*) FROM items GROUP BY state")
        )
        classes: Counter[str] = Counter()
        for row in self.db.execute(
            "SELECT verdict FROM items WHERE state IN ('saved','reviewed')"
        ):
            classes[json.loads(row[0])["classification"]] += 1
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
