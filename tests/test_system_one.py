import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp.types import CallToolResult, TextContent, Tool

from history_agent.history.constants import URL_ACTION
from history_agent.interfaces.arguments import parse_args
from history_agent.review.client import ItemClient
from history_agent.review.system_one import (
    CRITERIA,
    SystemOne,
    SystemOneClient,
    warning_context,
)
from history_agent.runtime.agent import Harness
from history_agent.runtime.conversation import Conversation
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.tools import ToolClient
from tests.test_chat import FakeModel, answer, call

TOOLS = [
    Tool(name=name, input_schema={"type": "object"})
    for name in ("history_discover", "history_read")
]


class LocalClient(ToolClient):
    async def list_tools(self):
        return SimpleNamespace(tools=TOOLS)

    async def call_tool(self, name, arguments):
        raise AssertionError("Unexpected history call")


class SystemOneTest(unittest.IsolatedAsyncioTestCase):
    async def test_prompts_and_tool_require_a_successful_probe(self):
        for configured, working in (
            (False, False), (True, False), (True, True),
        ):
            with self.subTest(configured=configured, working=working):
                options = ["--plain"]
                if configured:
                    options += ["--system-one-model", "fast-model"]
                args = parse_args(options)
                model = FakeModel()
                model.base, model.name = "http://main", "main"
                conversation = Conversation(
                    LocalClient(), model, args, None, None
                )
                with patch.object(
                    SystemOne, "classify",
                    return_value={"classification": "FP"} if working else None,
                ) as probe:
                    await conversation.initialize()
                self.assertEqual(probe.call_count, int(configured))
                reviewer = conversation.make_reviewer(ItemClient(
                    LocalClient(), {
                        "project_id": 0, "stream_id": "main",
                        "snapshot_id": "snapshot",
                    }, set(),
                ))
                for harness in (conversation.harness, reviewer):
                    for _ in range(2):
                        self.assertEqual(
                            "ask_system_one" in harness.allowed, working
                        )
                        self.assertEqual(
                            "System 1" in harness.messages[0]["content"],
                            working,
                        )
                        if working:
                            self.assertIn(
                                "fast-model", harness.messages[0]["content"]
                            )
                        harness.reset()

    async def test_runtime_failure_removes_tool_and_instructions(self):
        adviser = SystemOne("http://systemone", "fast", 1)
        adviser.available = True
        model = FakeModel(
            answer(calls=[call(
                "ask_system_one", {"code": "int a;", "environment": "C"}
            )]),
            answer("Investigate normally"),
        )
        transport = SystemOneClient(LocalClient(), adviser)
        transport.context = lambda: {
            "warning_description": "Possibly uninitialized a",
            "tp_conditions": "Reading an uninitialized value is TP",
        }
        harness = Harness(
            transport, model, TOOLS,
            on_event=lambda *args, **kwargs: None,
        )
        with patch.object(adviser, "classify", return_value=None):
            self.assertEqual(
                await harness.ask("Review"), "Investigate normally"
            )
        self.assertIn("System 1", model.requests[0]["messages"][0]["content"])
        self.assertNotIn(
            "System 1", model.requests[1]["messages"][0]["content"]
        )
        self.assertNotIn("ask_system_one", harness.allowed)
        self.assertFalse(adviser.available)
        harness.reset()
        self.assertNotIn("System 1", harness.messages[0]["content"])

    async def test_payload_has_only_code_environment_and_host_metadata(self):
        adviser = SystemOne("http://systemone", "fast", 1)
        adviser.available = True
        transport = SystemOneClient(LocalClient(), adviser)
        transport.context = lambda: {
            "warning_description": "Division by zero",
            "tp_conditions": "The divisor evaluates to zero",
            "trace": [],
            "snapshot_id": "PRIVATE_SNAPSHOT",
            "review": "PRIVATE_REVIEW",
        }
        with patch(
            "history_agent.review.system_one._request"
        ) as request, patch(
            "history_agent.review.system_one._records",
            return_value=iter([{"answers": {"sentiment": {
                "choice": "TP", "confidence": 0.9,
            }}}]),
        ):
            await transport.call_tool("ask_system_one", {
                "code": "int value = 1 / divisor;",
                "environment": "C; divisor is zero",
            })
        payload = request.call_args.args[2]
        self.assertIn("int value = 1 / divisor;", payload["state"])
        self.assertIn("C; divisor is zero", payload["state"])
        self.assertIn("Reported warning:\nDivision by zero", payload["state"])
        instructions = payload["questions"]["sentiment"]["instructions"]
        self.assertIn("Division by zero", instructions)
        self.assertIn("The divisor evaluates to zero", instructions)
        self.assertIn("Checker documentation", instructions)
        self.assertEqual(
            payload["questions"]["sentiment"]["criteria"], CRITERIA
        )
        self.assertNotIn("PRIVATE", json.dumps(payload))

    def test_context_selects_current_warning_and_matching_checker_text(self):
        messages = [{"role": "user", "content": "PRIVATE_CHAT"}]
        for checker in ("OLD_CHECKER", "ZERO"):
            messages += tool_exchange(
                "guide-" + checker, "agent_checker_info",
                {"checker_name": checker}, json.dumps({
                    "checker_name": checker, "source_url": "PRIVATE_URL",
                    "documentation": {"text": "TP conditions for " + checker},
                }),
            )
            messages += tool_exchange(
                "warning-" + checker, "get_warning",
                {"snapshot_id": "PRIVATE_SNAPSHOT"}, json.dumps({
                    "warning": {
                        "checker_name": checker, "message": "Division by zero",
                        "warning_id": "PRIVATE_ID",
                        "comment": "PRIVATE_REVIEW",
                    },
                }),
            )
        messages += tool_exchange(
            "page", "agent_checker_documentation",
            {"checker_name": "ZERO", "offset": 20},
            json.dumps({"text": "Further conditions"}),
        )
        context = warning_context(messages)
        self.assertEqual(context, {
            "checker_name": "ZERO",
            "warning_description": "ZERO: Division by zero",
            "tp_conditions": "TP conditions for ZERO\nFurther conditions",
        })
        self.assertNotIn("PRIVATE", json.dumps(context))
        self.assertNotIn("OLD_CHECKER", json.dumps(context))

    async def test_early_code_only_call_gives_a_provisional_assessment(self):
        adviser = SystemOne("http://systemone", "fast", 1)
        adviser.available = True
        transport = SystemOneClient(LocalClient(), adviser)
        with patch.object(adviser, "classify", return_value={
            "classification": "TP", "confidence": 0.95,
            "can_skip_investigation": True,
        }) as classify:
            result = await transport.call_tool("ask_system_one", {
                "code": "int a;",
            })
        data = json.loads(result.content[0].text)
        self.assertNotIn("error", data)
        self.assertEqual(data["classification"], "TP")
        self.assertFalse(data["can_skip_investigation"])
        state = json.loads(classify.call_args.args[0])
        self.assertEqual(state["code"], "int a;")
        self.assertEqual(state["environment"], "")
        self.assertEqual(state["tp_conditions"], "")

    async def test_first_call_loads_context_from_resolved_warning_link(self):
        class ContextClient(LocalClient):
            def __init__(self):
                self.actions = []

            async def call_tool(self, name, arguments):
                action = arguments["action"]
                self.actions.append(action)
                if action == "get_warning":
                    data = {"warning": {
                        "checker_name": "ZERO", "message": "Division by zero",
                        "snapshot_id": "PRIVATE_SNAPSHOT",
                        "warning_id": "PRIVATE_WARNING_ID",
                    }}
                elif action == "agent_checker_info":
                    data = {"documentation": {
                        "text": "TP if the divisor is zero",
                        "source_url": "PRIVATE_URL",
                    }}
                elif action == "get_warning_trace_and_role":
                    data = {"trace": []}
                else:
                    raise AssertionError(action)
                return CallToolResult(content=[TextContent(
                    type="text", text=json.dumps(data),
                )])

        client = ContextClient()
        model = FakeModel(
            answer(calls=[call("ask_system_one", {"code": "int n = 1 / 0;"})]),
            answer("TP based on the initial assessment"),
        )
        model.base, model.name = "http://main", "main"
        conversation = Conversation(client, model, parse_args([
            "--plain", "--system-one-model", "fast",
        ]), None, None)
        with patch.object(
            SystemOne, "classify", side_effect=lambda *_a, **_k: {
                "classification": "TP", "confidence": 0.95,
                "can_skip_investigation": True,
            },
        ) as classify:
            await conversation.initialize()
            conversation.harness.prefetch([
                ("get_warning", {"warning_id": "OLD"}, {"warning": {
                    "checker_name": "OLD_CHECKER", "message": "Old warning",
                }}),
                ("agent_checker_info", {"checker_name": "OLD_CHECKER"}, {
                    "documentation": {"text": "Old conditions"},
                }),
            ])
            await conversation.harness.ask("Review this warning", prefetched=[(
                URL_ACTION, {"url": "PRIVATE_HISTORY_URL"}, {
                    "read_action": "get_warning",
                    "read_arguments": {
                        "project_id": 1, "stream_id": "PRIVATE_BRANCH",
                        "snapshot_id": "PRIVATE_SNAPSHOT",
                        "warning_id": "PRIVATE_WARNING_ID",
                    },
                },
            )])
        self.assertEqual(client.actions, [
            "get_warning", "agent_checker_info", "get_warning_trace_and_role",
        ])
        self.assertEqual(len(model.requests), 2)
        state = json.loads(classify.call_args.args[0])
        self.assertEqual(
            state["warning_description"], "ZERO: Division by zero"
        )
        self.assertEqual(state["tp_conditions"], "TP if the divisor is zero")
        self.assertNotIn("PRIVATE", json.dumps(state))
        response = json.loads(model.requests[1]["messages"][-1]["content"])
        self.assertTrue(response["can_skip_investigation"])

    async def test_extra_arguments_are_not_sent(self):
        adviser = SystemOne("http://systemone", "fast", 1)
        adviser.available = True
        transport = SystemOneClient(LocalClient(), adviser)
        arguments = {"code": "int a;", "environment": "C"}
        with patch.object(adviser, "classify") as classify:
            transport.context = lambda: {
                "warning_description": "Host warning",
                "tp_conditions": "Host checker conditions",
            }
            result = await transport.call_tool("ask_system_one", {
                **arguments, "snapshot_id": "PRIVATE_SNAPSHOT",
            })
            self.assertIn("error", json.loads(result.content[0].text))
            classify.assert_not_called()
        self.assertTrue(adviser.available)

    def test_confidence_scales_boundaries_and_invalid_responses(self):
        adviser = SystemOne("http://systemone", "fast", 1)
        for score, normalized in (
            (0, 0), (0.79, 0.79), (0.8, 0.8), (1, 1),
            (79, 0.79), (80, 0.8), (100, 1),
            (-1, None), (101, None), (True, None),
            (float("nan"), None), (float("inf"), None), ("0.8", None),
        ):
            with self.subTest(score=score), patch(
                "history_agent.review.system_one._request"
            ), patch(
                "history_agent.review.system_one._records",
                return_value=iter([{"answers": {"sentiment": {
                    "choice": "FP", "confidence": score,
                }}}]),
            ):
                result = adviser.classify("warning")
                if normalized is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result["classification"], "FP")
                    self.assertEqual(result["confidence"], normalized)
                    self.assertEqual(
                        result["can_skip_investigation"], normalized >= 0.7
                    )
                    json.dumps(result, allow_nan=False)
