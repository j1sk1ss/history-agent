"""Resolve different link shapes without a running server or fixed projects."""

import unittest
from urllib.parse import quote

from history_agent.history.links import find_history_urls, resolve_history_url

ORIGIN = "https://history.example/prefix"
INDEX = ORIGIN + "/history/svacelight/index.html#"


class HistoryLinkRoutesTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def execute(self, action, args):
        self.calls.append((action, args))
        if action == "get_projects":
            return {"project_groups": [{"project_group_id": 0}]}
        if action == "get_project_group_info":
            return {
                "project_group": {
                    "projects": [
                        {"project_id": 0, "project_name": "A/B + тест"},
                        {"project_id": 19, "project_name": "Other project"},
                    ]
                }
            }
        if action == "get_snapshots":
            return {
                "snapshots": [
                    {"snapshot_id": "pinned-" + str(args["project_id"])}
                ]
            }
        raise AssertionError(action)

    def resolve(self, route):
        return resolve_history_url(
            self.execute,
            ORIGIN + "/history/api",
            INDEX + route,
            ValueError,
        )

    def test_encoded_projects_branches_and_cross_project_subsets(self):
        project = quote("A/B + тест", safe="")
        branch = quote("feature/a b", safe="")
        base = quote("Other project", safe="")
        for suffix, subset in (
            ("", "appeared"),
            ("First/", "appeared"),
            ("Second/", "disappeared"),
            ("Both/", "both"),
        ):
            with self.subTest(suffix=suffix):
                result = self.resolve(
                    f"compare_svace2svace/{project}/{branch}/HEAD/"
                    f"{base}/release/old/{suffix}"
                )
                self.assertEqual(
                    result["display"]["current_project"], "A/B + тест"
                )
                self.assertEqual(
                    result["display"]["current_branch"], "feature/a b"
                )
                self.assertEqual(
                    result["current"],
                    {
                        "project_id": 0,
                        "stream_id": "feature/a b",
                        "snapshot_id": "pinned-0",
                    },
                )
                self.assertEqual(
                    result["base"],
                    {
                        "project_id": 19,
                        "stream_id": "release",
                        "snapshot_id": "old",
                    },
                )
                self.assertEqual(result["subset"], subset)

    def test_project_warning_and_source_cursor_links(self):
        project = quote("A/B + тест", safe="")
        selected = self.resolve(f"show/{project}/main/snapshot/")
        self.assertEqual(selected["kind"], "project")
        warning = self.resolve(
            f"show/{project}/main/snapshot/warn-1#src%2Fa.c:h:8"
        )
        self.assertEqual(warning["kind"], "warning")
        self.assertEqual(warning["selected_warning_id"], "warn-1")
        self.assertEqual(warning["location"]["file"], "src/a.c")
        self.assertEqual(warning["read_arguments"]["snapshot_id"], "snapshot")

    def test_unknown_routes_are_detected_and_fail_without_default_lookup(self):
        url = INDEX + "another_view/project/branch/snapshot/"
        self.assertEqual(find_history_urls("Проверь " + url), [url])
        with self.assertRaisesRegex(ValueError, "Supported history views"):
            self.resolve("another_view/project/branch/snapshot/")
        self.assertEqual(self.calls, [])
        for route in (
            "show/",
            "compare_svace2svace/a/b/c/",
            "show/missing/b/c/",
        ):
            with self.subTest(route=route), self.assertRaises(ValueError):
                self.resolve(route)
