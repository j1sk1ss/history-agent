"""Connection verification: local models without keys and API-keyed models."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from history_agent.configuration.defaults import LOCAL_PROVIDERS
from history_agent.inference.model import ChatError, Model
from history_agent.interfaces.arguments import parse_args
from history_agent.interfaces.cli import run
from tests.fake_history import FakeHistory

COMPLETION = [
    {
        "choices": [
            {"index": 0, "delta": {"content": "ok"}, "finish_reason": None},
        ],
    },
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}},
    "[DONE]",
]
ANTHROPIC = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "ok"},
    },
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 1},
    },
    {"type": "message_stop"},
]
CODEX = [
    {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        },
    },
    {
        "type": "response.completed",
        "response": {"usage": {"input_tokens": 3, "output_tokens": 2}},
    },
]
GEMINI = [
    {
        "candidates": [
            {
                "content": {"parts": [{"text": "ok"}]},
                "finishReason": "STOP",
            },
        ],
        "usageMetadata": {
            "promptTokenCount": 3,
            "candidatesTokenCount": 2,
        },
    },
]


class HeaderServer:
    """Chat endpoint that records request headers for assertions."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.headers_seen = []
        self.last_path = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.headers_seen.append({
                    key.lower(): value
                    for key, value in self.headers.items()
                })
                owner.last_path = self.path
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b""
                for chunk in owner.chunks:
                    data = (
                        chunk
                        if isinstance(chunk, str)
                        else json.dumps(chunk)
                    )
                    body += ("data: " + data + "\r\n\r\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.owner = self
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True,
        )
        self.thread.start()
        self.url = "http://127.0.0.1:%d/v1" % self.httpd.server_address[1]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LocalModelConnectionTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HISTORY_AGENT_MODEL_API_KEY", None)

    def complete_local(self, provider):
        server = HeaderServer(COMPLETION)
        try:
            model = Model(server.url, "qwen-local", None, 10,
                          provider=provider)
            result = model.complete([{"role": "user", "content": "ping"}],
                                    [])
        finally:
            server.stop()
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(len(server.headers_seen), 1)
        self.assertNotIn("authorization", server.headers_seen[0])
        return server

    def test_local_providers_connect_without_an_api_key(self):
        for provider in LOCAL_PROVIDERS:
            with self.subTest(provider=provider):
                self.complete_local(provider)

    def test_compatible_local_connection_sends_the_configured_key(self):
        server = HeaderServer(COMPLETION)
        try:
            model = Model(server.url, "qwen-local", "gateway-key", 10,
                          provider="openai-compatible")
            model.complete([{"role": "user", "content": "ping"}], [])
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("authorization"),
            "Bearer gateway-key",
        )


class ApiKeyModelConnectionTest(unittest.TestCase):
    def test_anthropic_sends_x_api_key(self):
        server = HeaderServer(ANTHROPIC)
        try:
            model = Model(server.url, "claude-test", "anth-secret", 10,
                          provider="anthropic")
            model.complete([{"role": "user", "content": "ping"}], [])
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("x-api-key"), "anth-secret",
        )
        self.assertEqual(server.last_path, "/v1/messages")

    def test_codex_sends_bearer_key(self):
        server = HeaderServer(CODEX)
        try:
            model = Model(server.url, "codex-test", "oai-secret", 10,
                          provider="codex")
            result = model.complete(
                [{"role": "user", "content": "ping"}], [],
            )
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("authorization"),
            "Bearer oai-secret",
        )
        self.assertEqual(
            result["choices"][0]["message"]["content"], "ok",
        )

    def test_gemini_sends_x_goog_api_key(self):
        server = HeaderServer(GEMINI)
        try:
            model = Model(
                server.url.rsplit("/v1", 1)[0] + "/v1beta",
                "gemini-test",
                "gemini-secret",
                10,
                provider="gemini",
            )
            result = model.complete(
                [{"role": "user", "content": "ping"}], [],
            )
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("x-goog-api-key"),
            "gemini-secret",
        )
        self.assertEqual(
            result["choices"][0]["message"]["content"], "ok",
        )


