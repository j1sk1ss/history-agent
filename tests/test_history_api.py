"""New server catalog contracts and native action transport."""

import copy
import unittest
from unittest.mock import patch

from history_agent.history.client import HistoryClient, HistoryError, Settings
from tests.fake_history import FakeHistory
from tests.test_client import REVIEW, SOURCE

SCOPE = {"project_id": 0, "stream_id": "main", "snapshot_id": "current"}


class HistoryAPITest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.fake.warnings = [
            {
                "warning_id": "warning",
                "checker_name": "NULL",
                "location": {"file_path": "src/a.c", "line": 3},
            }
        ]
        self.addCleanup(self.fake.__exit__)
        self.client = HistoryClient(Settings(self.fake.url, session="valid"))

    def test_new_machine_catalog_exposes_schemas_constraints_and_session(self):
        metadata = self.client.discover("agent_source")
        self.assertEqual(
            metadata["constraints"],
            {
                "before": (
                    "Lines before the address; total window must be at most "
                    "200 lines"
                ),
                "after": (
                    "Lines after the address; total window must be at most "
                    "200 lines"
                ),
            },
        )
        self.assertTrue(metadata["session_required"])
        metadata["constraints"].clear()
        self.assertTrue(self.client.discover("agent_source")["constraints"])
        self.client.execute("agent_source", SOURCE)
        actions = [request["action"] for request in self.fake.requests]
        self.assertEqual(
            actions,
            [
                "get_actions",
                "actions_agent",
                "agent_source",
            ],
        )
        exposed = {a["name"] for a in self.client.discover()["actions"]}
        self.assertNotIn("actions_agent", exposed)
        self.assertNotIn("actions_userfriendly", exposed)

    def test_new_catalog_is_preferred_when_both_names_are_advertised(self):
        self.fake.legacy["agent_actions"] = {"params": {}}
        self.client.discover()
        self.assertEqual(
            [r["action"] for r in self.fake.requests],
            ["get_actions", "actions_agent"],
        )

    def test_old_schema_catalog_still_supports_reads_and_writes(self):
        self.fake.legacy["agent_actions"] = self.fake.legacy.pop(
            "actions_agent"
        )
        self.fake.legacy.pop("actions_userfriendly")
        self.client.execute("agent_source", SOURCE)
        self.client.execute("agent_set_review", REVIEW, write=True)
        self.assertEqual(self.fake.requests[1]["action"], "agent_actions")
        self.assertEqual(
            self.fake.writes[0]["comment"],
            "Bismark said: " + REVIEW["comment"],
        )
        self.assertNotIn(
            "actions_agent", [r["action"] for r in self.fake.requests]
        )

    def test_human_catalog_cannot_substitute_for_machine_schemas(self):
        self.fake.legacy.pop("actions_agent")
        exposed = {a["name"] for a in self.client.discover()["actions"]}
        self.assertIn("get_projects", exposed)
        self.assertNotIn("agent_source", exposed)
        with self.assertRaisesRegex(HistoryError, "unavailable"):
            self.client.execute("agent_source", SOURCE)
        self.assertEqual(len(self.fake.requests), 1)

    def test_invalid_catalogs_cannot_fall_back_to_legacy_schemas(self):
        original = self.client._call
        for invalid in ({}, {"data": []}, {"data": {"actions": []}}):
            with self.subTest(response=invalid):
                client = HistoryClient(
                    Settings(self.fake.url, session="valid")
                )
                with patch.object(
                    client,
                    "_call",
                    side_effect=lambda action: (
                        original(action)
                        if action == "get_actions"
                        else invalid
                    ),
                ):
                    with self.assertRaisesRegex(
                        HistoryError, "invalid catalog"
                    ):
                        client.discover()
        self.assertFalse(self.fake.writes)

    def test_invalid_action_metadata_is_reported_as_a_history_error(self):
        for invalid in (
            None,
            {},
            {"input_schema": True},
            {
                "description": "Invalid source",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "line": {
                            "type": "not-a-json-schema-type",
                        }
                    },
                },
            },
        ):
            with self.subTest(metadata=invalid):
                self.fake.extension["agent_source"] = invalid
                client = HistoryClient(
                    Settings(self.fake.url, session="valid")
                )
                with self.assertRaisesRegex(HistoryError, "invalid extension"):
                    client.execute("agent_source", SOURCE)
        self.assertNotIn(
            "agent_source", [r["action"] for r in self.fake.requests]
        )

    def test_server_source_bounds_are_enforced_before_http(self):
        self.client.discover()
        before = len(self.fake.requests)
        for override in (
            {"project_id": 9223372036854775808},
            {"line": 2147483648},
            {"before": 200},
            {"after": -1},
        ):
            with self.subTest(override=override):
                with self.assertRaises(HistoryError):
                    self.client.execute("agent_source", {**SOURCE, **override})
        self.assertEqual(len(self.fake.requests), before)

    def test_symbol_navigation_preserves_one_based_address_and_defaults(self):
        result = self.client.execute(
            "agent_resolve_symbol",
            {
                **SOURCE,
                "column": 1,
                "relation": "definition",
            },
        )
        self.assertEqual(result["matches"], [])
        request = self.fake.requests[-1]
        self.assertEqual(request["query"]["column"], "1")
        self.assertEqual(request["query"]["relation"], "definition")
        self.assertEqual(request["query"]["offset"], "0")
        self.assertEqual(request["query"]["limit"], "20")
        self.assertEqual(request["body"], {})
        for override in ({"column": 0}, {"relation": "unknown"}):
            with self.assertRaises(HistoryError):
                self.client.execute(
                    "agent_resolve_symbol",
                    {
                        **SOURCE,
                        "column": 1,
                        "relation": "usage",
                        **override,
                    },
                )

    def test_native_comparison_accepts_head_and_server_defaults(self):
        result = self.client.execute(
            "agent_compare_warnings",
            {
                **SCOPE,
                "snapshot_id": "HEAD",
                "base_snapshot_id": "HEAD",
            },
        )
        self.assertEqual(result["current"]["snapshot_id"], "snapshot-first")
        self.assertEqual(result["base"], result["current"])
        self.assertEqual(result["scope"], result["current"])
        self.assertEqual(result["subset"], "appeared")
        request = self.fake.requests[-1]
        self.assertEqual(request["action"], "agent_compare_warnings")
        self.assertEqual(request["query"]["snapshot_id"], "HEAD")
        self.assertEqual(request["query"]["base_snapshot_id"], "HEAD")
        self.assertNotIn("base_project_id", request["query"])
        self.assertNotIn("base_stream_id", request["query"])
        self.assertEqual(request["query"]["offset"], "0")
        self.assertEqual(request["query"]["limit"], "20")
        self.assertEqual(request["body"], {})

    def test_comparison_preserves_baseline_scope_matches_and_pagination(self):
        entries = [
            {
                "warning_id": str(i),
                "checker_name": "NULL",
                "counterparts": [
                    {
                        "kind": "RENAME",
                        "warning": {
                            "warning_id": "base-" + str(i),
                        },
                    }
                ],
            }
            for i in range(3)
        ]
        for subset in ("appeared", "disappeared", "both"):
            with self.subTest(subset=subset):
                self.fake.comparison_warnings[subset] = copy.deepcopy(entries)
                result = self.client.execute(
                    "agent_compare_warnings",
                    {
                        **SCOPE,
                        "base_project_id": 7,
                        "base_stream_id": "release",
                        "base_snapshot_id": "base",
                        "subset": subset,
                        "checker_name": "null",
                        "offset": 1,
                        "limit": 1,
                    },
                )
                expected_scope = (
                    {
                        "project_id": 7,
                        "stream_id": "release",
                        "snapshot_id": "base",
                    }
                    if subset == "disappeared"
                    else SCOPE
                )
                self.assertEqual(result["scope"], expected_scope)
                self.assertEqual(result["warnings"], [entries[1]])
                self.assertEqual(result["total"], 3)
                self.assertTrue(result["has_more"])
                self.assertEqual(result["next_offset"], 2)

    def test_legacy_comparison_works_without_native_compare_schema(self):
        self.fake.extension.pop("agent_compare_warnings")
        self.fake.legacy["get_warnings_compare_svace2svace"] = {"params": {}}
        original = self.client._call
        warning = {
            "warning_id": "new",
            "checker_name": "NULL",
            "location": {
                "file_path": "src/a.c",
                "line": 3,
            },
        }

        def legacy(action, *args, **kwargs):
            if action == "get_warnings_compare_svace2svace":
                return {"firstOnly": [], "secondOnly": [warning], "common": []}
            return original(action, *args, **kwargs)

        with patch.object(self.client, "_call", side_effect=legacy):
            result = self.client.execute(
                "agent_compare_warnings",
                {
                    **SCOPE,
                    "base_snapshot_id": "base",
                },
            )
        self.assertEqual(result["warnings"], [warning])
        self.assertEqual(result["scope"], SCOPE)
