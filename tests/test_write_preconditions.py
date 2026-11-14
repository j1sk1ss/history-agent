"""A failed UD check remains resumable without implying a POST occurred."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from history_agent.history.settings import HistoryError
from history_agent.history.writes import (
    ReviewStateConflict,
    ReviewWriteRejected,
)
from history_agent.review.models import ReviewVerdict
from history_agent.review.processor import ItemProcessor

SCOPE = {"project_id": 0, "stream_id": "main", "snapshot_id": "snapshot"}


class WritePreconditionTest(unittest.IsolatedAsyncioTestCase):
    async def test_prewrite_failures_and_unknown_posts_have_distinct_states(
        self,
    ):
        for error, state, rejected in (
            (ReviewWriteRejected("Cannot confirm UD"), "proposed", True),
            (ReviewStateConflict("Human reviewed"), "conflict", False),
            (HistoryError("POST outcome unknown"), "uncertain", True),
        ):
            with self.subTest(state=state):
                history = Mock(execute=Mock(side_effect=error))
                processor = ItemProcessor(
                    history,
                    None,
                    lambda *a, **k: None,
                    reuse_patterns=False,
                )
                baseline = {"classification_id": 0, "comment": ""}
                processor.current = AsyncMock(return_value=baseline)
                job = Mock(get=Mock(return_value=SCOPE))
                row = SimpleNamespace(warning_id="warning")
                verdict = ReviewVerdict(
                    classification="UC",
                    comment="Bismark said: Unclear",
                    evidence=[],
                )
                if rejected:
                    with self.assertRaises(type(error)):
                        await processor.save(
                            job, row, verdict, baseline, {"Undecided": 0}
                        )
                else:
                    self.assertFalse(
                        await processor.save(
                            job,
                            row,
                            verdict,
                            baseline,
                            {"Undecided": 0},
                        )
                    )
                self.assertEqual(job.update.call_args.args, ("warning", state))