class ConfigConnectionTest(unittest.IsolatedAsyncioTestCase):
    """Saved settings must reach both local and API-key model modes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="history-config-conn-",
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = FakeHistory().__enter__()
        self.addCleanup(self.history.__exit__)
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in (
            "HISTORY_AGENT_MODEL_API_KEY",
            "ANTHROPIC_API_KEY",
            "HISTORY_URL",
            "HISTORY_USER",
            "HISTORY_PASSWORD",
            "HISTORY_SESSION",
        ):
            os.environ.pop(name, None)
        os.environ["HISTORY_AGENT_CONFIG"] = str(self.root / "config.json")

    async def chat(self, defaults, argv):
        args = parse_args(argv, defaults=defaults)
        return await run(args)

    def flat_local(self):
        # Resembles a dirty manually edited file: nulls and sentinel limits.
        return {
            "provider": "llama",
            "model_url": None,
            "model": "qwen-local",
            "model_key_env": None,
            "history_url": self.history.url,
            "history_user": None,
            "history_password": None,
            "allow_write": True,
            "max_steps": 9999999,
            "max_output_tokens": 9999999,
            "max_context_bytes": 9999999,
            "max_tool_result_bytes": 9999999,
            "timeout": 60.0,
        }

    def profile_config(self, base_url, transport, model, key_env):
        connection = {
            "transport": transport,
            "base_url": base_url,
            "models": {model: {}},
        }
        if key_env:
            connection["api_key_env"] = key_env
        return {
            "model_profile": "saved/" + model,
            "providers": {"saved": connection},
            "history_url": self.history.url,
        }

    async def test_flat_local_config_without_a_key_env_connects_keyless(
        self,
    ):
        defaults = self.flat_local()
        server = HeaderServer(COMPLETION)
        try:
            defaults["model_url"] = server.url
            await self.chat(defaults, ["--plain", "--prompt", "ping"])
        finally:
            server.stop()
        self.assertEqual(len(server.headers_seen), 1)
        self.assertNotIn("authorization", server.headers_seen[0])

    async def test_flat_local_config_uses_the_default_key_env(self):
        defaults = self.flat_local()
        server = HeaderServer(COMPLETION)
        try:
            defaults["model_url"] = server.url
            os.environ["HISTORY_AGENT_MODEL_API_KEY"] = "local-gateway"
            await self.chat(defaults, ["--plain", "--prompt", "ping"])
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("authorization"),
            "Bearer local-gateway",
        )

    async def test_profile_local_model_without_a_key_connects_keyless(self):
        server = HeaderServer(COMPLETION)
        try:
            defaults = self.profile_config(
                server.url, "openai-compatible", "qwen-local", None,
            )
            await self.chat(defaults, ["--plain", "--prompt", "ping"])
        finally:
            server.stop()
        self.assertNotIn("authorization", server.headers_seen[0])

    async def test_profile_remote_model_requires_its_api_key(self):
        server = HeaderServer(ANTHROPIC)
        try:
            defaults = self.profile_config(
                server.url, "anthropic", "claude-review", "ANTHROPIC_API_KEY",
            )
            with self.assertRaisesRegex(
                ChatError,
                "anthropic requires an API key in ANTHROPIC_API_KEY",
            ):
                await self.chat(
                    defaults, ["--plain", "--prompt", "ping"],
                )
        finally:
            server.stop()
        self.assertEqual(server.headers_seen, [])

    async def test_profile_remote_model_sends_its_api_key(self):
        server = HeaderServer(ANTHROPIC)
        try:
            defaults = self.profile_config(
                server.url, "anthropic", "claude-review", "ANTHROPIC_API_KEY",
            )
            os.environ["ANTHROPIC_API_KEY"] = "profile-key"
            await self.chat(defaults, ["--plain", "--prompt", "ping"])
        finally:
            server.stop()
        self.assertEqual(
            server.headers_seen[0].get("x-api-key"), "profile-key",
        )


if __name__ == "__main__":
    unittest.main()
