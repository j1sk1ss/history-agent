"""All model writes obey fresh UD state and host-owned attribution."""

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from mcp import Client

from history_agent.history.client import HistoryClient, Settings
from history_agent.history.writes import ReviewWriteRejected
from history_agent.interfaces.mcp import create_server
from history_agent.review.comments import review_comment
from tests.fake_history import FakeHistory
from tests.test_batch import warning
from tests.test_client import REVIEW


class WriteProtectionTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.fake.warnings = [warning("warning")]
        self.client = self.make_client()

    def make_client(self):
        return HistoryClient(Settings(self.fake.url, session="valid"))

    def save(self, client=None, **changes):
        return (client or self.client).execute(
            "agent_set_review",
            {**REVIEW, **changes},
            write=True,
        )

    def test_every_existing_classification_is_protected_even_same_verdict(
        self,
    ):
        for classification in (1, 2, 3, 4):
            with self.subTest(classification=classification):
                original = {
                    "classification_id": classification,
                    "comment": "Human decision",
                    "user_attributes": {"keep": True},
                }
                self.fake.reviews["warning"] = copy.deepcopy(original)
                for verdict in ("TP", "FP", "WF", "UC", "UD"):
                    with self.assertRaises(ReviewWriteRejected):
                        self.save(classification=verdict)
                self.assertEqual(self.fake.reviews["warning"], original)
        self.assertFalse(self.fake.writes)

    def test_only_ud_saves_and_host_prefix_is_added_exactly_once(self):
        for text in (
            "Evidence",
            "Bismark said: Evidence",
            "Bismark said: Bismark said: Evidence",
        ):
            self.fake.reviews.clear()
            result = self.save(comment=text)
            self.assertEqual(result["comment"], "Bismark said: Evidence")
        self.assertEqual(len(self.fake.writes), 3)
        with self.assertRaises(ReviewWriteRejected):
            self.save()
        self.assertEqual(len(self.fake.writes), 3)

    def test_empty_prefixed_comment_is_rejected_without_any_post(self):
        from history_agent.history.settings import HistoryError

        with self.assertRaises(HistoryError):
            self.save(comment="Bismark said: ")
        self.assertFalse(self.fake.writes)

    def test_ud_with_existing_comment_is_preserved(self):
        original = {"classification_id": 0, "comment": "Prior notes"}
        self.fake.reviews["warning"] = original.copy()
        with self.assertRaises(ReviewWriteRejected):
            self.save()
        self.assertEqual(self.fake.reviews["warning"], original)
        self.assertFalse(self.fake.writes)

    def test_cached_ud_cannot_override_a_human_edit(self):
        self.client.execute(
            "get_warning",
            {
                "project_id": 0,
                "stream_id": "master",
                "snapshot_id": "snapshot",
                "warning_id": "warning",
            },
        )
        self.fake.reviews["warning"] = {
            "classification_id": 3,
            "comment": "Human FP",
        }
        with self.assertRaises(ReviewWriteRejected):
            self.save()
        self.assertFalse(self.fake.writes)
        self.assertEqual(
            self.fake.requests[-1]["query"]["snapshot_id"], "HEAD"
        )

    def test_missing_warning_or_status_fails_closed_before_write(self):
        self.fake.warnings.clear()
        with self.assertRaises(ReviewWriteRejected):
            self.save()
        self.assertFalse(self.fake.writes)

    def test_concurrent_clients_serialize_check_and_write_for_the_same_warning(
        self,
    ):
        second = self.make_client()
        ready = threading.Barrier(2)

        def run(client):
            ready.wait(timeout=3)
            try:
                return self.save(client)
            except ReviewWriteRejected:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(run, client) for client in (self.client, second)
            ]
            results = [result.result(timeout=5) for result in results]
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(len(self.fake.writes), 1)

    def test_prefix_survives_source_reference_normalization(self):
        result = review_comment(
            "Bismark said: /build/repo/src/a.c:3",
            [
                {
                    "file": "/build/repo/src/a.c",
                    "line": 3,
                    "snapshot_id": "revision",
                },
            ],
        )
        self.assertEqual(result, "Bismark said: src/a.c:3")
        self.assertEqual(review_comment(result), result)

    def test_even_a_legacy_overwrite_capability_cannot_enable_overwrites(self):
        import tempfile

        from history_agent.inference.model import ChatError
        from history_agent.review.batch import BatchRunner
        from history_agent.review.journal import Journal
        from history_agent.review.requests import BatchRequest

        scope = {
            "project_id": 0,
            "stream_id": "master",
            "snapshot_id": "snapshot",
        }
        with tempfile.TemporaryDirectory() as directory:
            runner = BatchRunner(
                self.client,
                None,
                None,
                directory,
                {"model": "synthetic"},
                allow_write=True,
                allow_overwrite=True,
            )
            with self.assertRaises(ChatError):
                runner.validate_request({**scope, "overwrite": True}, True)
            self.assertFalse(runner.allow_overwrite)
            job = Journal(directory)
            try:
                runner.initialize_job(
                    job, BatchRequest.model_validate(scope), True
                )
                job.set("overwrite", True)
                runner.validate_checkpoint(job, True)
                self.assertIs(job.get("overwrite"), False)
            finally:
                job.close()
        self.assertFalse(self.fake.writes)


class MCPWriteProtectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_mcp_reports_no_write_for_a_protected_warning(self):
        import json

        with FakeHistory() as fake:
            fake.warnings = [warning("warning")]
            fake.reviews["warning"] = {
                "classification_id": 1,
                "comment": "Human TP",
            }
            async with Client(
                create_server(
                    HistoryClient(
                        Settings(fake.url, session="valid"),
                    )
                )
            ) as client:
                result = await client.call_tool(
                    "history_write",
                    {
                        "action": "agent_set_review",
                        "arguments": REVIEW,
                    },
                )
                data = json.loads(result.content[0].text)
                self.assertTrue(data["write_rejected"])
                self.assertFalse(data["write_attempted"])
            self.assertFalse(fake.writes)
