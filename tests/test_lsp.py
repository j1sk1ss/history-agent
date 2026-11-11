"""LSP queries use scoped remote source and preserve source coordinates."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.types import CallToolResult, TextContent

from history_agent.configuration.capabilities import LanguageServerSettings
from history_agent.review.client import ItemClient, ReadScopeError
from history_agent.tools.code_context import (
    CodeContextRequest,
    CodeContextTools,
)


class SourceClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "lines": [
                                {
                                    "number": 25,
                                    "text": "int sample() { return 1; }",
                                }
                            ],
                        }
                    ),
                )
            ]
        )


class LSPTest(unittest.IsolatedAsyncioTestCase):
    def settings(self, language="c"):
        fixture = Path(__file__).parent / "fixtures/lsp_server.py"
        return LanguageServerSettings(
            enabled=True,
            commands={
                language: [sys.executable, str(fixture.resolve())],
            },
        )

    def request(self, **changes):
        return CodeContextRequest(
            **{
                "project_id": 1,
                "snapshot_id": "snapshot",
                "file": "src/test.c",
                "line": 25,
                **changes,
            }
        )

    async def test_stdio_symbols_keep_remote_line_and_uri(self):
        source = SourceClient()
        result = await CodeContextTools.execute(
            self.request(), source, self.settings()
        )
        self.assertTrue(result["partial_document"])
        location = result["result"][0]["location"]
        self.assertEqual(location["uri"], "src/test.c")
        self.assertEqual(location["range"]["start"]["line"], 24)
        self.assertEqual(source.calls[0][0], "history_read")
        self.assertEqual(source.calls[0][1]["action"], "agent_source")

    async def test_hover_converts_one_based_position(self):
        result = await CodeContextTools.execute(
            self.request(action="hover", column=5),
            SourceClient(),
            self.settings(),
        )
        self.assertEqual(
            result["result"]["position"], {"line": 24, "character": 4}
        )

    async def test_scoped_client_rejects_another_snapshot(self):
        source = SourceClient()
        scoped = ItemClient(
            source,
            {
                "project_id": 1,
                "stream_id": "main",
                "snapshot_id": "assigned",
            },
            set(),
        )
        with self.assertRaises(ReadScopeError):
            await CodeContextTools.execute(
                self.request(), scoped, self.settings()
            )
        self.assertFalse(source.calls)

    async def test_all_requested_languages_have_a_protocol_adapter(self):
        for language, suffix in (
            ("lua", "lua"),
            ("c", "c"),
            ("cpp", "cpp"),
            ("python", "py"),
            ("java", "java"),
            ("javascript", "js"),
        ):
            with self.subTest(language=language):
                result = await CodeContextTools.execute(
                    self.request(file="src/test." + suffix),
                    SourceClient(),
                    self.settings(language),
                )
                self.assertEqual(result["language"], language)

    async def test_missing_binary_does_not_fetch_source(self):
        source = SourceClient()
        with patch("shutil.which", return_value=None):
            result = await CodeContextTools.execute(
                self.request(), source, self.settings()
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(source.calls)
