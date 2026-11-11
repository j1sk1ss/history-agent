"""Timing persists across attempts and old checkpoint schemas."""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from history_agent.review.journal import Journal
from history_agent.review.storage.models import ReviewAttempt
from history_agent.review.timing import ReviewTimer, timing_summary


class TimingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_separates_user_wait_and_accumulates_after_resume(self):
        job = Journal(self.root)
        self.addCleanup(job.close)
        with patch("history_agent.review.timing.time.monotonic") as clock:
            clock.return_value = 10
            with ReviewTimer(job, "warning", lambda *a, **k: None):
                clock.return_value = 12
                with ReviewTimer.wait_for_user():
                    clock.return_value = 17
                clock.return_value = 20
        job.close()
        resumed = Journal(self.root, job.id)
        self.addCleanup(resumed.close)
        with patch("history_agent.review.timing.time.monotonic") as clock:
            clock.return_value = 30
            with ReviewTimer(resumed, "warning", lambda *a, **k: None):
                clock.return_value = 33
        self.assertEqual(
            timing_summary(resumed, "warning"),
            {
                "attempts": 2,
                "wall_seconds": 13,
                "waiting_seconds": 5,
                "agent_seconds": 8,
            },
        )
        self.assertEqual(resumed.summary()["timing"]["attempts"], 2)

    def test_interrupted_attempt_is_retained(self):
        job = Journal(self.root)
        self.addCleanup(job.close)
        with self.assertRaises(asyncio.CancelledError):
            with ReviewTimer(job, "warning", lambda *a, **k: None):
                raise asyncio.CancelledError()
        with job.sessions() as session:
            attempt = session.scalars(select(ReviewAttempt)).one()
            self.assertEqual(attempt.status, "interrupted")
            self.assertIsNotNone(attempt.finished_at)
            self.assertGreaterEqual(attempt.agent_seconds, 0)

    def test_legacy_schema_gains_timing_without_altering_items(self):
        path = self.root / ("a" * 32 + ".sqlite3")
        with sqlite3.connect(path) as connection:
            connection.executescript(
                Path("tests/fixtures/checkpoint_v1.sql").read_text()
            )
        job = Journal(self.root, path.stem)
        self.addCleanup(job.close)
        self.assertEqual(job.items()[0].warning_id, "legacy-warning")
        self.assertEqual(timing_summary(job)["attempts"], 0)
