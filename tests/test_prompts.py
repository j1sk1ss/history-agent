"""Prompt overrides must reach live contexts and preserve required fields."""

import json
import tempfile
import unittest
from pathlib import Path

from history_agent.prompts import Prompts
from history_agent.review.system_one import SystemOneTools
from history_agent.runtime.agent import Harness
from tests.test_chat import FakeModel
from tests.test_system_one import TOOLS, LocalClient


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(Prompts.configure)
        self.path = Path(self.directory.name) / "prompts.json"

    def configure(self, values):
        self.path.write_text(json.dumps(values))
        Prompts.configure(str(self.path))

    def test_override_reaches_new_and_reset_harness(self):
        harness = Harness(LocalClient(), FakeModel(), TOOLS)
        self.configure({"runtime.system": "Custom instructions"})
        harness.reset()
        self.assertIn("Custom instructions", harness.messages[0]["content"])
        self.assertNotIn("Review Svace", harness.messages[0]["content"])

    def test_criteria_are_composed_dynamically(self):
        self.configure({"review.criteria": "New review criteria"})
        self.assertIn(
            "New review criteria", Prompts.get("review.batch_instruction")
        )

    def test_adviser_schema_uses_current_overrides(self):
        self.configure({"tools.system_one": "Custom adviser instructions"})
        self.assertEqual(
            SystemOneTools.definition().description,
            "Custom adviser instructions",
        )

    def test_invalid_overrides_are_rejected(self):
        for values in (
            {"typo": "bad"},
            {"runtime.system": ""},
            {"review.investigation": "Missing scope"},
            {"runtime.system": "{$runtime.system}"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.configure(values)
