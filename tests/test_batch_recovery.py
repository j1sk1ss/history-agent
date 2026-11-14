"""Queue reads can recover without being mistaken for completed writes."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mcp import Client

from history_agent.history.client import HistoryClient, HistoryError, Settings
from history_agent.interfaces.mcp import create_server
from history_agent.review.batch import BatchRunner
from history_agent.review.classification import STATUSES
from history_agent.review.router import BatchRouter
from history_agent.runtime.agent import Harness
from history_agent.runtime.recovery import ToolFailure
from tests.fake_history import FakeHistory
from tests.test_batch import HTML, ReviewModel, warning
from tests.test_chat import answer, call
from tests.test_named_context import TOOLS, ScriptedModel, Transport, packets

CURRENT = {"project_id": 0, "stream_id": "main", "snapshot_id": "current"}
BASE = {**CURRENT, "snapshot_id": "base"}
DIFF = {
    **CURRENT,
    **{"base_" + k: v for k, v in BASE.items()},
    "subset": "appeared",
    "checker_name": "BUFFER_SHIFT",
}


class BatchRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_queue_failure_reloads_and_resumes_the_same_writable_job(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            worker = Mock(
                side_effect=AssertionError("Empty queue needs no reviewer")
            )
            runner = BatchRunner(
                SimpleNamespace(
                    endpoint="http://history.example/history/api",
                    execute=Mock(
                        side_effect=AssertionError("Unexpected scope lookup")
                    ),
                ),
                None,
                worker,
                directory,
                {"model": "synthetic"},
                allow_write=True,
            )
            queue_reads = []

            async def read(action, args):
                if action == "agent_compare_warnings":
                    queue_reads.append(args)
                    if len(queue_reads) == 1:
                        raise HistoryError("History request failed: timed out")
                    return {
                        "current": CURRENT,
                        "base": BASE,
                        "scope": CURRENT,
                        "subset": "appeared",
                        "warnings": [],
                        "total": 0,
                        "has_more": False,
                    }
                self.assertEqual(action, "get_value_filter")
                return {
                    "classification": [
                        {"name": name, "id": i}
                        for i, name in enumerate(STATUSES.values())
                    ]
                }

            runner.read = AsyncMock(side_effect=read)
            router = BatchRouter(Transport(), runner)

            def start(messages, tools):
                return answer(
                    calls=[
                        call(
                            "history_read",
                            {
                                "action": "review_diff",
                                "arguments": packets(messages)[0],
                            },
                        )
                    ]
                )

            def resume(messages, tools):
                self.assertTrue(tools, packets(messages)[-1])
                # Qwen rejects a system role anywhere except position zero.
                self.assertEqual(
                    [
                        i
                        for i, m in enumerate(messages)
                        if m["role"] == "system"
                    ],
                    [0],
                )
                error = packets(messages)[-1]
                self.assertTrue(error["context_reloaded"])
                self.assertIn("timed out", error["error"])
                self.assertEqual(error["resume_action"], "review_diff")
                self.assertEqual(
                    error["resume_arguments"], {"job_id": error["job_id"]}
                )
                self.assertNotIn(runner.last_job_id, json.dumps(messages))
                self.assertFalse(runner.writes_may_have_completed)
                return answer(
                    calls=[
                        call(
                            "history_read",
                            {
                                "action": error["resume_action"],
                                "arguments": error["resume_arguments"],
                            },
                        )
                    ]
                )

            def finished(messages, tools):
                self.assertNotIn(runner.last_job_id, json.dumps(messages))
                return answer("No warnings")

            model = ScriptedModel(start, resume, finished)
            harness = Harness(
                router,
                model,
                TOOLS,
                allow_write=True,
                on_event=lambda *a, **k: None,
            )
            result = await harness.ask(
                "Review this comparison",
                prefetched=[("agent_resolve_history_url", {}, DIFF)],
            )
            self.assertEqual(result, "No warnings")
            self.assertFalse(harness.blocked)
            self.assertEqual(len(list(Path(directory).glob("*.sqlite3"))), 1)
            self.assertEqual(queue_reads[0], queue_reads[1])
            self.assertEqual(runner.last_summary["states"], {})
            worker.assert_not_called()

    async def test_failed_write_still_requires_review_history_inspection(self):
        with FakeHistory() as fake, tempfile.TemporaryDirectory() as directory:
            fake.warnings = [warning("a1")]
            fake.guide_html = HTML
            fake.disconnect_on_write = True
            history = HistoryClient(
                Settings(fake.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools

                def reviewer(scoped):
                    return Harness(
                        scoped,
                        ReviewModel(),
                        tools,
                        on_event=lambda *a, **k: None,
                    )

                runner = BatchRunner(
                    history,
                    client,
                    reviewer,
                    directory,
                    {"model": "synthetic"},
                    allow_write=True,
                )
                router = BatchRouter(client, runner)
                with self.assertRaises(ToolFailure) as caught:
                    await router.call_tool(
                        "history_read",
                        {
                            "action": "review_batch",
                            "arguments": {
                                "project_id": 0,
                                "stream_id": "master",
                                "snapshot_id": "snapshot",
                            },
                        },
                    )
                self.assertTrue(caught.exception.uncertain_write)
                self.assertEqual(
                    runner.last_summary["states"], {"uncertain": 1}
                )
                self.assertEqual(len(fake.writes), 1)
