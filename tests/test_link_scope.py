"""Model requests cannot drift away from arbitrary user-selected links."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from history_agent.history.constants import (
    COMPARE_ACTION,
    LINK_WARNINGS_ACTION,
    URL_ACTION,
)
from history_agent.history.links import resolve_history_url
from history_agent.review.journal import Journal
from history_agent.review.link_scope import LinkScope
from history_agent.review.router import BatchRouter
from history_agent.runtime.agent import Harness
from history_agent.runtime.recovery import ToolFailure
from tests.test_chat import answer, call
from tests.test_named_context import (
    TOOLS,
    ScriptedModel,
    Transport,
    packets,
    reply,
)

ORIGIN = "http://history.example"
PREFIX = ORIGIN + "/history/svacelight/index.html#"
CURRENT = {"project_id": 82, "stream_id": "master", "snapshot_id": "now"}
BASE = {**CURRENT, "snapshot_id": "before"}


def link(project="linux-svace", subset="First", **overrides):
    current = {**CURRENT, **overrides}
    base = {**BASE, "project_id": current["project_id"]}
    arguments = {
        **current,
        **{"base_" + k: v for k, v in base.items()},
        "subset": {
            "First": "appeared",
            "Second": "disappeared",
            "Both": "both",
        }[subset],
    }
    return {
        "kind": "comparison",
        "current": current,
        "base": base,
        "scope": base if subset == "Second" else current,
        "subset": arguments["subset"],
        "link_id": "link-" + project,
        "read_arguments": arguments,
        "review_arguments": arguments,
        "display": {
            "current_project": project,
            "current_branch": "master",
            "base_project": project,
            "base_branch": "master",
        },
    }


def schema(action):
    fields = (
        ("project_id", "snapshot_id", "warning_id")
        if action == "get_warning"
        else ("project_id", "snapshot_id", "file")
        if action == "agent_source"
        else ("project_id", "stream_id", "snapshot_id")
    )
    return {"input_schema": {"properties": dict.fromkeys(fields, {})}}


class LinkScopeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = Mock(discover=Mock(side_effect=schema))
        self.guard = LinkScope(self.history, self.directory.name)
        self.selected = link()
        self.guard.bind({"user-url": self.selected})

    def prepare(self, action, args=None):
        return self.guard.prepare(
            "history_read",
            {"action": action, "arguments": args or {}},
        )

    def test_current_returns_link_without_catalog_lookup(self):
        for tool, args in (
            ("history_current", {}),
            ("history_read", {"action": "current", "arguments": {}}),
        ):
            _, current = self.guard.prepare(tool, args)
            self.assertEqual(current["scope"], CURRENT)
            self.assertEqual(
                current["display"]["current_project"], "linux-svace"
            )
        self.history.assert_not_called()
        self.history.discover.assert_not_called()
        with self.assertRaises(ToolFailure):
            self.guard.prepare("history_current", {"project_name": "7z"})

    def test_comparison_pins_missing_fields_and_rejects_scope_changes(self):
        for action in (COMPARE_ACTION, "review_diff"):
            values, _ = self.prepare(action, {"checker_name": "BUFFER_SHIFT"})
            self.assertEqual(
                values["arguments"],
                {
                    **self.selected["read_arguments"],
                    "checker_name": "BUFFER_SHIFT",
                },
            )
            for override in (
                {"project_id": 7},
                {"stream_id": "other"},
                {"snapshot_id": "HEAD"},
                {"base_project_id": 7},
                {"base_stream_id": "other"},
                {"base_snapshot_id": "other"},
                {"subset": "both"},
            ):
                with self.subTest(action=action, override=override):
                    with self.assertRaises(ToolFailure) as caught:
                        self.prepare(action, override)
                    self.assertFalse(caught.exception.uncertain_write)
                    self.assertEqual(
                        caught.exception.context["active_links"][0]["current"],
                        CURRENT,
                    )
        for action in (
            "review_batch",
            "get_projects",
            "get_project_group_info",
            "get_warnings",
            "agent_find_warnings",
        ):
            with self.subTest(action=action), self.assertRaises(ToolFailure):
                self.prepare(action)

    def test_single_warning_and_trace_source_stay_in_selected_scope(self):
        for args in ({"project_id": 7}, {"snapshot_id": "untrusted"}):
            with self.assertRaises(ToolFailure):
                self.prepare("agent_source", args)
        warning_args, _ = self.prepare("get_warning", {"warning_id": "warn"})
        self.assertEqual(warning_args["arguments"]["snapshot_id"], "now")
        self.assertNotIn("stream_id", warning_args["arguments"])
        self.guard.observe(
            warning_args,
            reply(
                {
                    "trace": json.dumps(
                        {"snapshot_override": "verified-override"}
                    ),
                }
            ),
        )
        source, _ = self.prepare(
            "agent_source",
            {
                "file": "src/a.c",
                "snapshot_id": "verified-override",
            },
        )
        self.assertEqual(source["arguments"]["project_id"], 82)
        self.assertNotIn("stream_id", source["arguments"])
        head, _ = self.prepare("get_warning", {"snapshot_id": "HEAD"})
        self.assertEqual(head["arguments"]["snapshot_id"], "HEAD")
        single, _ = self.prepare("review_batch", {"warning_id": "warn"})
        self.assertEqual(
            single["arguments"], {**CURRENT, "warning_id": "warn"}
        )

    def test_new_selection_can_be_any_project_and_invalidates_old_link(self):
        self.prepare(LINK_WARNINGS_ACTION, {"link_id": "link-linux-svace"})
        new = link("7z", "Second", project_id=7, snapshot_id="another")
        self.assertTrue(self.guard.bind({"new-url": new}))
        _, selected = self.guard.prepare("history_current", {})
        self.assertEqual(selected["display"]["current_project"], "7z")
        self.assertEqual(selected["scope"], new["base"])
        for action, args in (
            (LINK_WARNINGS_ACTION, {"link_id": "link-linux-svace"}),
            (URL_ACTION, {"url": "user-url"}),
            (COMPARE_ACTION, self.selected["read_arguments"]),
        ):
            with self.assertRaises(ToolFailure):
                self.prepare(action, args)
        _, resolved = self.prepare(URL_ACTION, {"url": "new-url"})
        self.assertEqual(resolved, new)

    def test_multiple_links_require_unambiguous_selection(self):
        other = link("CordellCompiler", "Both", project_id=21)
        self.guard.bind({"one": self.selected, "two": other})
        with self.assertRaises(ToolFailure):
            self.guard.prepare("history_current", {})
        with self.assertRaises(ToolFailure):
            self.prepare("review_diff", {"checker_name": "NULL"})
        values, _ = self.prepare("review_diff", {"project_id": 21})
        self.assertEqual(values["arguments"], other["read_arguments"])
        self.prepare(LINK_WARNINGS_ACTION, {"link_id": "link-linux-svace"})
        self.prepare(LINK_WARNINGS_ACTION, {"link_id": "link-CordellCompiler"})

    def test_project_and_warning_links_never_expand_selected_warning(self):
        project = {
            "kind": "project",
            "scope": CURRENT,
            "current": CURRENT,
            "display": {
                "current_project": "anything",
                "current_branch": "master",
            },
        }
        self.guard.bind({"project-url": project})
        values, _ = self.prepare("review_batch")
        self.assertEqual(values["arguments"], CURRENT)
        warning = {**project, "kind": "warning", "selected_warning_id": "one"}
        self.guard.bind({"warning-url": warning})
        values, _ = self.prepare("review_batch")
        self.assertEqual(values["arguments"], {**CURRENT, "warning_id": "one"})
        values, _ = self.prepare("get_warning")
        self.assertEqual(values["arguments"]["warning_id"], "one")
        for action in ("get_warning", "review_batch"):
            with self.assertRaises(ToolFailure):
                self.prepare(action, {"warning_id": "different"})

    def test_resume_checks_existing_checkpoint_without_writing_to_it(self):
        job = Journal(self.directory.name)
        job.set("scope", CURRENT)
        job.set(
            "comparison",
            {
                key: self.selected[key]
                for key in (
                    "current",
                    "base",
                    "subset",
                )
            },
        )
        job.close()
        path = Path(self.directory.name) / (job.id + ".sqlite3")
        before = path.read_bytes()
        self.prepare("review_diff", {"job_id": job.id})
        self.assertEqual(before, path.read_bytes())
        self.guard.bind({"other": link("7z", project_id=7)})
        with self.assertRaises(ToolFailure):
            self.prepare("review_diff", {"job_id": job.id})
        self.assertEqual(before, path.read_bytes())

    def test_real_vpn_url_resolves_offline_to_its_named_project(self):
        execute = Mock(
            side_effect=[
                {"project_groups": [{"project_group_id": 1}]},
                {
                    "project_group": {
                        "projects": [
                            {"project_name": "7z", "project_id": 7},
                            {"project_name": "linux-svace", "project_id": 82},
                        ]
                    }
                },
            ]
        )
        route = (
            "compare_svace2svace/linux-svace/master/"
            "6de6b01ed3e205d3f2692a994c89ceeb5ee23a1b/linux-svace/master/"
            "59b8368ea91a78a8e383d1b7cade665b6f0cc3a6/"
        )
        result = resolve_history_url(
            execute,
            "http://samsung-data:8060/history/api",
            "http://samsung-data:8060/history/svacelight/index.html#" + route,
            ValueError,
        )
        self.assertEqual(result["display"]["current_project"], "linux-svace")
        self.assertEqual(result["current"]["project_id"], 82)
        self.assertEqual(result["base"]["project_id"], 82)
        self.assertEqual(result["subset"], "appeared")


class LinkRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_known_wrong_alias_is_rejected_before_transport_and_reloads(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            transport = Transport(reply({"warnings": [], "total": 0}))
            router = BatchRouter(
                transport,
                SimpleNamespace(
                    history=Mock(discover=Mock(side_effect=schema)),
                    root=directory,
                    last_job_id=None,
                ),
            )
            router.link_scope.bind({"user-url": link()})

            def wrong(messages, tools):
                return answer(calls=[call("history_current", {})])

            def wrong_project(messages, tools):
                current = packets(messages)[-1]
                self.assertEqual(
                    current["display"]["current_project"], "linux-svace"
                )
                return answer(
                    calls=[
                        call(
                            "history_read",
                            {
                                "action": "get_warning",
                                "arguments": {
                                    "project_id": previous,
                                },
                            },
                        )
                    ]
                )

            def corrected(messages, tools):
                error = packets(messages)[-1]
                self.assertTrue(error["context_reloaded"])
                self.assertEqual(len(transport.calls), 0)
                selection = error["active_links"][0]
                return answer(
                    calls=[
                        call(
                            "history_read",
                            {
                                "action": LINK_WARNINGS_ACTION,
                                "arguments": {
                                    "link_id": selection["link_id"],
                                    "checker_name": "NULL",
                                },
                            },
                        )
                    ]
                )

            model = ScriptedModel(
                wrong,
                wrong_project,
                corrected,
                lambda m, t: answer("No warnings"),
            )
            harness = Harness(
                router, model, TOOLS, on_event=lambda *a, **k: None
            )
            previous = harness.model_context.names.encode({"project_id": 7})[
                "project_id"
            ]
            result = await harness.ask(
                "Use linked warnings",
                prefetched=[
                    (URL_ACTION, {}, link()),
                ],
            )
            self.assertEqual(result, "No warnings")
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(
                transport.calls[0][1]["action"], LINK_WARNINGS_ACTION
            )
            self.assertNotIn("history-link", json.dumps(model.requests))
