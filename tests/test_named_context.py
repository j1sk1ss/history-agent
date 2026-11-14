"""Server IDs stay in the host; model corrections use scoped session names."""

import copy
import json
import unittest
from types import SimpleNamespace

from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool

from history_agent.history.client import HistoryClient, Settings
from history_agent.inference.model import ChatError, compact
from history_agent.interfaces.mcp import create_server
from history_agent.review.models import ReviewVerdict
from history_agent.review.router import BatchRouter
from history_agent.runtime.agent import Harness
from history_agent.runtime.identities import IdentityError, IdentityMap
from history_agent.runtime.model_context import ModelContext
from history_agent.runtime.recovery import ToolFailure
from history_agent.runtime.tools import ToolClient
from tests.fake_history import FakeHistory
from tests.test_chat import answer, call

SCOPE = {
    "project_id": 987654321,
    "stream_id": "private-branch-opaque",
    "snapshot_id": "private-snapshot-opaque",
}
TOOLS = [
    Tool(
        name=name,
        description="History operation",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    for name in ("history_discover", "history_read", "history_current")
]


def reply(data, *, error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=compact(data))],
        is_error=error,
    )


class Transport(ToolClient):
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ScriptedModel:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def complete(self, messages, tools, on_event=None):
        self.requests.append(
            copy.deepcopy({"messages": messages, "tools": tools})
        )
        return self.steps.pop(0)(messages, tools)


def packets(messages):
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


class NamesTest(unittest.TestCase):
    def test_names_are_typed_stable_and_round_trip_base_and_overrides(self):
        names = IdentityMap()
        raw = {
            **SCOPE,
            "base_project_id": SCOPE["project_id"],
            "base_snapshot_id": "private-base-opaque",
            "location": {"snapshot_override": SCOPE["snapshot_id"]},
            "warning_id": "private-warning-opaque",
            "job_id": "private-job",
            "hash": "private-file-hash",
            "token_group": "private-token",
        }
        encoded = names.encode(raw)
        self.assertEqual(encoded["project_id"], "diana")
        self.assertEqual(encoded["stream_id"], "max")
        self.assertEqual(encoded["snapshot_id"], "chloe")
        self.assertEqual(encoded["base_project_id"], "diana")
        self.assertEqual(encoded["location"]["snapshot_override"], "chloe")
        self.assertEqual(names.decode(encoded), raw)
        self.assertEqual(names.encode(raw), encoded)
        for value in ("invented", 987654321, "max"):
            with self.assertRaises(IdentityError):
                names.decode({"project_id": value})

    def test_schema_changes_ids_to_names_but_preserves_offsets_and_code(self):
        context = ModelContext()
        raw = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                    },
                    "line": {"type": "integer", "minimum": 1},
                },
            },
            "trace": json.dumps([{"snapshot_override": "trace-snapshot"}]),
            "constraints": {"project_id": "nonnegative numeric ID",
                            "line": "one based"},
            "lines": [{"number": 3, "text": "project_id = 987654321;"}],
        }
        result = context.packet(raw)
        prop = result["input_schema"]["properties"]["project_id"]
        self.assertEqual(prop["type"], "string")
        self.assertNotIn("default", prop)
        self.assertEqual(result["lines"], raw["lines"])
        self.assertNotIn("trace-snapshot", result["trace"])
        self.assertIn("host name", result["constraints"]["project_id"])
        self.assertEqual(result["constraints"]["line"], "one based")
        self.assertEqual(len(context.names.forward), 1)
        self.assertEqual(
            raw["input_schema"]["properties"]["project_id"]["type"], "integer"
        )

    def test_error_redaction_preserves_entity_words_and_source_paths(self):
        names = IdentityMap()
        names.encode(
            {
                "snapshot_id": "snapshot",
                "warning_id": "a1",
                "project_id": "opaque-project-value",
            }
        )
        message = "Unknown snapshot name at src/a1.c for opaque-project-value"
        result = names.redact_error(message)
        self.assertIn("Unknown snapshot name at src/a1.c", result)
        self.assertNotIn("opaque-project-value", result)

    def test_fenced_verdicts_and_history_links(self):
        context = ModelContext()
        named = context.names.encode({"snapshot_id": "private-snapshot"})
        verdict = {
            "classification": "TP",
            "comment": "a.c:3",
            "evidence": [{"file": "a.c", "line": 3, **named}],
        }
        result = context.verdict("```json\n" + compact(verdict) + "\n```")
        self.assertEqual(
            json.loads(result)["evidence"][0]["snapshot_id"],
            "private-snapshot",
        )
        link = "https://history.local/#show/project/branch/private-snapshot"
        self.assertNotIn("private-snapshot", context.text("Review " + link))

    def test_repair_drafts_and_validation_errors_hide_raw_ids(self):
        context = ModelContext()
        raw_address = compact(
            {"file": "src/a1.c", "line": 3, "snapshot_id": "snapshot"}
        )
        payload = {
            "draft": raw_address,
            "validation_errors": [
                "Unread evidence: " + raw_address,
            ],
        }
        result = context.packet(payload)
        self.assertEqual(json.loads(result["draft"])["snapshot_id"], "diana")
        self.assertNotIn('"snapshot"', result["validation_errors"][0])
        self.assertIn("diana", compact(result))
        self.assertIn("src/a1.c", compact(result))


class NamedMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_mcp_uses_names_and_rejects_raw_ids(self):
        with FakeHistory() as fake:
            server = create_server(
                HistoryClient(
                    Settings(
                        fake.url,
                        "tester",
                        "test-secret",
                    )
                ),
                named_ids=True,
            )
            async with Client(server) as client:
                current = await client.call_tool("history_current", {})
                scope = json.loads(current.content[0].text)["scope"]
                self.assertEqual(
                    scope,
                    {
                        "project_id": "diana",
                        "stream_id": "max",
                        "snapshot_id": "chloe",
                    },
                )
                schema = await client.call_tool(
                    "history_discover",
                    {
                        "action": "agent_source",
                    },
                )
                prop = json.loads(schema.content[0].text)["input_schema"][
                    "properties"
                ]
                self.assertEqual(prop["project_id"]["type"], "string")
                before = len(fake.requests)
                error = await client.call_tool(
                    "history_read",
                    {
                        "action": "agent_source",
                        "arguments": {
                            "project_id": 2,
                            "snapshot_id": "snapshot-first",
                            "file": "a.c",
                            "line": 3,
                        },
                    },
                )
                self.assertTrue(error.is_error)
                self.assertEqual(len(fake.requests), before)
                result = await client.call_tool(
                    "history_read",
                    {
                        "action": "agent_source",
                        "arguments": {
                            "project_id": scope["project_id"],
                            "snapshot_id": scope["snapshot_id"],
                            "file": "a.c",
                            "line": 3,
                        },
                    },
                )
                self.assertFalse(result.is_error)
                data = json.loads(result.content[0].text)
                self.assertEqual(data["snapshot_id"], "chloe")
                self.assertEqual(fake.requests[-1]["query"]["project_id"], "2")


