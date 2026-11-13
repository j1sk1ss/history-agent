"""LiteLLM proxy integration without upstream credentials or live services."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from history_agent.configuration.defaults import validate_defaults
from history_agent.configuration.models import ModelCatalog
from history_agent.configuration.probes import probe_model_connection
from history_agent.inference.model import ChatError, Model
from history_agent.interfaces.arguments import parse_args
from history_agent.interfaces.cli import run
from tests.fake_history import FakeHistory
from tests.test_api_keys import COMPLETION, HeaderServer
from tests.test_streaming import (
    StreamingServer,
    delta,
    finish,
    tool_chunks,
)


class LiteLLMTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 5
        temporary = tempfile.TemporaryDirectory(prefix="history-litellm-")
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {
            "HISTORY_AGENT_CONFIG": str(Path(temporary.name) / "config.json"),
            "HISTORY_AGENT_MODEL_API_KEY": "unrelated-local-key",
            "OPENAI_API_KEY": "upstream-openai-key",
            "ANTHROPIC_API_KEY": "upstream-anthropic-key",
            "GEMINI_API_KEY": "upstream-gemini-key",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in (
            "LITELLM_API_KEY", "PROXY_TOKEN", "HISTORY_URL", "HISTORY_USER",
            "HISTORY_PASSWORD", "HISTORY_SESSION",
        ):
            os.environ.pop(name, None)

    async def test_cli_normalizes_proxy_urls_and_uses_only_proxy_keys(self):
        cases = (
            ("root", {"LITELLM_API_KEY": "proxy-default"}, [],
             "Bearer proxy-default"),
            ("base", {}, [], None),
            ("completion", {"LITELLM_API_KEY": "ignored-default",
                            "PROXY_TOKEN": "proxy-custom"},
             ["--model-key-env", "PROXY_TOKEN"], "Bearer proxy-custom"),
        )
        with FakeHistory() as history:
            for form, environment, options, authorization in cases:
                with self.subTest(form=form), patch.dict(
                    os.environ, environment,
                ):
                    server = HeaderServer(COMPLETION)
                    try:
                        url = {
                            "root": server.url.removesuffix("/v1"),
                            "base": server.url,
                            "completion": server.url + "/chat/completions",
                        }[form]
                        args = parse_args([
                            "--provider", "litellm", "--model-url", url,
                            "--model", "team/reviewer", "--history-url",
                            history.url, "--plain", "--prompt", "ping",
                            *options,
                        ])
                        await run(args)
                    finally:
                        server.stop()
                    self.assertEqual(server.last_path, "/v1/chat/completions")
                    self.assertEqual(len(server.headers_seen), 1)
                    headers = server.headers_seen[0]
                    self.assertEqual(headers.get("authorization"),
                                     authorization)
                    self.assertNotIn("x-api-key", headers)
                    self.assertNotIn("x-goog-api-key", headers)

    def test_streaming_tool_roundtrip_preserves_proxy_model_alias(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "history_current",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        messages = [{"role": "user", "content": "Inspect current project"}]
        responses = [
            [delta(reasoning_content="Select the current project"),
             *tool_chunks("history_current", {})],
            [delta(content="Ready"), finish(), "[DONE]"],
        ]
        events = []
        with StreamingServer(responses) as server:
            model = Model(server.url + "/v1", "team/reviewer",
                          provider="litellm")
            response = model.complete(
                messages, tools,
                lambda kind, **data: events.append((kind, data)),
            )
            message = response["choices"][0]["message"]
            call = message["tool_calls"][0]
            self.assertEqual(call["function"]["name"], "history_current")
            self.assertEqual(json.loads(call["function"]["arguments"]), {})
            self.assertEqual(message["reasoning_content"],
                             "Select the current project")
            self.assertEqual(response["usage"]["completion_tokens"], 24)
            messages.extend([
                message,
                {"role": "tool", "tool_call_id": call["id"], "content": "{}"},
            ])
            final = model.complete(messages, tools)
        self.assertEqual(final["choices"][0]["message"]["content"], "Ready")
        self.assertIn("reasoning", [kind for kind, _ in events])
        for request in server.requests:
            self.assertEqual(request["model"], "team/reviewer")
            self.assertEqual(request["tools"], tools)
            self.assertTrue(request["stream"])
            self.assertEqual(
                request["stream_options"], {"include_usage": True},
            )
            self.assertNotIn("chat_template_kwargs", request)
        self.assertEqual(server.requests[1]["messages"][-1]["tool_call_id"],
                         call["id"])

    def test_probe_uses_default_endpoint_and_optional_proxy_key(self):
        cases = (
            ({}, [], None),
            ({"LITELLM_API_KEY": "proxy-default"}, [], "proxy-default"),
            ({"LITELLM_API_KEY": "ignored", "PROXY_TOKEN": "proxy-custom"},
             ["--model-key-env", "PROXY_TOKEN"], "proxy-custom"),
        )
        for environment, options, key in cases:
            with (
                self.subTest(key=key),
                patch.dict(os.environ, environment),
                patch.object(Model, "complete", autospec=True,
                             return_value={"choices": [{}]}) as complete,
            ):
                connected, _ = probe_model_connection(parse_args([
                    "--provider", "litellm", "--model", "team/reviewer",
                    *options,
                ]))
            self.assertTrue(connected)
            model = complete.call_args.args[0]
            self.assertEqual(model.base, "http://127.0.0.1:4000/v1")
            self.assertEqual(model.provider, "litellm")
            self.assertEqual(model.name, "team/reviewer")
            self.assertEqual(model.key, key)
            self.assertFalse(model.stream)
            self.assertLessEqual(model.timeout, 5)

    async def test_alias_is_required_without_local_model_discovery(self):
        args = parse_args(["--provider", "litellm"])
        with (
            patch("history_agent.interfaces.cli.discover_model") as cli,
            patch(
                "history_agent.configuration.probes.discover_model",
            ) as probe,
        ):
            self.assertFalse(probe_model_connection(args)[0])
            with self.assertRaisesRegex(ChatError, "Specify --model"):
                await run(args)
        cli.assert_not_called()
        probe.assert_not_called()

    async def test_profile_selection_clears_local_thinking_setting(self):
        providers = {"proxy": {
            "transport": "litellm",
            "base_url": "http://127.0.0.1:4000/v1",
            "models": {"team/reviewer": {"max_output_tokens": 4096}},
        }}
        ModelCatalog.validate(providers)
        args = parse_args([], defaults={
            "provider": "llama", "no_thinking": True, "providers": providers,
            "model_profile": "proxy/team/reviewer",
        })
        self.assertEqual(args.provider, "litellm")
        self.assertEqual(args.model, "team/reviewer")
        self.assertEqual(args.max_output_tokens, 4096)
        self.assertIsNone(args.model_key_env)
        self.assertFalse(args.no_thinking)
        with self.assertRaisesRegex(ValueError, "Disable thinking"):
            validate_defaults({"provider": "litellm", "no_thinking": True})
        args = parse_args([
            "--provider", "litellm", "--model", "team/reviewer",
            "--no-thinking",
        ])
        with self.assertRaisesRegex(ChatError, "--no-thinking"):
            await run(args)
