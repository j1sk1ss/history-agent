"""The model only adds a checker name to a host-resolved comparison link."""

import json
import tempfile
import unittest

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.history.constants import LINK_WARNINGS_ACTION, URL_ACTION
from history_agent.interfaces.cli import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.runtime.conversation import Conversation
from tests.fake_history import FakeHistory
from tests.test_chat import FakeModel, answer, call
from tests.test_history_links import comparison_url
from tests.test_session import ScriptedUI


def packet(result):
    return json.loads(
        "".join(part.text for part in result.content if part.type == "text")
    )


class LinkWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_prefetched_link_fetches_checker_pages_with_only_host_names(
        self,
    ):
        with FakeHistory() as fake, tempfile.TemporaryDirectory() as directory:
            fake.comparison_warnings["appeared"] = [
                {"warning_id": "wire-warning-1", "checker_name": "NULL"},
                {"warning_id": "wire-warning-2", "checker_name": "NULL"},
            ]
            history = HistoryClient(Settings(fake.url, session="valid"))
            model = FakeModel()
            model.name, model.base = "synthetic", "http://synthetic/v1"
            link_args = {}

            def complete(messages, tools, on_event=None):
                model.requests.append(messages)
                wire = json.dumps(messages)
                for raw in (
                    fake.url,
                    "wire-current",
                    "wire-base",
                    "wire-warning-1",
                    "wire-warning-2",
                    "history-link-1",
                ):
                    self.assertNotIn(raw, wire)
                data = json.loads(messages[-1]["content"])
                step = len(model.requests)
                if step == 1:
                    self.assertEqual(
                        data["warnings_action"], LINK_WARNINGS_ACTION
                    )
                    link_args.update(data["warnings_arguments"])
                    self.assertIsInstance(link_args["link_id"], str)
                    return answer(
                        calls=[
                            call(
                                "history_discover",
                                {"action": LINK_WARNINGS_ACTION},
                            )
                        ]
                    )
                if step == 2:
                    schema = data["input_schema"]
                    self.assertEqual(
                        set(schema["properties"]),
                        {"link_id", "checker_name", "offset", "limit"},
                    )
                    return answer(
                        calls=[
                            call(
                                "history_read",
                                {
                                    "action": LINK_WARNINGS_ACTION,
                                    "arguments": {
                                        **link_args,
                                        "checker_name": "null",
                                        "limit": 1,
                                    },
                                },
                            )
                        ]
                    )
                self.assertEqual(data["total"], 2)
                self.assertEqual(data["subset"], "appeared")
                if data["has_more"]:
                    return answer(
                        calls=[
                            call(
                                "history_read",
                                {
                                    "action": LINK_WARNINGS_ACTION,
                                    "arguments": {
                                        **link_args,
                                        "checker_name": "null",
                                        "limit": 1,
                                        "offset": data["next_offset"],
                                    },
                                },
                            )
                        ]
                    )
                return answer("Получено 2 предупреждения NULL.")

            model.complete = complete
            ui = ScriptedUI([])
            args = parse_args(["--batch-state-dir", directory, "--read-only"])
            url = comparison_url(
                fake.url, current="wire-current", base="wire-base"
            )
            async with Client(create_server(history)) as client:
                conversation = Conversation(client, model, args, history, ui)
                await conversation.initialize()
                await conversation.respond(
                    f"Возьми варны NULL из [{url}]({url})"
                )
            queries = [
                r["query"]
                for r in fake.requests
                if r["action"] == "agent_compare_warnings"
            ]
            self.assertEqual(len(queries), 2)
            self.assertEqual([q["offset"] for q in queries], ["0", "1"])
            self.assertTrue(
                all(
                    q["snapshot_id"] == "wire-current"
                    and q["base_snapshot_id"] == "wire-base"
                    and q["project_id"] == "2"
                    and q["base_project_id"] == "7"
                    for q in queries
                )
            )
            self.assertFalse(fake.writes)
            self.assertFalse(
                any(kind == "batch_started" for kind, _ in ui.events)
            )
            self.assertFalse(
                any(
                    r["action"]
                    in (
                        "get_warning",
                        "get_warnings",
                        "agent_find_warnings",
                        "agent_source",
                    )
                    for r in fake.requests
                )
            )

    async def test_standalone_named_mcp_can_fetch_from_resolved_link(self):
        with FakeHistory() as fake:
            fake.comparison_warnings["both"] = [
                {"warning_id": "raw-warning", "checker_name": "NULL"}
            ]
            history = HistoryClient(Settings(fake.url, session="valid"))
            async with Client(
                create_server(history, named_ids=True)
            ) as client:
                schema = packet(
                    await client.call_tool(
                        "history_discover", {"action": URL_ACTION}
                    )
                )["input_schema"]
                self.assertEqual(schema["properties"]["url"]["type"], "string")
                resolved = packet(
                    await client.call_tool(
                        "history_read",
                        {
                            "action": URL_ACTION,
                            "arguments": {
                                "url": comparison_url(fake.url, "Both/")
                            },
                        },
                    )
                )
                result = packet(
                    await client.call_tool(
                        "history_read",
                        {
                            "action": resolved["warnings_action"],
                            "arguments": {
                                **resolved["warnings_arguments"],
                                "checker_name": "NULL",
                            },
                        },
                    )
                )
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["subset"], "both")
                self.assertNotEqual(
                    result["warnings"][0]["warning_id"], "raw-warning"
                )
                self.assertEqual(result["link_id"], resolved["link_id"])
                rejected = await client.call_tool(
                    "history_read",
                    {
                        "action": LINK_WARNINGS_ACTION,
                        "arguments": {
                            "link_id": "history-link-1",
                            "checker_name": "NULL",
                        },
                    },
                )
                self.assertTrue(rejected.is_error)
            self.assertFalse(fake.writes)
