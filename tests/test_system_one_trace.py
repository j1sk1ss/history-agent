import json
import unittest
from unittest.mock import patch

from mcp.types import CallToolResult, TextContent

from history_agent.review.client import ItemClient
from history_agent.review.system_one import (
    SystemOne,
    SystemOneClient,
    load_warning_context,
    warning_context,
)
from history_agent.review.system_one_trace import trace_points
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.tools import ToolClient

TARGET = {
    "project_id": 1, "stream_id": "main",
    "snapshot_id": "PRIVATE_CURRENT", "warning_id": "PRIVATE_WARNING",
}
TRACE = {"events": [
    {"message": "Value is initialized", "location": {
        "file_path": "src/init.c", "line": "3",
    }},
    {"message": "Value is read", "location": {
        "file_path": "src/use.c", "line": 20,
        "snapshot_override": "PRIVATE_OLD",
    }, "children": [
        {"message": "The same value reaches the defect", "location": {
            "file_path": "src/use.c", "line": 20,
            "snapshot_override": "PRIVATE_OLD",
        }},
    ]},
]}


def reply(data, error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data))],
        is_error=error,
    )


class TraceClient(ToolClient):
    def __init__(self, missing_source=False, serialized=False):
        self.calls = []
        self.missing_source = missing_source
        self.serialized = serialized

    async def call_tool(self, name, arguments):
        action, args = arguments["action"], arguments["arguments"]
        self.calls.append((action, args))
        if action == "get_warning_trace_and_role":
            trace = json.dumps(TRACE) if self.serialized else TRACE
            return reply({"trace": trace})
        if action == "agent_source":
            if self.missing_source and args["file"] == "src/use.c":
                return reply({"error": "PRIVATE_SOURCE_ERROR"}, error=True)
            version = (
                "old" if args["snapshot_id"] == "PRIVATE_OLD" else "current"
            )
            return reply({
                "snapshot_id": args["snapshot_id"], "hash": "PRIVATE_HASH",
                "lines": [{
                    "number": line, "text": f"{version} code line {line}",
                    "metadata": "PRIVATE_LINE_METADATA",
                } for line in range(
                    max(1, args["line"] - 5), args["line"] + 6
                )],
            })
        raise AssertionError(action)


class SystemOneTraceTest(unittest.IsolatedAsyncioTestCase):
    def test_each_point_defaults_to_the_warning_snapshot(self):
        points = trace_points({"location": {
            "file_path": "parent.c", "line": 1, "snapshot_override": "OLD",
        }, "children": [{"location": {
            "file_path": "child.c", "line": 2, "snapshot_override": None,
        }}]}, "CURRENT")
        self.assertEqual(
            [point["_snapshot"] for point in points], ["OLD", "CURRENT"]
        )

    def test_serialized_nested_trace_and_text_locations_are_parsed(self):
        points = trace_points(json.dumps({"events": [{
            "message": "Read here", "location": "src/a.c:12:3",
        }]}), "INTERNAL_SNAPSHOT")
        self.assertEqual(points, [{
            "message": "Read here", "file": "src/a.c", "line": 12,
            "_snapshot": "INTERNAL_SNAPSHOT",
        }])

    def transport(
        self, missing_source=False, inline=False, cached=False,
        serialized=False,
    ):
        backend = TraceClient(missing_source, serialized)
        client = ItemClient(backend, TARGET, set())
        warning = {
            "checker_name": "ZERO", "message": "Division by zero",
            "attributes": {"comment": "PRIVATE_REVIEW"},
        }
        if inline:
            warning["trace"] = TRACE
        client.observe("get_warning", TARGET, {"warning": warning})
        messages = tool_exchange(
            "warning", "get_warning", TARGET, json.dumps({"warning": warning})
        ) + tool_exchange(
            "checker", "agent_checker_info", {"checker_name": "ZERO"},
            json.dumps({"documentation": {"text": "TP when divisor is zero"}}),
        )
        if cached:
            messages += tool_exchange("source", "agent_source", {
                "project_id": 1, "snapshot_id": "PRIVATE_CURRENT",
                "file": "src/init.c", "line": 3, "before": 5, "after": 5,
            }, json.dumps({"lines": [
                {"number": line, "text": f"cached code line {line}"}
                for line in range(1, 15)
            ]}))
        adviser = SystemOne("http://systemone", "fast", 1)
        adviser.available = True
        transport = SystemOneClient(client, adviser)
        transport.context = lambda: warning_context(messages)
        transport.load_context = lambda: load_warning_context(client, messages)
        return transport, adviser, backend

    async def assess(self, **options):
        transport, adviser, backend = self.transport(**options)
        with patch.object(adviser, "classify", return_value={
            "classification": "TP", "confidence": 0.95,
            "can_skip_investigation": True,
        }) as classify:
            result = await transport.call_tool("ask_system_one", {
                "code": "int value = 1 / divisor;", "environment": "C",
            })
        state = json.loads(classify.call_args.args[0])
        self.assertEqual(
            state["warning_description"], "ZERO: Division by zero"
        )
        self.assertNotIn("PRIVATE", json.dumps(state))
        return state, json.loads(result.content[0].text), backend.calls

    async def test_every_trace_point_has_five_lines_before_and_after(self):
        state, result, calls = await self.assess()
        self.assertEqual(len(state["trace"]), 3)
        self.assertEqual(
            [line["number"] for line in state["trace"][0]["code"]],
            list(range(1, 9)),
        )
        for point in state["trace"][1:]:
            self.assertEqual(point["file"], "src/use.c")
            self.assertEqual(
                [line["number"] for line in point["code"]], list(range(15, 26))
            )
            self.assertIn("old code", point["code"][0]["text"])
        source = [args for action, args in calls if action == "agent_source"]
        self.assertEqual(len(source), 2)
        self.assertTrue(all(
            args["before"] == args["after"] == 5 for args in source
        ))
        self.assertEqual(source[1]["snapshot_id"], "PRIVATE_OLD")
        self.assertTrue(result["can_skip_investigation"])

    async def test_serialized_overrides_are_observed_before_source_reads(self):
        state, result, calls = await self.assess(serialized=True)
        self.assertEqual(len(state["trace"]), 3)
        self.assertIn("old code", state["trace"][1]["code"][0]["text"])
        self.assertTrue(result["can_skip_investigation"])

    async def test_inline_warning_trace_and_cached_source_are_reused(self):
        state, result, calls = await self.assess(inline=True, cached=True)
        self.assertEqual([action for action, _ in calls], ["agent_source"])
        self.assertEqual(len(state["trace"][0]["code"]), 8)
        self.assertIn("cached code", state["trace"][0]["code"][0]["text"])
        self.assertTrue(result["can_skip_investigation"])

    async def test_missing_source_keeps_trace_and_requires_investigation(self):
        state, result, _ = await self.assess(missing_source=True)
        self.assertEqual(len(state["trace"]), 3)
        self.assertTrue(state["trace"][1]["source_unavailable"])
        self.assertTrue(state["trace"][2]["source_unavailable"])
        self.assertNotIn("source_unavailable", state["trace"][0])
        self.assertFalse(result["can_skip_investigation"])
        self.assertNotIn("error", result)
