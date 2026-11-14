"""Failure details persist locally with bounded size and private access."""

import json
import stat
import tempfile
import unittest

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.cli import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.runtime.conversation import Conversation
from history_agent.runtime.diagnostics import DiagnosticLog
from history_agent.ui.app import HistoryApp
from tests.fake_history import FakeHistory
from tests.test_chat import FakeModel, answer, call
from tests.test_session import ScriptedUI


class DiagnosticLogTest(unittest.TestCase):
    def test_private_json_lines_redact_configured_secrets_and_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            log = DiagnosticLog(root, secrets=("private-key",))
            log.event(
                "tool_result",
                arguments={"password": "another-secret"},
                response="private-key redis://user:pass@cache:6379/0",
            )
            log.event("reasoning", text="Skipped streaming chunk")
            text = log.path.read_text()
            self.assertNotIn("private-key", text)
            self.assertNotIn("another-secret", text)
            self.assertNotIn("user:pass", text)
            self.assertEqual(len(text.splitlines()), 1)
            self.assertEqual(json.loads(text)["event"], "tool_result")
            self.assertEqual(stat.S_IMODE(log.path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(log.path.parent.stat().st_mode), 0o700
            )

    def test_rotation_bounds_files_and_retains_latest_failure(self):
        with tempfile.TemporaryDirectory() as root:
            log = DiagnosticLog(root, max_bytes=600)
            for index in range(20):
                log.event(
                    "failure", message="failed " + str(index) + "x" * 200
                )
            files = list(log.path.parent.iterdir())
            self.assertLessEqual(len(files), 3)
            self.assertTrue(all(p.stat().st_size <= 600 for p in files))
            self.assertIn("failed 19", log.path.read_text())
            self.assertTrue(
                all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
            )


class ConversationLogTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_logging_preserves_home_until_user_message(self):
        with tempfile.TemporaryDirectory() as root:
            args = parse_args(["--batch-state-dir", root, "--read-only"])
            model = FakeModel(answer("Ready"))
            model.name, model.base = "synthetic", "http://synthetic/v1"
            history = HistoryClient(Settings("http://history.invalid"))
            async with Client(create_server(history)) as client:
                app = HistoryApp(args)
                async with app.run_test(size=(100, 30)) as pilot:
                    conversation = Conversation(
                        client, model, args, history, app.adapter,
                    )
                    await conversation.initialize()
                    app.adapter.welcome(conversation.harness.context_bytes())
                    app.adapter.event("input_ready")
                    await pilot.pause()
                    self.assertTrue(app.query_one("#body").has_class("home"))
                    self.assertTrue(app.query_one("#welcome").display)
                    self.assertFalse(app.query_one("#composer").disabled)
                    self.assertFalse(app.cards)
                    self.assertFalse(model.requests)
                    records = [json.loads(line) for line in
                               conversation.diagnostics.path.read_text()
                               .splitlines()]
                    self.assertEqual(records[0]["event"], "session_started")
                    await conversation.respond("Hello")
                    await pilot.pause()
                    self.assertFalse(app.query_one("#body").has_class("home"))
                    self.assertEqual(len(model.requests), 1)

    async def test_rejected_action_and_model_exchange_are_logged_without_http(
        self,
    ):
        with FakeHistory() as fake, tempfile.TemporaryDirectory() as root:
            history = HistoryClient(Settings(fake.url, session="valid"))
            model = FakeModel(
                answer(
                    calls=[
                        call(
                            "history_discover", {"action": "agent_search_code"}
                        )
                    ]
                ),
                answer("Use source navigation"),
            )
            model.name, model.base = "synthetic", "http://synthetic/v1"
            model.max_output_tokens = 256
            model.key = "private-provider-key"
            args = parse_args(["--batch-state-dir", root, "--read-only"])
            async with Client(create_server(history)) as client:
                ui = ScriptedUI([])
                conversation = Conversation(client, model, args, history, ui)
                await conversation.initialize()
                before = len(fake.requests)
                await conversation.respond("Inspect this warning")
                self.assertEqual(len(fake.requests), before)
                lines = conversation.diagnostics.path.read_text().splitlines()
                events = [
                    json.loads(line)
                    for line in lines
                ]
                self.assertEqual(
                    sum(e["event"] == "model_request" for e in events), 2
                )
                request = next(e for e in events
                               if e["event"] == "model_request")
                self.assertEqual(request["model"]["max_output_tokens"], 256)
                self.assertNotIn("private-provider-key", json.dumps(events))
                self.assertEqual(
                    sum(e["event"] == "model_response" for e in events), 2
                )
                error = next(
                    e
                    for e in events
                    if e["event"] == "tool_result" and e.get("error")
                )
                self.assertIn("agent_search_code", error["response"])
                self.assertIn("available_actions", error["response"])
                self.assertTrue(
                    any(
                        e["event"] == "notice"
                        and "Automatic reload" in e.get("message", "")
                        for e in events
                    )
                )