class NamedHarnessTest(unittest.IsolatedAsyncioTestCase):
    async def test_names_in_assistant_answers_survive_the_next_turn(self):
        model = ScriptedModel(
            lambda m, t: answer(compact(packets(m)[0])),
            lambda m, t: answer("Continued"),
        )
        harness = Harness(
            Transport(), model, TOOLS, on_event=lambda *a, **k: None
        )
        first = await harness.ask(
            "Resolve", prefetched=[("current", {}, SCOPE)]
        )
        await harness.ask("Continue with that scope")
        answers = [
            m["content"]
            for m in model.requests[-1]["messages"]
            if m["role"] == "assistant" and m.get("content")
        ]
        self.assertEqual(answers, [first])
        self.assertEqual(len(harness.model_context.names.forward), 3)

    async def test_invalid_batch_request_reloads_and_can_be_corrected(self):
        runner = SimpleNamespace(last_job_id=None, allow_write=True)
        client = BatchRouter(Transport(), runner)

        def corrected(messages, tools):
            self.assertTrue(tools)
            self.assertIn(
                "Invalid review_batch", packets(messages)[-1]["error"]
            )
            return answer("Correct the batch selection")

        model = ScriptedModel(
            lambda m, t: answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "review_batch",
                            "arguments": {"invalid": 1},
                        },
                    )
                ]
            ),
            corrected,
        )
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        await harness.ask("Review")
        self.assertFalse(harness.blocked)

    async def test_failed_job_references_are_named_in_error_feedback(self):
        raw_job = "0123456789abcdef0123456789abcdef"
        client = Transport(
            ToolFailure(
                "Batch stopped; job_id=" + raw_job,
                context={"job_id": raw_job},
                uncertain_write=True,
            )
        )

        def feedback(messages, tools):
            self.assertEqual(tools, [])
            self.assertEqual(packets(messages)[-1]["job_id"], "diana")
            self.assertNotIn(raw_job, compact(messages))
            return answer("Inspect job diana before retrying")

        model = ScriptedModel(
            lambda m, t: answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "review_batch",
                            "arguments": {},
                        },
                    )
                ]
            ),
            feedback,
        )
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        await harness.ask("Review")
        self.assertTrue(harness.blocked)

    async def test_full_scope_read_and_verdict_use_names_only_on_model_wire(
        self,
    ):
        def read(messages, tools):
            scope = packets(messages)[0]
            return answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "agent_source",
                            "arguments": {
                                "project_id": scope["project_id"],
                                "snapshot_id": scope["snapshot_id"],
                                "file": "a.c",
                                "line": 3,
                            },
                        },
                    )
                ]
            )

        def verdict(messages, tools):
            data = packets(messages)[-1]
            return answer(
                calls=[
                    call(
                        "submit_verdict",
                        {
                            "classification": "TP",
                            "comment": "a.c:3 proves the condition",
                            "evidence": [
                                {
                                    "file": "a.c",
                                    "line": 3,
                                    "snapshot_id": data["snapshot_id"],
                                }
                            ],
                        },
                    )
                ]
            )

        client = Transport(
            reply(
                {
                    "snapshot_id": SCOPE["snapshot_id"],
                    "lines": [{"number": 3, "text": "return *p;"}],
                }
            )
        )
        model = ScriptedModel(read, verdict)
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        harness.set_result_schema(ReviewVerdict.model_json_schema())
        result = json.loads(
            await harness.ask(
                "Review",
                prefetched=[
                    ("current", {}, SCOPE),
                ],
            )
        )
        self.assertEqual(
            result["evidence"][0]["snapshot_id"], SCOPE["snapshot_id"]
        )
        self.assertEqual(
            client.calls[0][1]["arguments"]["project_id"], SCOPE["project_id"]
        )
        wire = compact(model.requests)
        for value in SCOPE.values():
            self.assertNotIn(str(value), wire)
        self.assertIn("diana", wire)
        self.assertIn("chloe", wire)

    async def test_unknown_name_reloads_without_calling_transport(self):
        client = Transport()
        model = ScriptedModel(
            lambda m, t: answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "agent_source",
                            "arguments": {"project_id": "invented"},
                        },
                    )
                ]
            ),
            lambda m, t: answer("Need to resolve scope"),
        )
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        self.assertEqual(await harness.ask("Read"), "Need to resolve scope")
        self.assertFalse(client.calls)
        self.assertTrue(
            packets(model.requests[1]["messages"])[-1]["context_reloaded"]
        )
        self.assertIn("Unknown project name", compact(model.requests[1]))

    async def test_reload_preserves_names_and_stops_remaining_calls(
        self,
    ):
        client = Transport(
            OSError("Temporary backend failure"), reply({"ok": True})
        )

        def request(messages, tools):
            scope = packets(messages)[0]
            return answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "branches",
                            "arguments": {"project_id": scope["project_id"]},
                        },
                    ),
                    call(
                        "history_read",
                        {"action": "get_projects", "arguments": {}},
                        "skip",
                    ),
                ]
            )

        def corrected(messages, tools):
            self.assertIn(
                "Temporary backend failure", packets(messages)[-1]["error"]
            )
            return answer(
                calls=[
                    call(
                        "history_read",
                        {
                            "action": "branches",
                            "arguments": {"project_id": "diana"},
                        },
                    )
                ]
            )

        model = ScriptedModel(
            request, corrected, lambda m, t: answer("Recovered")
        )
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        self.assertEqual(
            await harness.ask("Read", prefetched=[("current", {}, SCOPE)]),
            "Recovered",
        )
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(
            all(args["action"] == "branches" for _, args in client.calls)
        )
        self.assertFalse(harness.blocked)
        harness.reset()
        self.assertEqual(
            harness.model_context.names.decode({"project_id": "diana"}),
            {"project_id": SCOPE["project_id"]},
        )

    async def test_tool_errors_have_a_bounded_reload_budget(self):
        client = Transport(*[reply({"error": "Unavailable"}, error=True)] * 3)

        def step(messages, tools):
            return answer(calls=[call("history_discover", {})])

        model = ScriptedModel(step, step, step)
        harness = Harness(client, model, TOOLS, on_event=lambda *a, **k: None)
        with self.assertRaisesRegex(ChatError, "2 automatic reloads"):
            await harness.ask("Discover")
        self.assertEqual(len(client.calls), 3)
