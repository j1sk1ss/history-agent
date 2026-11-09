import copy
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.inference.model import ChatError
from history_agent.interfaces.cli import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.runtime.session import converse
from tests.fake_history import FakeHistory
from tests.test_batch import ReviewModel, warning
from tests.test_chat import FakeModel, answer


class ScriptedUI:
    async_prompt = True

    def __init__(self, commands):
        self.commands = iter(commands)
        self.events = []
        self.errors = []

    def event(self, kind, **data):
        self.events.append((kind, data))

    def welcome(self, context):
        pass

    def begin(self, text):
        pass

    def finish(self, result=None):
        pass

    def error_message(self, text):
        self.errors.append(text)

    async def prompt_async(self):
        return next(self.commands)

    async def execute(self, operation):
        return await operation


class ReloadTest(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, model, commands, options=(), server=None):
        with tempfile.TemporaryDirectory() as directory:
            args = parse_args(
                ["--batch-state-dir", str(Path(directory)), *options]
            )
            model.name, model.base = "synthetic", "http://synthetic/v1"
            ui = ScriptedUI(commands)
            with server or FakeHistory() as history_server:
                history = HistoryClient(
                    Settings(history_server.url, "tester", "test-secret")
                )
                async with Client(create_server(history)) as client:
                    await converse(
                        client, model, args, history, ui_override=ui
                    )
                return ui, copy.deepcopy(history_server.writes)

    async def test_reload_retries_startup_prompt_with_fresh_context(self):
        model = FakeModel(ChatError("Unavailable"), answer("Recovered"))
        ui, writes = await self.run_session(
            model,
            ["/reload", "/quit"],
            ["--prompt", "Review this warning"],
        )
        self.assertEqual(ui.errors, ["Unavailable"])
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(
            model.requests[0]["messages"], model.requests[1]["messages"]
        )
        self.assertFalse(writes)

    async def test_reload_without_request_does_not_call_model(self):
        model = FakeModel()
        ui, _ = await self.run_session(model, ["/reload", "/quit"])
        self.assertEqual(ui.errors, ["No request to reload yet"])
        self.assertFalse(model.requests)

    async def test_reload_resumes_checkpoint_without_repeating_saved_reviews(
        self,
    ):
        server = FakeHistory()
        server.warnings = [warning("a1"), warning("a2")]
        model = ReviewModel()
        complete = model.complete

        def fail_second_request(messages, tools, on_event=None):
            if len(model.requests) == 1:
                model.requests.append({"failed": True})
                raise ChatError("Temporary model failure")
            return complete(messages, tools, on_event)

        model.complete = fail_second_request
        ui, writes = await self.run_session(
            model,
            ["/reload", "/quit"],
            ["--batch", "0", "master", "snapshot"],
            server,
        )
        self.assertEqual(len(ui.errors), 1)
        self.assertIn("Temporary model failure", ui.errors[0])
        self.assertEqual(len(writes), 2)
        started = [
            data["job_id"]
            for kind, data in ui.events
            if kind == "batch_started"
        ]
        self.assertEqual(len(started), 2)
        self.assertEqual(started[0], started[1])
