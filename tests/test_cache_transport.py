"""Exercise Redis over TCP and HTTP caching at the history boundary."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import fakeredis

from history_agent.history.client import HistoryClient, Settings
from history_agent.history.redis_backend import RedisBackend
from tests.fake_history import FakeHistory
from tests.test_batch import warning
from tests.test_client import REVIEW, SOURCE


class CachedTransportTest(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.TcpFakeServer(
            ("127.0.0.1", 0), server_version=(7, 0)
        )
        self.thread = threading.Thread(
            target=self.redis.serve_forever, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.redis.server_close)
        self.addCleanup(self.redis.shutdown)
        self.clients = []
        self.addCleanup(self.close_clients)
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.fake.warnings = [warning("warning")]
        self.settings = Settings(
            self.fake.url,
            session="valid",
            redis_url=(
                "redis://127.0.0.1:" + str(self.redis.server_address[1]) + "/0"
            ),
        )

    def close_clients(self):
        for client in self.clients:
            client._transport.cache.backend.client.close()

    def client(self):
        client = HistoryClient(self.settings)
        self.clients.append(client)
        return client

    def count(self, action):
        return sum(r["action"] == action for r in self.fake.requests)

    def test_new_client_reuses_catalog_source_and_guide_without_http(self):
        self.fake.guide_html = "<h2>NULL</h2><p>Checker description</p>"
        first, second = self.client(), self.client()
        first.execute("agent_source", SOURCE)
        first.execute("agent_checker_info", {"checker_name": "NULL"})
        before = len(self.fake.requests), len(self.fake.guide_requests)
        second.execute("agent_source", SOURCE)
        second.execute("agent_checker_info", {"checker_name": "NULL"})
        self.assertEqual(
            (len(self.fake.requests), len(self.fake.guide_requests)), before
        )
        self.assertEqual(self.count("agent_source"), 1)
        self.assertEqual(self.count("get_actions"), 1)

    def test_write_invalidates_review_reads_and_preserves_cached_source(self):
        first, second = self.client(), self.client()
        args = {
            "project_id": 0,
            "stream_id": "master",
            "snapshot_id": "snapshot",
            "warning_id": "warning",
        }
        before = first.execute("get_warning", args)
        self.assertEqual(
            before["warning"]["attributes"]["classification_id"], 0
        )
        first.execute("agent_source", SOURCE)
        second.execute("agent_set_review", REVIEW, write=True)
        after = first.execute("get_warning", args)
        self.assertEqual(
            after["warning"]["attributes"]["classification_id"], 1
        )
        self.assertEqual(
            after["warning"]["attributes"]["comment"],
            "Bismark said: " + REVIEW["comment"],
        )
        first.execute("agent_source", SOURCE)
        self.assertEqual(self.count("agent_source"), 1)
        heads = [
            r
            for r in self.fake.requests
            if r["action"] == "get_warning"
            and r["query"].get("snapshot_id") == "HEAD"
        ]
        self.assertEqual(len(heads), 1)

    def test_cached_http_hit_is_not_serialized_behind_another_slow_execute(
        self,
    ):
        client = self.client()
        client.execute("agent_source", SOURCE)
        started, release = threading.Event(), threading.Event()
        original = client._transport._request

        def slow(action, query=None, body=None):
            if action == "agent_source" and query.get("file") == "slow.c":
                started.set()
                if not release.wait(3):
                    raise AssertionError("Test did not release slow request")
            return original(action, query, body)

        with patch.object(client._transport, "_request", side_effect=slow):
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(
                    client.execute,
                    "agent_source",
                    {**SOURCE, "file": "slow.c"},
                )
                try:
                    self.assertTrue(started.wait(2))
                    hit = pool.submit(client.execute, "agent_source", SOURCE)
                    self.assertEqual(
                        hit.result(timeout=0.5)["file"], SOURCE["file"]
                    )
                finally:
                    release.set()
                pending.result(timeout=3)

    def test_lost_write_response_still_invalidates_cached_ud(self):
        from history_agent.history.settings import HistoryError

        first, second = self.client(), self.client()
        args = {
            "project_id": 0,
            "stream_id": "master",
            "snapshot_id": "snapshot",
            "warning_id": "warning",
        }
        first.execute("get_warning", args)
        self.fake.disconnect_on_write = True
        with self.assertRaises(HistoryError):
            second.execute("agent_set_review", REVIEW, write=True)
        result = first.execute("get_warning", args)
        self.assertEqual(
            result["warning"]["attributes"]["classification_id"], 1
        )
        self.assertEqual(len(self.fake.writes), 1)


class RedisOutageTest(unittest.TestCase):
    def test_fast_cache_circuit_breaker_never_retries_a_redis_timeout(self):
        from redis import TimeoutError

        backend = RedisBackend("", client=Mock())
        backend.client.get.side_effect = TimeoutError("private-password")
        with self.assertLogs(
            "history_agent.history.redis_backend", "WARNING"
        ) as log:
            self.assertIsNone(backend.call("get", "one"))
            self.assertIsNone(backend.call("get", "two"))
        self.assertEqual(backend.client.get.call_count, 1)
        self.assertNotIn("private-password", str(log.output))
