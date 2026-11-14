"""Direct credentials survive settings, profile selection and HTTP requests."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from history_agent.configuration.capabilities import DocumentationSettings
from history_agent.configuration.connection import discover_model
from history_agent.configuration.credentials import model_api_key
from history_agent.configuration.defaults import load_defaults, save_defaults
from history_agent.interfaces.arguments import parse_args
from history_agent.interfaces.cli import run
from history_agent.tools.documentation import (
    DocumentationRequest,
    DocumentationTools,
)
from history_agent.tools.documentation_http import DocumentationHTTP
from tests.fake_history import FakeHistory
from tests.test_api_keys import ANTHROPIC, COMPLETION, GEMINI, HeaderServer


class DirectKeyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "config.json"
        environment = patch.dict(
            os.environ,
            {
                "HISTORY_AGENT_CONFIG": str(self.path),
                "HISTORY_AGENT_MODEL_API_KEY": "ambient-key",
                "OLD_MODEL_KEY": "legacy-model-key",
                "OLD_SEARCH_KEY": "legacy-search-key",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_flat_key_is_saved_privately_and_empty_key_disables_fallback(self):
        args = parse_args(["--model-api-key", "sk-pasted-key"])
        save_defaults(args)
        restored = parse_args([], defaults=load_defaults())
        self.assertEqual(model_api_key(restored), "sk-pasted-key")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("model_key_env", load_defaults())
        args = parse_args(["--model-api-key", ""])
        save_defaults(args)
        self.assertIsNone(
            model_api_key(parse_args([], defaults=load_defaults()))
        )

    def test_legacy_settings_migrate_to_direct_keys_when_explicitly_saved(
        self,
    ):
        args = parse_args(
            [],
            defaults={
                "model_key_env": "OLD_MODEL_KEY",
                "providers": {
                "saved": {
                    "base_url": "https://model.example/v1",
                    "api_key_env": "OLD_MODEL_KEY",
                    "models": {"reviewer": {}},
                },
                "implicit": {
                    "base_url": "https://local.example/v1",
                    "models": {"local": {}},
                },
                },
                "capabilities": {
                    "documentation": {"api_key_env": "OLD_SEARCH_KEY"}
                },
            },
        )
        self.assertEqual(model_api_key(args), "legacy-model-key")
        save_defaults(args)
        saved = load_defaults()
        self.assertEqual(saved["model_api_key"], "legacy-model-key")
        self.assertEqual(
            saved["providers"]["saved"]["api_key"], "legacy-model-key"
        )
        self.assertEqual(
            saved["providers"]["implicit"]["api_key"], "ambient-key"
        )
        self.assertEqual(
            saved["capabilities"]["documentation"]["api_key"],
            "legacy-search-key",
        )
        self.assertNotIn("key_env", self.path.read_text())

    def test_model_discovery_uses_the_supplied_key(self):
        with patch(
            "history_agent.configuration.connection.urllib.request.build_opener"
        ) as build:
            response = (
                build.return_value.open.return_value.__enter__.return_value
            )
            response.read.return_value = b'{"data":[{"id":"reviewer"}]}'
            self.assertEqual(
                discover_model(
                    "https://model.example/v1", api_key="sk-direct"
                ),
                "reviewer",
            )
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer sk-direct"
        )

    def test_search_uses_direct_key_and_empty_key_stops_environment_fallback(
        self,
    ):
        request = DocumentationRequest(library="numpy", symbol="array")
        with patch.object(
            DocumentationHTTP, "search", return_value=[]
        ) as search:
            DocumentationTools.execute(
                request,
                DocumentationSettings(
                    api_key="sk-search-direct",
                    api_key_env="OLD_SEARCH_KEY",
                ),
            )
            self.assertEqual(search.call_args.args[1], "sk-search-direct")
            search.reset_mock()
            result = DocumentationTools.execute(
                request,
                DocumentationSettings(
                    api_key="",
                    api_key_env="OLD_SEARCH_KEY",
                ),
            )
            self.assertEqual(result["status"], "unavailable")
            search.assert_not_called()


class DirectKeyHTTPTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_direct_keys_reach_each_provider_protocol(self):
        for provider, chunks, header, prefix in (
            ("litellm", COMPLETION, "authorization", "Bearer "),
            ("anthropic", ANTHROPIC, "x-api-key", ""),
            ("gemini", GEMINI, "x-goog-api-key", ""),
        ):
            with self.subTest(provider=provider), FakeHistory() as history:
                server = HeaderServer(chunks)
                try:
                    args = parse_args(
                        ["--plain", "--prompt", "ping"],
                        defaults={
                            "model_profile": "saved/reviewer",
                            "providers": {
                                "saved": {
                                    "transport": provider,
                                    "base_url": server.url,
                                    "api_key": "sk-saved-direct-key",
                                    "models": {"reviewer": {}},
                                }
                            },
                            "history_url": history.url,
                        },
                    )
                    with redirect_stdout(io.StringIO()):
                        await run(args)
                    self.assertEqual(
                        server.headers_seen[0][header],
                        prefix + "sk-saved-direct-key",
                    )
                finally:
                    server.stop()
