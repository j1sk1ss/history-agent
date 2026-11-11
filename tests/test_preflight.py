"""Fast classification requires complete, observed source evidence."""

import json
import unittest
from unittest.mock import AsyncMock, patch

from history_agent.review.client import ItemClient
from history_agent.review.preflight import ReviewPreflight
from history_agent.review.system_one import SystemOne, SystemOneClient
from history_agent.runtime.agent import Harness
from history_agent.runtime.messages import tool_exchange
from tests.test_chat import FakeModel
from tests.test_system_one import TOOLS, LocalClient


class PreflightTest(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, observed=True, complete=True):
        adviser = SystemOne("http://classifier", "clef-flash", 1)
        adviser.available = True
        item = ItemClient(
            LocalClient(),
            {
                "project_id": 0,
                "stream_id": "main",
                "snapshot_id": "snapshot",
            },
            {("src/test.c", 5, "snapshot")} if observed else set(),
        )
        transport = SystemOneClient(item, adviser)
        transport.load_context = AsyncMock(
            return_value={
                "warning_description": "ZERO: division by zero",
                "tp_conditions": "The divisor is zero",
                "trace": [],
                "trace_complete": True,
                "tp_complete": complete,
            }
        )
        model = FakeModel()
        reviewer = Harness(transport, model, TOOLS)
        reviewer.messages.extend(
            tool_exchange(
                "source",
                "agent_source",
                {
                    "project_id": 0,
                    "snapshot_id": "snapshot",
                    "file": "src/test.c",
                    "line": 5,
                },
                json.dumps(
                    {"lines": [{"number": 5, "text": "return 1 / 0;"}]}
                ),
            )
        )
        return reviewer, item, adviser, model

    async def test_threshold_is_applied_before_any_main_model_request(self):
        for confidence in (0.699, 0.7, 0.9):
            reviewer, item, adviser, model = self.fixture()
            with patch.object(
                adviser,
                "classify",
                return_value={
                    "classification": "TP",
                    "confidence": confidence,
                    "can_skip_investigation": confidence >= 0.7,
                },
            ):
                raw = await ReviewPreflight.run(reviewer, item)
            self.assertEqual(raw is not None, confidence >= 0.7)
            self.assertFalse(model.requests)
            self.assertTrue(reviewer.client.preflight_done)
            if raw:
                verdict = json.loads(raw)
                self.assertEqual(verdict["classification"], "TP")
                self.assertEqual(verdict["evidence"][0]["line"], 5)
                self.assertIn("No additional investigator", verdict["comment"])

    async def test_incomplete_guide_or_unobserved_source_prevents_fast_verdict(
        self,
    ):
        for complete, observed in ((False, True), (True, False)):
            reviewer, item, adviser, _ = self.fixture(
                complete=complete, observed=observed
            )
            with patch.object(
                adviser,
                "classify",
                return_value={
                    "classification": "FP",
                    "confidence": 0.99,
                    "can_skip_investigation": True,
                },
            ):
                self.assertIsNone(await ReviewPreflight.run(reviewer, item))

    async def test_failure_preserves_manual_path_and_disables_failed_adviser(
        self,
    ):
        reviewer, item, adviser, model = self.fixture()
        with patch.object(adviser, "classify", return_value=None):
            self.assertIsNone(await ReviewPreflight.run(reviewer, item))
        self.assertFalse(adviser.available)
        self.assertNotIn("ask_system_one", reviewer.allowed)
        self.assertFalse(model.requests)
