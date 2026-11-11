"""Durable attempt timing, including interruptions and user wait time."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import func, select

from history_agent.review.storage.models import ReviewAttempt

CURRENT_TIMER: ContextVar[ReviewTimer | None] = ContextVar(
    "review_timer", default=None
)


class ReviewTimer:
    def __init__(self, job, warning_id: str, emit):
        self.job, self.emit = job, emit
        self.started = time.monotonic()
        self.waiting = 0.0
        self.attempt = ReviewAttempt(
            id=uuid.uuid4().hex,
            warning_id=warning_id,
            started_at=self.now(),
            status="running",
            route="agent",
            wall_seconds=0,
            waiting_seconds=0,
            agent_seconds=0,
        )

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def __enter__(self):
        self.token = CURRENT_TIMER.set(self)
        self.persist()
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.attempt.status = (
                "completed" if kind is None else "interrupted"
            )
            self.attempt.finished_at = self.now()
            self.persist()
            self.emit(
                "review_timing",
                warning_id=self.attempt.warning_id,
                status=self.attempt.status,
                route=self.attempt.route,
                wall_seconds=self.attempt.wall_seconds,
                waiting_seconds=self.attempt.waiting_seconds,
                agent_seconds=self.attempt.agent_seconds,
            )
        finally:
            CURRENT_TIMER.reset(self.token)

    def persist(self):
        elapsed = max(0.0, time.monotonic() - self.started)
        self.attempt.wall_seconds = elapsed
        self.attempt.waiting_seconds = self.waiting
        self.attempt.agent_seconds = max(0.0, elapsed - self.waiting)
        with self.job.sessions.begin() as session:
            session.merge(self.attempt)

    @staticmethod
    @contextmanager
    def wait_for_user():
        timer = CURRENT_TIMER.get()
        started = time.monotonic()
        try:
            yield
        finally:
            if timer:
                timer.waiting += max(0.0, time.monotonic() - started)
                timer.persist()

    @staticmethod
    def route(value: str):
        timer = CURRENT_TIMER.get()
        if timer:
            timer.attempt.route = value


def timing_summary(job, warning_id: str | None = None):
    fields = ("wall_seconds", "waiting_seconds", "agent_seconds")
    query = select(
        func.count(),
        *(
            func.coalesce(func.sum(getattr(ReviewAttempt, name)), 0)
            for name in fields
        ),
    )
    if warning_id:
        query = query.where(ReviewAttempt.warning_id == warning_id)
    with job.sessions() as session:
        row = session.execute(query).one()
        return {
            "attempts": row[0],
            **{name: round(value, 6) for name, value in zip(fields, row[1:])},
        }
