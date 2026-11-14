"""Exercise model management and clarifications in the actual terminal UI."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Collapsible, Input, Select

from history_agent.configuration.defaults import load_defaults
from history_agent.interfaces.arguments import parse_args
from history_agent.ui.app import HistoryApp
from history_agent.ui.config import ConfigurationApp
from history_agent.ui.config_capabilities import CapabilityEditor
from history_agent.ui.config_models import ModelEditor
from tests.test_model_catalog import CATALOG


class ModelSettingsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.json"
        environment = patch.dict(
            os.environ, {"HISTORY_AGENT_CONFIG": str(self.path)}
        )
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("probe_model_connection", "probe_system_one_connection"):
            probe = patch(
                "history_agent.ui.config." + name, return_value=(False, "test")
            )
            probe.start()
            self.addCleanup(probe.stop)

    async def test_picker_changes_protocol_endpoint_and_model(self):
        args = parse_args([], defaults={"providers": CATALOG})
        app = ConfigurationApp(args)
        async with app.run_test(size=(100, 60)) as pilot:
            screen = app.screen
            screen.query_one(
                "#model_profile", Select
            ).value = "messages/reviewer"
            await pilot.pause()
            self.assertEqual(
                screen.query_one("#provider", Select).value, "anthropic"
            )
            self.assertEqual(
                screen.query_one("#model", Input).value, "reviewer"
            )
            self.assertEqual(
                screen.query_one("#model_url", Input).value,
                "https://model.example/v1",
            )
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual(
                load_defaults()["model_profile"], "messages/reviewer"
            )
            self.assertEqual(app.return_value.provider, "anthropic")

    async def test_add_model_persists_and_does_not_mutate_original_args(self):
        args = parse_args([])
        app = ConfigurationApp(args)
        async with app.run_test(size=(100, 60)) as pilot:
            await pilot.click("#add-model")
            await pilot.pause()
            self.assertIsInstance(app.screen, ModelEditor)
            self.assertTrue(
                app.screen.query_one("#connection_api_key", Input).password
            )
            for name, value in (
                ("connection_name", "local"),
                ("model_id", "reviewer"),
                ("connection_url", "http://localhost:9000/v1"),
                ("connection_api_key", "sk-pasted-model-key"),
            ):
                app.screen.query_one("#" + name, Input).value = value
            await pilot.click("#apply-model")
            await pilot.pause()
            self.assertEqual(
                app.screen.query_one("#model", Input).value, "reviewer"
            )
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual(
                load_defaults()["model_profile"], "local/reviewer"
            )
            self.assertEqual(
                load_defaults()["providers"]["local"]["api_key"],
                "sk-pasted-model-key",
            )
            self.assertFalse(args.providers)

    async def test_model_and_search_keys_are_masked_and_saved_as_values(self):
        app = ConfigurationApp(parse_args([]))
        async with app.run_test(size=(100, 60)) as pilot:
            key = app.screen.query_one("#model_api_key", Input)
            self.assertTrue(key.password)
            key.value = "sk-ui-model-key"
            section = app.screen.query_one("#agent-settings", Collapsible)
            section.collapsed = False
            app.screen.query_one("#tool-settings").scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("#tool-settings")
            await pilot.pause()
            self.assertIsInstance(app.screen, CapabilityEditor)
            search = app.screen.query_one("#search-api-key", Input)
            self.assertTrue(search.password)
            search.value = "sk-ui-search-key"
            await pilot.click("#apply-policy")
            await pilot.pause()
            await pilot.click("#save")
            await pilot.pause()
            saved = load_defaults()
            self.assertEqual(saved["model_api_key"], "sk-ui-model-key")
            self.assertEqual(
                saved["capabilities"]["documentation"]["api_key"],
                "sk-ui-search-key",
            )

    async def test_question_enables_input_during_investigation(self):
        app = HistoryApp(parse_args([]))
        async with app.run_test(size=(100, 35)) as pilot:
            app.adapter.event("begin", text="Review warning")
            pending = asyncio.create_task(
                app.adapter.ask_user(
                    "Is this production code?",
                    ["Yes", "No"],
                )
            )
            await pilot.pause()
            self.assertTrue(app.waiting)
            self.assertFalse(app.query_one("#composer").disabled)
            self.assertIn("Is this production code?", app.cards[-1].text)
            app.submit("Yes")
            self.assertEqual(await pending, "Yes")
            self.assertTrue(app.query_one("#composer").disabled)
