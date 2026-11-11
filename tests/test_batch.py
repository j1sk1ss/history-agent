import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.inference.model import ChatError
from history_agent.interfaces.chat import Harness
from history_agent.interfaces.mcp import create_server
from history_agent.review import patterns
from history_agent.review.batch import BatchRunner
from history_agent.review.client import ItemClient
from history_agent.review.constants import BATCH_ACTION
from history_agent.review.journal import Journal
from history_agent.review.requests import BatchCommand
from history_agent.review.router import BatchRouter
from history_agent.review.system_one import (
    SystemOne,
    SystemOneClient,
    load_warning_context,
    warning_context,
)
from tests.fake_history import FakeHistory
from tests.test_chat import answer, call
from tests.test_streaming import (
    USAGE,
    StreamingServer,
    delta,
    finish,
    tool_chunks,
)

SCOPE = {"project_id": 0, "stream_id": "master", "snapshot_id": "snapshot"}
MODEL = {"base": "http://synthetic/v1", "model": "synthetic"}
HTML = """<html><head><title>Fixture guide</title></head><body>
<h3 id="A_CHECKER">A_CHECKER</h3><p>UNIQUE_A_RULE</p>
<h3 id="B_CHECKER">B_CHECKER</h3><p>UNIQUE_B_RULE</p></body></html>"""


def warning(id, checker="A_CHECKER"):
    return {
        "warning_id": id,
        "checker_name": checker,
        "message": "Synthetic finding " + id,
        "location": {
            "file_path": "src/" + id + ".c",
            "line": 3,
            "snapshot_override": None,
        },
        "classification_name": "Undecided",
        "comment": "",
    }


class ReviewModel:
    def __init__(self, fail=None, verdict=None, callback=None):
        self.requests, self.fail, self.verdict, self.callback = (
            [],
            fail,
            verdict,
            callback,
        )

    def complete(self, messages, tools, on_event=None):
        self.requests.append(
            copy.deepcopy({"messages": messages, "tools": tools})
        )
        items = [
            json.loads(message["content"])["warning"]
            for message in messages
            if message["role"] == "tool"
            and "warning" in json.loads(message["content"])
        ]
        if not items:
            return answer(json.dumps(self.verdict))
        item = items[0]
        if item["warning_id"] == self.fail:
            raise ChatError("Synthetic interruption")
        if self.callback:
            self.callback(item)
        result = self.verdict or {
            "classification": "TP",
            "comment": "Synthetic evidence for " + item["warning_id"],
            "evidence": [
                {
                    "file": item["location"]["file_path"],
                    "line": 3,
                    "snapshot_id": "snapshot",
                }
            ],
        }
        response = answer(json.dumps(result))
        response["choices"][0]["message"]["reasoning_content"] = (
            "PRIVATE_THOUGHT_" + item["warning_id"]
        )
        return response


class BatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="history-batch-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 5

    def runner(self, history, client, tools, model, **kwargs):
        system_one = kwargs.pop("system_one", None)

        def factory(item_client):
            transport = SystemOneClient(item_client, system_one)
            reviewer = Harness(
                transport,
                model,
                tools,
                max_steps=4,
                on_event=lambda *args, **kwargs: None,
            )
            transport.context = lambda: warning_context(reviewer.messages)
            transport.load_context = lambda: load_warning_context(
                item_client, reviewer.messages
            )
            return reviewer

        return BatchRunner(
            history, client, factory, self.root, MODEL, **kwargs
        )

    def fixture(self):
        server = FakeHistory()
        server.warnings = [
            warning("b1", "B_CHECKER"),
            warning("a2"),
            warning("a1"),
        ]
        server.guide_html = HTML
        return server

    async def test_scope_error_auto_reloads_and_model_corrects_the_read(self):
        class CorrectingModel(ReviewModel):
            def complete(self, messages, tools, on_event=None):
                verdict = super().complete(messages, tools, on_event)
                if len(self.requests) == 1:
                    return answer(calls=[call("history_read", {
                        "action": "agent_source",
                        "arguments": {
                            "project_id": 0, "snapshot_id": "WRONG_SNAPSHOT",
                            "file": "src/a1.c", "line": 3,
                        },
                    })])
                if len(self.requests) == 2:
                    self.corrected_prompt = messages[-1]["content"]
                    return answer(calls=[call("history_read", {
                        "action": "agent_source",
                        "arguments": {
                            "project_id": 0, "snapshot_id": "snapshot",
                            "file": "src/a1.c", "line": 3,
                        },
                    })])
                return verdict

        with self.fixture() as server:
            server.warnings = [warning("a1")]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = CorrectingModel()
                events = []
                runner = self.runner(
                    history, client, tools, model, allow_write=True,
                    on_event=lambda kind, **data: events.append((kind, data)),
                )
                result = await runner.run(SCOPE, write=True)
        self.assertEqual(result["states"], {"saved": 1})
        self.assertEqual(len(server.writes), 1)
        self.assertEqual(len(model.requests), 3)
        self.assertIn("automatically reloaded", model.corrected_prompt)
        self.assertIn(
            'Allowed snapshot IDs: ["snapshot"]', model.corrected_prompt
        )
        self.assertTrue(any(kind == "notice" for kind, _ in events))
        self.assertFalse(any(
            item["query"].get("snapshot_id") == "WRONG_SNAPSHOT"
            for item in server.requests
        ))

    async def test_repeated_scope_errors_stop_after_two_auto_reloads(self):
        class WrongModel(ReviewModel):
            def complete(self, messages, tools, on_event=None):
                super().complete(messages, tools, on_event)
                return answer(calls=[call("history_read", {
                    "action": "agent_source",
                    "arguments": {
                        **SCOPE, "snapshot_id": "WRONG_SNAPSHOT",
                        "file": "src/a1.c", "line": 3,
                    },
                })])

        with self.fixture() as server:
            server.warnings = [warning("a1")]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                model = WrongModel()
                runner = self.runner(
                    history, client, (await client.list_tools()).tools, model,
                    allow_write=True,
                )
                with self.assertRaisesRegex(
                    ChatError, "outside the warning/trace"
                ):
                    await runner.run(SCOPE, write=True)
        self.assertEqual(len(model.requests), 3)
        self.assertFalse(server.writes)

    async def test_system_one_threshold_and_failure_fallback(self):
        for confidence in (0.8, 0.79, 0.95, 80, 79, 69, "invalid", None):
            with self.subTest(confidence=confidence), self.fixture() as server:
                server.warnings = [warning("a1")]
                history = HistoryClient(
                    Settings(server.url, "tester", "test-secret")
                )
                async with Client(create_server(history)) as client:
                    tools = (await client.list_tools()).tools
                    model = ReviewModel()
                    adviser = SystemOne("http://systemone", "fast", 1)
                    adviser.available = True
                    runner = self.runner(
                        history, client, tools, model, allow_write=True,
                        system_one=adviser,
                    )
                    with patch(
                        "history_agent.review.system_one._request"
                    ) as request, patch(
                        "history_agent.review.system_one._records",
                        side_effect=lambda *_: iter([{
                            "answers": {"sentiment": {
                                "choice": "FP", "confidence": confidence,
                            }}
                        }]),
                    ):
                        if confidence is None:
                            request.side_effect = OSError("unavailable")
                        result = await runner.run(SCOPE, write=True)
                    self.assertEqual(result["states"], {"saved": 1})
                    high = confidence in (0.8, 0.79, 0.95, 80, 79, 0.7)
                    self.assertEqual(len(model.requests), 0 if high else 1)
                    self.assertEqual(
                        result["classifications"],
                        {"FP" if high else "TP": 1},
                    )
                    if confidence in (0.69, 69):
                        hints = [json.loads(message["content"])
                                 for message in model.requests[0]["messages"]
                                 if message["role"] == "tool"]
                        hint = next(h for h in hints if "confidence" in h)
                        self.assertEqual(hint["classification"], "FP")
                        self.assertEqual(hint["confidence"], 0.69)
                    payload = request.call_args.args[2] if confidence else {}
                    if payload:
                        self.assertIn("UNIQUE_A_RULE", payload["state"])
                        state = json.loads(payload["state"])
                        self.assertEqual(set(state), {
                            "code", "environment", "warning_description",
                            "tp_conditions",
                            "trace",
                        })
                        self.assertEqual(
                            state["warning_description"],
                            "A_CHECKER: Synthetic finding a1",
                        )
                        self.assertNotIn("snapshot", payload["state"])
                        self.assertNotIn("project_id", payload["state"])

    async def test_session_save_mode_applies_to_history_read_in_real_time(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                counts = []
                model = ReviewModel(
                    callback=lambda item: counts.append(len(server.writes))
                )
                runner = self.runner(
                    history, client, tools, model, allow_write=True
                )
                router = BatchRouter(client, runner)
                await router.call_tool(
                    "history_read",
                    {"action": BATCH_ACTION, "arguments": SCOPE},
                )
                self.assertEqual(counts, [0, 1, 2])
                self.assertEqual(len(server.writes), 3)
                self.assertEqual(runner.last_summary["states"], {"saved": 3})

    async def test_resume_preview_saves_prepared_reviews_without_reinference(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel()
                runner = self.runner(
                    history, client, tools, model, allow_write=True
                )
                preview = await runner.run(SCOPE, write=False)
                self.assertFalse(server.writes)
                result = await runner.run(
                    {"job_id": preview["job_id"]}, write=True
                )
                self.assertEqual(result["states"], {"saved": 3})
                self.assertEqual(len(server.writes), 3)
                self.assertEqual(len(model.requests), 3)
                await runner.run({"job_id": result["job_id"]}, write=True)
                self.assertEqual(len(server.writes), 3)

    async def test_retry_rejected_evidence_receives_validation_feedback(self):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel(
                    verdict={
                        "classification": "TP",
                        "comment": "Rejected evidence",
                        "evidence": [
                            {
                                "file": "src/unread.c",
                                "line": 999,
                                "snapshot_id": "snapshot",
                            }
                        ],
                    }
                )
                runner = self.runner(
                    history, client, tools, model, allow_write=True
                )
                with self.assertRaisesRegex(ChatError, "not actually read"):
                    await runner.run(SCOPE, write=True)
                self.assertFalse(server.writes)
                model.verdict = None
                result = await runner.run(
                    {"job_id": runner.last_job_id}, write=True
                )
                self.assertEqual(result["states"], {"saved": 1})
                prompt = model.requests[-1]["messages"][-1]["content"]
                self.assertIn("rejected by host validation", prompt)
                self.assertIn("src/unread.c", prompt)

    async def test_preview_resume_preserves_new_server_reviews(self):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                model = ReviewModel()
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    model,
                    allow_write=True,
                )
                preview = await runner.run(SCOPE)
                server.reviews["a1"] = {
                    "classification_id": 3,
                    "comment": "New human review",
                }
                result = await runner.run(
                    {"job_id": preview["job_id"]}, write=True
                )
                self.assertEqual(result["states"], {"conflict": 1})
                self.assertFalse(server.writes)
                self.assertEqual(len(model.requests), 1)

    async def test_warning_context_is_isolated_with_external_docs(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel()
                runner = self.runner(history, client, tools, model)
                make_reviewer = runner.reviewer_factory

                def stale_reviewer(item_client):
                    reviewer = make_reviewer(item_client)
                    reviewer.messages.append(
                        {"role": "user", "content": "STALE_CHECKER_CONTEXT"}
                    )
                    return reviewer

                runner.reviewer_factory = stale_reviewer
                result = await runner.run(SCOPE)
                self.assertEqual(result["states"], {"reviewed": 3})
                self.assertFalse(server.writes)
                self.assertEqual(len(model.requests), 3)
                for index, id in enumerate(("a1", "a2", "b1")):
                    text = json.dumps(
                        model.requests[index], ensure_ascii=False
                    )
                    self.assertIn("src/" + id + ".c", text)
                    self.assertNotIn("PRIVATE_THOUGHT_", text)
                    self.assertNotIn("STALE_CHECKER_CONTEXT", text)
                    for other in {"a1", "a2", "b1"} - {id}:
                        self.assertNotIn("src/" + other + ".c", text)
                    self.assertNotIn(
                        "history_write",
                        json.dumps(model.requests[index]["tools"]),
                    )
                self.assertNotIn(
                    "UNIQUE_A_RULE", json.dumps(model.requests[2])
                )
                self.assertNotIn(
                    "UNIQUE_B_RULE", json.dumps(model.requests[0])
                )
                self.assertEqual(
                    sum(
                        request["action"] == "agent_checker_info"
                        for request in server.requests
                    ),
                    2,
                )
                self.assertEqual(len(server.guide_requests), 1)
                journal = Journal(self.root, result["job_id"])
                try:
                    self.assertEqual(
                        sum(
                            journal.cached_checker(checker) is not None
                            for checker in ("A_CHECKER", "B_CHECKER")
                        ),
                        2,
                    )
                    self.assertEqual(
                        journal.path.stat().st_mode & 0o777, 0o600
                    )
                    self.assertNotIn(
                        "test-secret",
                        journal.path.read_bytes().decode(errors="ignore"),
                    )
                finally:
                    journal.close()

    async def test_resume_restores_goal_without_old_context(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                first = self.runner(
                    history, client, tools, ReviewModel(fail="a2")
                )
                with self.assertRaisesRegex(ChatError, "job_id="):
                    await first.run(
                        {**SCOPE, "instructions": "USER_POLICY_KEEP"}
                    )
                id = first.last_summary["job_id"]
                self.assertEqual(
                    first.last_summary["states"],
                    {"analyzing": 1, "pending": 1, "reviewed": 1},
                )
                model = ReviewModel()
                second = self.runner(history, client, tools, model)
                result = await second.run({"job_id": id})
                self.assertEqual(result["states"], {"reviewed": 3})
                self.assertEqual(len(model.requests), 2)
                for request in model.requests:
                    self.assertIn("USER_POLICY_KEEP", json.dumps(request))
                    self.assertNotIn("src/a1.c", json.dumps(request))
                self.assertEqual(
                    sum(
                        request["action"] == "agent_checker_info"
                        for request in server.requests
                    ),
                    2,
                )

    async def test_resume_does_not_rewrite_saved_verdicts(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel()
                runner = self.runner(
                    history, client, tools, model, allow_write=True
                )
                result = await runner.run(SCOPE, write=True)
                self.assertEqual(result["states"], {"saved": 3})
                self.assertEqual(len(server.writes), 3)
                self.assertTrue(
                    all(
                        write["stream_id"] == "master"
                        and write["project_id"] == "0"
                        for write in server.writes
                    )
                )
                self.assertTrue(
                    all(
                        "src/" in write["comment"]
                        and "[snapshot" not in write["comment"]
                        for write in server.writes
                    )
                )
                self.assertTrue(
                    all(
                        review["user_attributes"] == {"custom": "keep"}
                        for review in server.reviews.values()
                    )
                )
                await runner.run({"job_id": result["job_id"]}, write=True)
                self.assertEqual(len(server.writes), 3)
                self.assertEqual(len(model.requests), 3)

    async def test_lost_write_response_is_reconciled_once(
        self,
    ):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            server.disconnect_on_write = True
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                model = ReviewModel()
                runner = self.runner(
                    history, client, tools, model, allow_write=True
                )
                with self.assertRaisesRegex(ChatError, "Batch stopped"):
                    await runner.run(SCOPE, write=True)
                self.assertEqual(
                    runner.last_summary["states"], {"uncertain": 1}
                )
                server.disconnect_on_write = False
                result = await runner.run(
                    {"job_id": runner.last_summary["job_id"]}, write=True
                )
                self.assertEqual(result["states"], {"saved": 1})
                self.assertEqual(len(server.writes), 1)
                self.assertEqual(len(model.requests), 1)

    async def test_unknown_uncommitted_write_is_not_automatically_retried(
        self,
    ):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            server.disconnect_on_write = True
            server.apply_disconnected_write = False
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    ReviewModel(),
                    allow_write=True,
                )
                with self.assertRaises(ChatError):
                    await runner.run(SCOPE, write=True)
                id = runner.last_summary["job_id"]
                server.disconnect_on_write = False
                with self.assertRaisesRegex(ChatError, "will not be retried"):
                    await runner.run({"job_id": id}, write=True)
                self.assertEqual(len(server.writes), 1)

    async def test_existing_reviews_skipped_and_concurrent_edits_preserved(
        self,
    ):
        with self.fixture() as server:
            server.reviews["a1"] = {
                "classification_id": 3,
                "comment": "human FP",
            }

            def edit(item):
                server.reviews[item["warning_id"]] = {
                    "classification_id": 4,
                    "comment": "new human review",
                }

            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                model = ReviewModel(callback=edit)
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    model,
                    allow_write=True,
                )
                result = await runner.run(SCOPE, write=True)
                self.assertEqual(
                    result["states"], {"conflict": 2, "skipped": 1}
                )
                self.assertFalse(server.writes)
                self.assertEqual(server.reviews["a1"]["comment"], "human FP")

    async def test_invalid_verdict_or_invented_source_never_saves(self):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                for verdict in (
                    {
                        "classification": "TP",
                        "comment": "no source",
                        "evidence": [],
                    },
                    {
                        "classification": "FP",
                        "comment": "invented",
                        "evidence": [
                            {
                                "file": "other.c",
                                "line": 1,
                                "snapshot_id": "snapshot",
                            }
                        ],
                    },
                    {
                        "classification": "INVALID",
                        "comment": "bad",
                        "evidence": [],
                    },
                ):
                    runner = self.runner(
                        history,
                        client,
                        tools,
                        ReviewModel(verdict=verdict),
                        allow_write=True,
                    )
                    with self.assertRaises(ChatError):
                        await runner.run(SCOPE, write=True)
                    self.assertFalse(server.writes)

    async def test_wf_is_saved_for_valid_non_actionable_warning(self):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            verdict = {
                "classification": "WF",
                "comment": (
                    "The documented condition holds in src/a1.c:3, but the"
                    " code is an intentional checker fixture and has no "
                    "production behavior."
                ),
                "evidence": [
                    {"file": "src/a1.c", "line": 3, "snapshot_id": "snapshot"}
                ],
                "pattern": {
                    "name": "intentional-fixture",
                    "criterion": (
                        "A real finding in a purposely written checker "
                        "fixture is not actionable."
                    ),
                    "checks": [
                        "Confirm the checker condition in source.",
                        "Confirm the code is an intentional fixture.",
                    ],
                },
            }
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                model = ReviewModel(verdict=verdict)
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    model,
                    allow_write=True,
                )
                result = await runner.run(SCOPE, write=True)
                self.assertEqual(result["states"], {"saved": 1})
                self.assertEqual(server.writes[0]["classification"], "WF")
                self.assertEqual(server.reviews["a1"]["classification_id"], 2)
                journal = Journal(self.root, result["job_id"])
                try:
                    rows = patterns.recall(journal, "A_CHECKER")
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["classification"], "WF")
                    self.assertIn("intentional-fixture", json.dumps(rows[0]))
                finally:
                    journal.close()
                request = json.dumps(model.requests[0])
                self.assertIn(
                    "WF does not mean the detector is wrong", request
                )
                self.assertIn(
                    "Do not turn a technically correct but irrelevant warning "
                    "into FP",
                    request,
                )

    async def test_wf_without_source_evidence_is_not_saved(self):
        with self.fixture() as server:
            server.warnings = [warning("a1")]
            verdict = {
                "classification": "WF",
                "comment": "Not actionable, no code inspected.",
                "evidence": [],
            }
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    ReviewModel(verdict=verdict),
                    allow_write=True,
                )
                with self.assertRaisesRegex(
                    ChatError, "TP/FP/WF verdict has no source evidence"
                ):
                    await runner.run(SCOPE, write=True)
                self.assertFalse(server.writes)

    async def test_schema_and_permissions_reject_before_any_model_or_write(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                model = ReviewModel()
                runner = self.runner(
                    history, client, (await client.list_tools()).tools, model
                )
                for args, write in (
                    (SCOPE, True),
                    ({**SCOPE, "snapshot_id": "HEAD"}, False),
                    ({**SCOPE, "overwrite": True}, False),
                ):
                    with self.assertRaises(ChatError):
                        await runner.run(args, write=write)
                self.assertFalse(server.requests)
                self.assertFalse(model.requests)

    async def test_queue_materializes_all_pages_before_reviewing(self):
        with self.fixture() as server:
            server.warnings = [warning(f"a{i:03d}") for i in range(101)]
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                runner = self.runner(
                    history,
                    client,
                    (await client.list_tools()).tools,
                    ReviewModel(),
                )
                journal = Journal(self.root)
                try:
                    journal.set("scope", SCOPE)
                    await runner.enumerate(journal)
                    self.assertEqual(
                        len(journal.items()),
                        101,
                    )
                    self.assertTrue(journal.get("queue_complete"))
                    pages = [
                        request["query"]["offset"]
                        for request in server.requests
                        if request["action"] == "agent_find_warnings"
                    ]
                    self.assertEqual(pages, ["0", "100"])
                    self.assertFalse(server.writes)
                finally:
                    journal.close()

    async def test_item_client_restricts_scope_and_router_adds_no_model_tools(
        self,
    ):
        with self.fixture() as server:
            history = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            async with Client(create_server(history)) as client:
                tools = (await client.list_tools()).tools
                item = ItemClient(client, SCOPE, set())
                for name, args in (
                    ("history_write", {}),
                    (
                        "history_read",
                        {
                            "action": "agent_source",
                            "arguments": {"project_id": 42},
                        },
                    ),
                    (
                        "history_read",
                        {
                            "action": "agent_source",
                            "arguments": {"snapshot_id": "other"},
                        },
                    ),
                ):
                    with self.assertRaises(ChatError):
                        await item.call_tool(name, args)
                runner = self.runner(history, client, tools, ReviewModel())
                router = BatchRouter(client, runner)
                data = await router.call_tool(
                    "history_discover", {"action": BATCH_ACTION}
                )
                self.assertEqual(
                    json.loads(data.content[0].text)["input_schema"],
                    BatchCommand.model_json_schema(),
                )
                data = await router.call_tool("history_discover", {})
                self.assertIn(
                    BATCH_ACTION,
                    [
                        action["name"]
                        for action in json.loads(data.content[0].text)[
                            "actions"
                        ]
                    ],
                )
                self.assertEqual(len((await client.list_tools()).tools), 4)

    def test_journal_lock_rejects_a_second_runner(self):
        first = Journal(self.root)
        second = Journal(self.root, first.id)
        try:
            with first.lock():
                with self.assertRaisesRegex(ChatError, "already running"):
                    with second.lock():
                        pass
        finally:
            first.close()
            second.close()

    async def test_cli_resume_keeps_context_small_without_reapplying(
        self,
    ):
        verdict = {
            "classification": "TP",
            "comment": "Synthetic source evidence",
            "evidence": [
                {"file": "src/a1.c", "line": 3, "snapshot_id": "snapshot"}
            ],
        }
        responses = [
            [delta(content=json.dumps(verdict)), finish(), USAGE, "[DONE]"]
        ]
        with (
            self.fixture() as server,
            StreamingServer(responses) as model_server,
        ):
            server.warnings = [warning("a1")]
            command = [
                sys.executable,
                "-m",
                "history_agent.interfaces.chat",
                "--model-url",
                model_server.url,
                "--model",
                "synthetic",
                "--history-url",
                server.url,
                "--batch-state-dir",
                str(self.root),
                "--allow-write",
                "--plain",
            ]
            env = {
                **os.environ,
                "HISTORY_AGENT_CONFIG": str(self.root / "chat-config.json"),
                "HISTORY_CONFIG": "",
                "HISTORY_USER": "tester",
                "HISTORY_PASSWORD": "test-secret",
                "HISTORY_SESSION": "",
            }
            first = await asyncio.to_thread(
                subprocess.run,
                command + ["--batch", "0", "master", "snapshot"],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            result = json.loads(first.stdout)
            self.assertEqual(result["states"], {"saved": 1})
            second = await asyncio.to_thread(
                subprocess.run,
                command + ["--resume", result["job_id"]],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(server.writes), 1)
            self.assertEqual(len(model_server.requests), 1)

    async def test_bulk_chat_does_not_retain_worker_context(
        self,
    ):
        verdict = {
            "classification": "TP",
            "comment": "Source checked",
            "evidence": [
                {"file": "src/a1.c", "line": 3, "snapshot_id": "snapshot"}
            ],
        }
        responses = [
            tool_chunks("history_discover", {"action": BATCH_ACTION}),
            tool_chunks(
                "history_read", {"action": BATCH_ACTION, "arguments": SCOPE}
            ),
            [delta(content=json.dumps(verdict)), finish(), USAGE, "[DONE]"],
            [
                delta(content="Reviewed one warning."),
                finish(),
                USAGE,
                "[DONE]",
            ],
        ]
        with (
            self.fixture() as server,
            StreamingServer(responses) as model_server,
        ):
            server.warnings = [warning("a1")]
            env = {
                **os.environ,
                "HISTORY_AGENT_CONFIG": str(self.root / "chat-config.json"),
                "HISTORY_CONFIG": "",
                "HISTORY_USER": "tester",
                "HISTORY_PASSWORD": "test-secret",
                "HISTORY_SESSION": "",
            }
            process = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "history_agent.interfaces.chat",
                    "--model-url",
                    model_server.url,
                    "--model",
                    "synthetic",
                    "--history-url",
                    server.url,
                    "--batch-state-dir",
                    str(self.root),
                    "--prompt",
                    "Review all project warnings",
                    "--plain",
                    "--read-only",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout.strip(), "Reviewed one warning.")
            self.assertIn(
                "UNIQUE_A_RULE", json.dumps(model_server.requests[2])
            )
            self.assertNotIn(
                "UNIQUE_A_RULE", json.dumps(model_server.requests[3])
            )
            self.assertEqual(len(model_server.requests[3]["tools"]), 5)
            self.assertFalse(server.writes)


if __name__ == "__main__":
    unittest.main()
