"""The live server uses choose for enums rather than a JSON Schema type."""

import copy
import unittest

from jsonschema import Draft202012Validator

from history_agent.history.catalog import extension_action
from history_agent.history.settings import HistoryError
from tests.fake_history import EXTENSION


class SchemaDialectTest(unittest.TestCase):
    def test_live_enum_fields_become_valid_schemas_without_mutating_metadata(
        self,
    ):
        for action, field in (
            ("agent_compare_warnings", "subset"),
            ("agent_find_warnings", "match"),
            ("agent_resolve_symbol", "relation"),
            ("agent_set_review", "classification"),
        ):
            with self.subTest(action=action):
                metadata = copy.deepcopy(EXTENSION[action])
                original = copy.deepcopy(metadata)
                info = extension_action(action, metadata)
                Draft202012Validator.check_schema(info.input_schema)
                prop = info.input_schema["properties"][field]
                expected = original["input_schema"]["properties"][field]
                self.assertEqual(prop, {**expected, "type": "string"})
                self.assertEqual(metadata, original)

    def test_malformed_choices_and_unknown_types_are_rejected(self):
        for prop in (
            {"type": "choose"},
            {"type": "choose", "enum": []},
            {"type": "choose", "enum": ["exact", 1]},
            {"type": "choose", "enum": "exact"},
            {"type": "unknown", "enum": ["exact"]},
        ):
            with self.subTest(prop=prop):
                metadata = copy.deepcopy(EXTENSION["agent_find_warnings"])
                metadata["input_schema"]["properties"]["match"] = prop
                with self.assertRaisesRegex(HistoryError, "invalid extension"):
                    extension_action("agent_find_warnings", metadata)
