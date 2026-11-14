"""Invented actions must not erase source evidence or reach the server."""

import json
import unittest

from mcp.types import Tool

from history_agent.inference.model import compact
from history_agent.runtime.action_catalog import constrain_tools
from history_agent.runtime.agent import Harness
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.recovery_context import retained_messages
from tests.test_chat import answer, call
from tests.test_named_context import ScriptedModel, Transport, packets, reply


def tools(worker=False):
    definitions = [
        Tool(
            name=name,
            description="History operation",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": ["string", "null"]},
                    "arguments": {"type": "object"},
                },
            },
        )
        for name in (
            "history_discover",
            "history_read",
            "history_write",
            "history_current",
        )
    ]
    catalog = {
        "actions": [
            {"name": name, "tool": tool}
            for name, tool in (
                ("agent_source", "history_read"),
                ("agent_resolve_symbol", "history_read"),
                ("agent_set_review", "history_write"),
                ("review_diff", "history_read/history_write"),
            )
        ]
    }
    return constrain_tools(definitions, catalog, worker=worker)


def request(action, name="history_read", id="call-1"):
    return answer(
        calls=[
            call(
                name,
                {
                    "action": action,
                    "arguments": {"file": "src/a.c", "line": 7},
                },
                id,
            )
        ]
    )


def assert_complete(test, messages):
    pending = set()
    for message in messages:
        if message["role"] == "tool":
            test.assertIn(message["tool_call_id"], pending)
            pending.remove(message["tool_call_id"])
        else:
            test.assertFalse(pending)
            pending = {entry["id"] for entry in message.get("tool_calls", [])}
    test.assertFalse(pending)


class RecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_catalog_narrows_actions_and_hides_empty_tools(self):
        client = Transport(reply({"actions": []}))

        def corrected(messages, functions):
            names = {tool["function"]["name"] for tool in functions}
            self.assertEqual(names, {"history_discover", "history_current"})
            enum = next(tool for tool in functions
                        if tool["function"]["name"] == "history_discover")
            enum = enum["function"]["parameters"]["properties"]["action"]
            enum = enum["enum"]
            self.assertNotIn("agent_source", enum)
            return answer("No executable server actions available")

        model = ScriptedModel(
            lambda m, t: answer(calls=[
                call("history_discover", {}, "catalog"),
            ]),
            corrected,
        )
        harness = Harness(client, model, tools(), allow_write=True,
                          on_event=lambda *a, **k: None)
        await harness.ask("Discover available actions")

    async def test_invented_action_retains_schema_source_and_partial_success(
        self,
    ):
        schema = {"input_schema": {"properties": {"file": {"type": "string"}}}}
        source = {"lines": [{"number": 7, "text": "return *p;"}]}
        client = Transport(
            reply(schema), reply(source), reply({"targets": []})
        )
        events = []

        def corrected(messages, functions):
            assert_complete(self, messages)
            data = packets(messages)
            self.assertIn(schema, data)
            self.assertIn(source, data)
            error = data[-1]
            self.assertEqual(error["code"], "unsupported_action")
            self.assertIn("agent_resolve_symbol", error["available_actions"])
            self.assertIn("no source text search", error["guidance"])
            return request("agent_resolve_symbol", id="navigate")

        model = ScriptedModel(
            lambda m, t: request("agent_source", "history_discover", "schema"),
            lambda m, t: answer(
                calls=[
                    call("history_read", {"action": "agent_source"}, "source"),
                    call(
                        "history_discover",
                        {"action": "agent_search_code"},
                        "bad",
                    ),
                    call("history_read", {"action": "agent_source"}, "skip"),
                ]
            ),
            corrected,
            lambda m, t: answer("Reviewed using observed source"),
        )
        harness = Harness(
            client,
            model,
            tools(worker=True),
            on_event=lambda kind, **d: events.append((kind, d)),
        )
        await harness.ask("Investigate this warning")
        self.assertEqual(len(client.calls), 3)
        self.assertNotIn("agent_search_code", compact(client.calls))
        failed = [
            d for k, d in events if k == "tool_result" and d.get("error")
        ]
        self.assertEqual(len(failed), 1)
        self.assertIn("Unsupported action", failed[0]["response"])
        self.assertIn("agent_search_code", compact(failed[0]["arguments"]))

    async def test_reload_remembers_previous_unsupported_actions(self):
        def corrected(messages, functions):
            assert_complete(self, messages)
            feedback = packets(messages)[-1]
            self.assertIn(
                "agent_search_code", compact(feedback["previous_errors"])
            )
            self.assertIn("agent_grep", feedback["error"])
            self.assertEqual(sum(m["role"] == "system" for m in messages), 1)
            return answer("Missing context; use UC")

        model = ScriptedModel(
            lambda m, t: request(
                "agent_search_code", "history_discover", "bad1"
            ),
            lambda m, t: request("agent_grep", "history_discover", "bad2"),
            corrected,
        )
        client = Transport()
        harness = Harness(
            client, model, tools(worker=True), on_event=lambda *a, **k: None
        )
        await harness.ask("Review")
        self.assertFalse(client.calls)

    async def test_rejected_write_action_is_known_not_to_have_executed(self):
        def corrected(messages, functions):
            self.assertTrue(functions)
            self.assertFalse(packets(messages)[-1]["write_attempted"])
            return answer("Correct the request before writing")

        client = Transport()
        model = ScriptedModel(
            lambda m, t: request("invented_write", "history_write"),
            corrected,
        )
        harness = Harness(
            client,
            model,
            tools(),
            allow_write=True,
            on_event=lambda *a, **k: None,
        )
        await harness.ask("Save the review")
        self.assertFalse(client.calls)
        self.assertFalse(harness.blocked)


class RetainedContextTest(unittest.TestCase):
    def test_pruning_keeps_host_scope_and_new_reads_without_orphan_calls(self):
        messages = [
            {"role": "system", "content": "Rules"},
            {"role": "user", "content": "Review"},
        ]
        messages += tool_exchange(
            "host-scope", "current", {}, '{"project_id":1}'
        )
        messages += tool_exchange(
            "old",
            "agent_source",
            {},
            compact(
                {
                    "lines": [{"number": 7, "text": "old code" * 100}],
                }
            ),
        )
        messages += tool_exchange("new", "agent_source", {}, '{"lines":[]}')
        messages += tool_exchange(
            "failed", "agent_source", {}, '{"error":"bad"}'
        )
        result = retained_messages(messages, 900)
        assert_complete(self, result)
        ids = [m["tool_call_id"] for m in result if m["role"] == "tool"]
        self.assertEqual(ids, ["host-scope", "new"])
        self.assertLess(len(compact(result).encode()), 900)

    def test_worker_definitions_exclude_jobs_writes_and_current_defaults(self):
        definitions = {tool.name: tool for tool in tools(worker=True)}
        self.assertEqual(
            set(definitions), {"history_read", "history_discover"}
        )
        enum = definitions["history_read"].input_schema["properties"][
            "action"
        ]["enum"]
        self.assertEqual(enum, ["agent_resolve_symbol", "agent_source"])
        self.assertNotIn(
            "agent_search_code",
            json.dumps(definitions["history_read"].input_schema),
        )
