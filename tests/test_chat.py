import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.chat import ChatError, Harness, Model, model_base
from history_agent.interfaces.mcp import create_server
from history_agent.runtime.identities import IdentityMap
from tests.fake_history import FakeHistory
from tests.test_client import REVIEW, SOURCE


def answer(text="Read src/a.c:3: value = 42", calls=None):
    message = {"role": "assistant", "content": None if calls else text}
    if calls:
        message["tool_calls"] = calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    }


def call(name, arguments, id="call-1"):
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, messages, tools, on_event=None):
        self.requests.append(
            copy.deepcopy({"messages": messages, "tools": tools})
        )
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def named_reply(result, names):
    """Encode canned server addresses just as a model copies exposed names."""
    if isinstance(result, Exception):
        return result
    result = copy.deepcopy(result)
    for choice in result.get("choices", []):
        for entry in choice.get("message", {}).get("tool_calls", []):
            function = entry["function"]
            try:
                function["arguments"] = json.dumps(
                    names.encode(json.loads(function["arguments"]))
                )
            except ValueError:
                pass
    return result


def connected(test):
    @wraps(test)
    async def wrapped(self):
        async with Client(
            create_server(
                HistoryClient(Settings(self.fake.url, "tester", "test-secret"))
            )
        ) as client:
            self.client = client
            self.tools = (await client.list_tools()).tools
            await test(self)

    return wrapped


class ChatTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def harness(self, *responses, **kwargs):
        model = FakeModel(*responses)
        harness = Harness(self.client, model, self.tools, **kwargs)
        names = harness.model_context.names
        names.encode(SOURCE)
        names.encode(REVIEW)
        model.replies = [named_reply(result, names) for result in responses]
        return harness, model

    @connected
    async def test_schema_then_source_then_answer_with_small_stable_prefix(
        self,
    ):
        harness, model = self.harness(
            answer(
                calls=[call("history_discover", {"action": "agent_source"})]
            ),
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_source", "arguments": SOURCE},
                    )
                ]
            ),
            answer(),
            stats=True,
        )
        self.assertIn("42", await harness.ask("Read this source address"))
        self.assertEqual(len(model.requests), 3)
        self.assertLess(len(json.dumps(model.requests[0]).encode()), 4096)
        self.assertEqual(
            model.requests[0]["messages"], model.requests[1]["messages"][:2]
        )
        self.assertEqual(
            model.requests[0]["tools"], model.requests[2]["tools"]
        )
        self.assertNotIn("test-secret", json.dumps(model.requests))
        self.assertNotIn(
            "history_write", json.dumps(model.requests[0]["tools"])
        )
        self.assertIn("пример", model.requests[-1]["messages"][-1]["content"])
        self.assertIn("cached_tokens=100", self.output.getvalue())
        self.assertFalse(self.fake.writes)

    @connected
    async def test_disabled_write_rejects_entire_batch_before_any_tool_runs(
        self,
    ):
        harness, model = self.harness(
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_source", "arguments": SOURCE},
                    ),
                    call(
                        "history_write",
                        {"action": "agent_set_review", "arguments": REVIEW},
                        "call-2",
                    ),
                ]
            )
        )
        with self.assertRaisesRegex(ChatError, "disabled"):
            await harness.ask("Inspect")
        self.assertFalse(self.fake.requests)
        self.assertFalse(self.fake.writes)
        with self.assertRaisesRegex(ChatError, "/reset"):
            await harness.ask("Continue")
        self.assertEqual(len(model.requests), 1)

    @connected
    async def test_read_tool_cannot_be_used_as_a_write(self):
        harness, _ = self.harness(
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_set_review", "arguments": REVIEW},
                    )
                ]
            ),
            answer("Write rejected"),
        )
        self.assertEqual(await harness.ask("Inspect"), "Write rejected")
        self.assertFalse(self.fake.writes)
        self.assertFalse(self.fake.requests)
        self.assertIn("not allowed", harness.messages[-2]["content"])

    @connected
    async def test_explicit_write_is_saved_once_in_isolated_history(self):
        harness, model = self.harness(
            answer(
                calls=[
                    call(
                        "history_write",
                        {"action": "agent_set_review", "arguments": REVIEW},
                    )
                ]
            ),
            answer("Saved"),
            allow_write=True,
        )
        self.assertEqual(
            await harness.ask("Save TP with this evidence"), "Saved"
        )
        self.assertIn("history_write", json.dumps(model.requests[0]["tools"]))
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(self.fake.writes[0]["comment"], REVIEW["comment"])

    @connected
    async def test_ambiguous_write_reloads_with_tools_disabled_and_no_retry(
        self,
    ):
        self.fake.disconnect_on_write = True
        harness, model = self.harness(
            answer(
                calls=[
                    call(
                        "history_write",
                        {"action": "agent_set_review", "arguments": REVIEW},
                    )
                ]
            ),
            answer("Write outcome is uncertain; inspect history."),
            allow_write=True,
        )
        result = await harness.ask("Save")
        self.assertIn("Inspect warning/review history", result)
        self.assertEqual(len(self.fake.writes), 1)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(model.requests[-1]["tools"], [])
        self.assertTrue(harness.blocked)

    @connected
    async def test_context_budget_and_step_limit_prevent_more_work(self):
        harness, model = self.harness(answer(), max_context_bytes=1)
        with self.assertRaisesRegex(ChatError, "byte budget"):
            await harness.ask("Inspect")
        self.assertFalse(model.requests)
        harness, model = self.harness(
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_source", "arguments": SOURCE},
                    )
                ]
            ),
            max_steps=1,
        )
        with self.assertRaisesRegex(ChatError, "step limit"):
            await harness.ask("Inspect")
        self.assertEqual(len(model.requests), 1)
        self.assertFalse(self.fake.requests)

    @connected
    async def test_oversized_tool_result_is_omitted_with_explicit_error(self):
        harness, model = self.harness(
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_source", "arguments": SOURCE},
                    )
                ]
            ),
            answer("Need smaller window"),
            max_tool_result_bytes=64,
        )
        await harness.ask("Inspect")
        content = model.requests[-1]["messages"][-1]["content"]
        self.assertIn("omitted completely", content)
        self.assertNotIn("пример", content)

    @connected
    async def test_reset_restores_original_prefix_after_failure(self):
        harness, model = self.harness(
            ChatError("Connection failed"), answer("Next task")
        )
        original = copy.deepcopy(harness.messages)
        with self.assertRaises(ChatError):
            await harness.ask("First task")
        harness.reset()
        self.assertEqual(harness.messages, original)
        self.assertEqual(await harness.ask("Second task"), "Next task")
        self.assertNotIn("First task", json.dumps(model.requests[-1]))

    @connected
    async def test_bad_or_truncated_model_calls_execute_nothing(self):
        truncated = answer(
            calls=[
                call(
                    "history_read",
                    {"action": "agent_source", "arguments": SOURCE},
                )
            ]
        )
        truncated["choices"][0]["finish_reason"] = "length"
        bad = call("history_read", {})
        bad["function"]["arguments"] = "{broken"
        for response in (truncated, answer(calls=[bad]), {"choices": []}):
            with self.subTest(response=response):
                harness, _ = self.harness(response)
                with self.assertRaises(ChatError):
                    await harness.ask("Inspect")
                self.assertFalse(self.fake.requests)


