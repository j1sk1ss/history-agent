"""Single warnings share the isolated pipeline, timing and write guards."""

import json
import unittest

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.arguments import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.review.journal import Journal
from history_agent.review.router import BatchRouter
from history_agent.runtime.request_scope import command_arguments
from tests import test_batch as batch_fixture
from tests.test_batch import SCOPE, ReviewModel


class SingleReviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = batch_fixture.BatchTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    async def test_single_warning_does_not_enumerate_or_keep_other_warnings(
        self,
    ):
        with self.fixture.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel()
                runner = self.fixture.runner(history, client, tools, model)
                summary = await runner.run({**SCOPE, "warning_id": "a1"})
                self.assertEqual(summary["total"], 1)
                self.assertEqual(summary["timing"]["attempts"], 1)
                self.assertEqual(len(model.requests), 1)
                job = Journal(self.fixture.root, summary["job_id"])
                try:
                    self.assertEqual(
                        [item.warning_id for item in job.items()], ["a1"]
                    )
                finally:
                    job.close()
                requests = [request["action"] for request in server.requests]
                self.assertNotIn("agent_find_warnings", requests)

    async def test_single_read_never_saves_but_explicit_write_can_save(self):
        with self.fixture.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                runner = self.fixture.runner(
                    history,
                    client,
                    tools,
                    ReviewModel(),
                    allow_write=True,
                )
                router = BatchRouter(client, runner)
                arguments = {
                    "action": "review_batch",
                    "arguments": {**SCOPE, "warning_id": "a1"},
                }
                result = await router.call_tool("history_read", arguments)
                data = json.loads(result.content[0].text)
                self.assertEqual(data["states"], {"reviewed": 1})
                self.assertFalse(server.writes)
                resumed = await router.call_tool(
                    "history_read",
                    {
                        "action": "review_batch",
                        "arguments": {"job_id": data["job_id"]},
                    },
                )
                self.assertEqual(
                    json.loads(resumed.content[0].text)["states"],
                    {"reviewed": 1},
                )
                self.assertFalse(server.writes)
                result = await router.call_tool("history_write", arguments)
                self.assertEqual(
                    json.loads(result.content[0].text)["states"], {"saved": 1}
                )
                self.assertEqual(len(server.writes), 1)

    async def test_review_command_keeps_explicit_warning_scope(self):
        values, resume = await command_arguments(
            "/review 0 master snapshot a1",
            parse_args([]),
            None,
        )
        self.assertFalse(resume)
        self.assertEqual(values["warning_id"], "a1")
        self.assertEqual(values["snapshot_id"], "snapshot")
