"""Link changes replace stale chat context without losing scope on reloads."""

import json
import tempfile
import unittest

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.cli import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.runtime.conversation import Conversation
from tests.fake_history import FakeHistory
from tests.test_chat import answer, call
from tests.test_history_links import comparison_url
from tests.test_named_context import ScriptedModel, packets
from tests.test_session import ScriptedUI


class LinkContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_project_link_replaces_old_context_and_followups_pin_it(
        self,
    ):
        with FakeHistory() as fake, tempfile.TemporaryDirectory() as directory:

            def first(messages, tools):
                data = packets(messages)[-1]
                self.assertEqual(data["display"]["current_project"], "Alpha")
                return answer(calls=[call("history_current", {})])

            def old_answer(messages, tools):
                return answer("Stale Alpha marker")

            def second(messages, tools):
                self.assertNotIn("Stale Alpha marker", json.dumps(messages))
                self.assertNotIn("Alpha", json.dumps(messages))
                data = packets(messages)[-1]
                self.assertEqual(data["kind"], "project")
                self.assertEqual(data["display"]["current_project"], "Zulu")
                return answer(calls=[call("history_current", {})])

            def selected(messages, tools):
                data = packets(messages)[-1]
                self.assertEqual(data["display"]["current_project"], "Zulu")
                return answer("Zulu selection")

            def followup(messages, tools):
                return answer(calls=[call("history_current", {})])

            model = ScriptedModel(
                first,
                old_answer,
                second,
                selected,
                followup,
                selected,
            )
            model.name, model.base = "synthetic", "http://synthetic/v1"
            history = HistoryClient(Settings(fake.url, session="valid"))
            args = parse_args(
                [
                    "--batch-state-dir",
                    directory,
                    "--read-only",
                    "--max-tool-retries",
                    "13",
                ]
            )
            async with Client(create_server(history)) as client:
                conversation = Conversation(
                    client,
                    model,
                    args,
                    history,
                    ScriptedUI([]),
                )
                await conversation.initialize()
                self.assertEqual(conversation.harness.max_tool_retries, 13)
                self.assertEqual(
                    conversation.make_reviewer(None).max_tool_retries, 13
                )
                await conversation.respond(comparison_url(fake.url))
                url = (
                    fake.url
                    + "/history/svacelight/index.html#show/"
                    "Zulu/release/another/"
                )
                await conversation.respond(url)
                before = len(fake.requests)
                await conversation.respond("Продолжай с тем же проектом")
                self.assertEqual(len(fake.requests), before)
                scope = next(
                    iter(conversation.router.link_scope.links.values())
                )["scope"]
                self.assertEqual(
                    scope,
                    {
                        "project_id": 7,
                        "stream_id": "release",
                        "snapshot_id": "another",
                    },
                )
                conversation.reset()
                self.assertEqual(conversation.router.link_scope.links, {})

    async def test_completed_summary_masks_ids_and_retains_link_labels(self):
        with FakeHistory() as fake, tempfile.TemporaryDirectory() as directory:
            raw_job = "1234567890abcdef1234567890abcdef"

            def check(messages, tools):
                text = json.dumps(messages)
                for raw in (
                    raw_job,
                    "current-private",
                    "base-private",
                    fake.url,
                ):
                    self.assertNotIn(raw, text)
                self.assertIn(
                    '"current_project":"Alpha"', messages[-2]["content"]
                )
                return answer("Retained scoped summary")

            model = ScriptedModel(lambda m, t: answer("Selected"), check)
            model.name, model.base = "synthetic", "http://synthetic/v1"
            history = HistoryClient(Settings(fake.url, session="valid"))
            async with Client(create_server(history)) as client:
                conversation = Conversation(
                    client,
                    model,
                    parse_args(["--batch-state-dir", directory]),
                    history,
                    ScriptedUI([]),
                )
                await conversation.initialize()
                await conversation.respond(
                    comparison_url(
                        fake.url,
                        current="current-private",
                        base="base-private",
                    )
                )
                selected = next(
                    iter(conversation.router.link_scope.links.values())
                )
                conversation.retain_summary(
                    {
                        "job_id": raw_job,
                        "scope": selected["scope"],
                        "write": False,
                        "total": 0,
                        "states": {},
                        "classifications": {},
                    }
                )
                await conversation.respond("Что в выбранном проекте?")