class ModelHTTPTest(unittest.TestCase):
    def setUp(self):
        self.requests, self.status, self.response = (
            [],
            200,
            answer("CLI answer"),
        )
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                owner.requests.append((self.path, dict(self.headers), body))
                response = (
                    owner.response[len(owner.requests) - 1]
                    if isinstance(owner.response, list)
                    else owner.response
                )
                data = json.dumps(response).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                if owner.status == 302:
                    self.send_header("Location", owner.url + "/redirect")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_custom_endpoint_auth_options_and_model_errors(self):
        model = Model(
            model_base(self.url + "/tenant/v1/chat/completions"),
            "custom",
            "model-secret",
            no_thinking=True,
        )
        self.assertEqual(
            model.complete([], [])["choices"][0]["message"]["content"],
            "CLI answer",
        )
        path, headers, payload = self.requests[-1]
        self.assertEqual(path, "/tenant/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer model-secret")
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.status, self.response = 401, {"error": "model-secret echoed"}
        with self.assertRaises(ChatError) as caught:
            model.complete([], [])
        self.assertNotIn("model-secret", str(caught.exception))
        self.assertEqual(len(self.requests), 2)
        self.status = 302
        with self.assertRaises(ChatError):
            model.complete([], [])
        self.assertEqual(len(self.requests), 3)

    def test_one_shot_cli_uses_http_and_prints_stats(self):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "history_agent.interfaces.chat",
                "--model-url",
                self.url,
                "--history-url",
                self.url,
                "--model",
                "synthetic",
                "--prompt",
                "Hello",
                "--stats",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env={
                **os.environ,
                "HISTORY_CONFIG": "",
                "HISTORY_USER": "",
                "HISTORY_PASSWORD": "",
                "HISTORY_SESSION": "",
            },
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "CLI answer")
        self.assertIn("cached_tokens=100", process.stderr)
        self.assertEqual(len(self.requests), 1)

    def test_cli_model_failure_has_a_short_error_and_does_not_retry(self):
        self.status = 401
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "history_agent.interfaces.chat",
                "--model-url",
                self.url,
                "--model",
                "synthetic",
                "--prompt",
                "Hello",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "HISTORY_CONFIG": ""},
        )
        self.assertEqual(process.returncode, 1)
        self.assertIn("Model API returned HTTP 401", process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        self.assertEqual(len(self.requests), 1)

    def test_cli_http_model_tool_calls_reach_only_isolated_history(self):
        names = IdentityMap()
        source = names.encode(SOURCE)
        self.response = [
            answer(
                calls=[call("history_discover", {"action": "agent_source"})]
            ),
            answer(
                calls=[
                    call(
                        "history_read",
                        {"action": "agent_source", "arguments": source},
                    )
                ]
            ),
            answer("Verified synthetic source: value = 42"),
        ]
        with FakeHistory() as history:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "history_agent.interfaces.chat",
                    "--model-url",
                    self.url,
                    "--history-url",
                    history.url,
                    "--model",
                    "synthetic",
                    "--prompt",
                    "Inspect source " + json.dumps(SOURCE),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env={
                    **os.environ,
                    "HISTORY_CONFIG": "",
                    "HISTORY_USER": "tester",
                    "HISTORY_PASSWORD": "test-secret",
                    "HISTORY_SESSION": "",
                },
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("value = 42", process.stdout)
            self.assertEqual(history.requests[-1]["action"], "agent_source")
            self.assertFalse(history.writes)
        self.assertEqual(len(self.requests), 3)
        self.assertIn(
            "пример", self.requests[-1][2]["messages"][-1]["content"]
        )
        self.assertNotIn("test-secret", json.dumps(self.requests))

    def test_endpoint_normalization(self):
        self.assertEqual(
            model_base("https://model.example"), "https://model.example/v1"
        )
        self.assertEqual(
            model_base("https://model.example/tenant/v1/chat/completions"),
            "https://model.example/tenant/v1",
        )
        for url in (
            "https://m/v1/responses",
            "https://m/v1/messages",
            "https://user:secret@m",
        ):
            with self.assertRaises(ValueError):
                model_base(url)


if __name__ == "__main__":
    unittest.main()
