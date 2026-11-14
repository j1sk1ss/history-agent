"""Resolve real UI routes and fetch only their pinned comparison warnings."""

import unittest
from unittest.mock import Mock

from history_agent.history.client import HistoryClient, HistoryError, Settings
from history_agent.history.constants import LINK_WARNINGS_ACTION, URL_ACTION
from history_agent.history.links import find_history_urls, resolve_history_url
from tests.fake_history import FakeHistory

USER_URL = (
    "http://127.0.0.1:8060/history/svacelight/index.html#compare_svace2svace/"
    "CordellCompiler/master/83944678c066804a6e91af29e2c3b043135e031a/"
    "CordellCompiler/master/43692d2ec745260c39cc3fcf20fb79e7bf4dcee1/"
)


def comparison_url(origin, suffix="", current="current", base="base"):
    return (
        origin + "/history/svacelight/index.html#compare_svace2svace/"
        f"Alpha/main/{current}/Zulu/release/{base}/" + suffix
    )


class HistoryLinkTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.client = HistoryClient(Settings(self.fake.url, session="valid"))

    def resolve(self, suffix="", **kwargs):
        return self.client.execute(
            URL_ACTION,
            {"url": comparison_url(self.fake.url, suffix, **kwargs)},
        )

    def test_user_markdown_url_resolves_local_project_zero_and_exact_snapshots(
        self,
    ):
        self.assertEqual(
            find_history_urls(
                f"Возьми варны NULL из [{USER_URL}]({USER_URL})"
            ),
            [USER_URL],
        )
        execute = Mock(
            side_effect=[
                {"project_groups": [{"project_group_id": 0}]},
                {
                    "project_group": {
                        "projects": [
                            {
                                "project_id": 0,
                                "project_name": "CordellCompiler",
                            }
                        ]
                    }
                },
            ]
        )
        result = resolve_history_url(
            execute,
            "http://127.0.0.1:8060/history/api",
            USER_URL,
            HistoryError,
        )
        self.assertEqual(
            result["read_arguments"],
            {
                "project_id": 0,
                "stream_id": "master",
                "snapshot_id": "83944678c066804a6e91af29e2c3b043135e031a",
                "base_project_id": 0,
                "base_stream_id": "master",
                "base_snapshot_id": "43692d2ec745260c39cc3fcf20fb79e7bf4dcee1",
                "subset": "appeared",
            },
        )

    def test_url_subset_and_evidence_side_match_ui_including_default_first(
        self,
    ):
        for suffix, subset in (
            ("", "appeared"),
            ("First/", "appeared"),
            ("Second/", "disappeared"),
            ("Both/", "both"),
        ):
            with self.subTest(suffix=suffix):
                result = self.resolve(suffix)
                self.assertEqual(result["subset"], subset)
                side = "base" if subset == "disappeared" else "current"
                self.assertEqual(result["scope"], result[side])
                self.assertEqual(
                    result["warnings_action"], LINK_WARNINGS_ACTION
                )
                self.assertEqual(
                    result["warnings_arguments"],
                    {"link_id": result["link_id"]},
                )
                self.assertFalse(result["saves_reviews"])

    def test_checker_filter_and_paging_preserve_both_sides_and_cursor(self):
        self.fake.comparison_warnings["disappeared"] = [
            {"warning_id": "other", "checker_name": "OTHER"},
            {"warning_id": "one", "checker_name": "NULL"},
            {"warning_id": "two", "checker_name": "NULL"},
            {"warning_id": "three", "checker_name": "NULL"},
        ]
        result = self.resolve("Second/cursor#src%2Fa.c:content-hash:9")
        self.assertEqual(result["selected_warning_id"], "cursor")
        self.assertEqual(
            result["location"],
            {"file": "src/a.c", "hash": "content-hash", "line": 9},
        )
        # Mutating returned metadata cannot change the host's selection.
        result["read_arguments"]["snapshot_id"] = "wrong"
        page = self.client.execute(
            LINK_WARNINGS_ACTION,
            {
                **result["warnings_arguments"],
                "checker_name": "null",
                "offset": 1,
                "limit": 1,
            },
        )
        self.assertEqual(
            page["warnings"], [{"warning_id": "two", "checker_name": "NULL"}]
        )
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["next_offset"], 2)
        self.assertEqual(page["scope"], result["base"])
        self.assertEqual(
            self.fake.requests[-1]["query"],
            {
                "act": "agent_compare_warnings",
                "project_id": "2",
                "stream_id": "main",
                "snapshot_id": "current",
                "base_project_id": "7",
                "base_stream_id": "release",
                "base_snapshot_id": "base",
                "subset": "disappeared",
                "checker_name": "null",
                "offset": "1",
                "limit": "1",
            },
        )
        self.assertFalse(self.fake.writes)

    def test_empty_first_does_not_fall_back_to_both_or_whole_project(self):
        self.fake.comparison_warnings["both"] = [
            {"warning_id": "common", "checker_name": "NULL"}
        ]
        resolved = self.resolve()
        before = len(self.fake.requests)
        page = self.client.execute(
            LINK_WARNINGS_ACTION,
            {
                **resolved["warnings_arguments"],
                "checker_name": "NULL",
            },
        )
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["warnings"], [])
        self.assertEqual(page["subset"], "appeared")
        self.assertEqual(
            [r["action"] for r in self.fake.requests[before:]],
            ["agent_compare_warnings"],
        )

    def test_pages_and_multiple_links_keep_head_snapshots_pinned(self):
        self.fake.snapshots[(2, "main")] = [{"snapshot_id": "old-current"}]
        self.fake.snapshots[(7, "release")] = [{"snapshot_id": "old-base"}]
        first = self.resolve(current="HEAD", base="HEAD")
        self.fake.snapshots[(2, "main")] = [{"snapshot_id": "new-current"}]
        self.fake.snapshots[(7, "release")] = [{"snapshot_id": "new-base"}]
        second = self.resolve(current="HEAD", base="HEAD")
        for resolved, expected in ((first, "old"), (second, "new")):
            for offset in (0, 20):
                page = self.client.execute(
                    LINK_WARNINGS_ACTION,
                    {
                        **resolved["warnings_arguments"],
                        "checker_name": "NULL",
                        "offset": offset,
                    },
                )
                self.assertEqual(
                    page["current"]["snapshot_id"], expected + "-current"
                )
                self.assertEqual(
                    page["base"]["snapshot_id"], expected + "-base"
                )
        self.assertEqual(
            sum(r["action"] == "get_snapshots" for r in self.fake.requests), 4
        )

    def test_unknown_links_missing_checker_and_scope_overrides_reject_locally(
        self,
    ):
        resolved = self.resolve()
        args = {**resolved["warnings_arguments"], "checker_name": "NULL"}
        before = len(self.fake.requests)
        for invalid in (
            {**args, "link_id": "unknown"},
            resolved["warnings_arguments"],
            {**args, "project_id": 7},
            {**args, "snapshot_id": "wrong"},
            {**args, "subset": "both"},
            {**args, "limit": 101},
            {**args, "offset": -1},
            {**args, "checker_name": ""},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HistoryError):
                    self.client.execute(LINK_WARNINGS_ACTION, invalid)
        with self.assertRaises(HistoryError):
            self.client.execute(LINK_WARNINGS_ACTION, args, write=True)
        self.assertEqual(len(self.fake.requests), before)

    def test_wrong_origins_prefixes_and_missing_projects_are_not_guessed(self):
        self.client.discover()
        for url in (
            comparison_url("http://different.invalid"),
            comparison_url(self.fake.url).replace("/history/", "/other/", 1),
            comparison_url(self.fake.url).replace("Alpha/", "Missing/"),
        ):
            with self.subTest(url=url), self.assertRaises(HistoryError):
                self.client.execute(URL_ACTION, {"url": url})
        self.assertFalse(
            any(
                r["action"]
                in ("agent_compare_warnings", "agent_find_warnings")
                for r in self.fake.requests
            )
        )
