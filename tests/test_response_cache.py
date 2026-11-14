"""Redis reuse and request coalescing must reduce actual backend work."""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock, patch

import fakeredis

from history_agent.history.cache import ResponseCache
from history_agent.history.cache_lease import CacheLease
from history_agent.history.redis_backend import RedisBackend
from history_agent.history.settings import HistoryError, Settings

SOURCE = {"project_id": 0, "snapshot_id": "revision", "file": "a.c", "line": 1}


class ResponseCacheTest(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()
        self.settings = Settings(
            "http://history.example",
            "user",
            "secret",
            timeout=600,
            redis_url="",
        )
        self.cache = self.make_cache()

    def make_cache(
        self, settings=None, endpoint="http://history.example/history/api"
    ):
        return ResponseCache(
            settings or self.settings,
            endpoint,
            backend=RedisBackend(
                "",
                client=fakeredis.FakeRedis(server=self.server),
            ),
        )

    def read(self, cache, load, action="agent_source", args=None):
        return cache.read(action, args or SOURCE, None, load)

    def test_cache_reused_across_instances_with_canonical_queries_and_copies(
        self,
    ):
        load = Mock(return_value={"lines": ["original"]})
        first = self.read(self.cache, load)
        first["lines"].append("changed locally")
        second = self.read(
            self.make_cache(),
            load,
            args={
                key: str(value)
                for key, value in reversed(list(SOURCE.items()))
            },
        )
        self.assertEqual(second, {"lines": ["original"]})
        load.assert_called_once()
        keys = repr(self.cache.backend.client.keys())
        for secret in ("secret", "user", "revision", "a.c", "history.example"):
            self.assertNotIn(secret, keys)

    def test_server_credentials_and_request_scope_cannot_share_data(self):
        load = Mock(return_value={"value": "private"})
        for cache, args in (
            (self.cache, SOURCE),
            (self.make_cache(replace(self.settings, user="another")), SOURCE),
            (
                self.make_cache(replace(self.settings, password="changed")),
                SOURCE,
            ),
            (self.make_cache(endpoint="http://other/history/api"), SOURCE),
            (self.cache, {**SOURCE, "snapshot_id": "another"}),
            (self.cache, {**SOURCE, "project_id": 8}),
        ):
            self.read(cache, load, args=args)
        self.assertEqual(load.call_count, 6)

    def test_configured_freshness_cannot_reuse_a_longer_lived_response(self):
        load = Mock(return_value={"ok": True})
        self.read(self.cache, load)
        shorter = self.make_cache(replace(self.settings, source_cache_ttl=1))
        self.read(shorter, load)
        self.assertEqual(load.call_count, 2)

    def test_identical_requests_share_one_load_across_threads_and_instances(
        self,
    ):
        started, release = threading.Event(), threading.Event()
        calls = []

        def load():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(3))
            return {"value": "ready"}

        other = self.make_cache()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(self.read, self.cache if i % 2 else other, load)
                for i in range(6)
            ]
            self.assertTrue(started.wait(2))
            time.sleep(0.1)
            release.set()
            self.assertEqual(
                [f.result(timeout=3) for f in futures],
                [{"value": "ready"}] * 6,
            )
        self.assertEqual(len(calls), 1)

    def test_slow_request_does_not_block_other_cached_requests(self):
        self.read(self.cache, lambda: {"value": "cached"})
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            self.assertTrue(release.wait(3))
            return {"value": "slow"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(
                self.read,
                self.cache,
                slow,
                "agent_source",
                {**SOURCE, "file": "slow.c"},
            )
            try:
                self.assertTrue(started.wait(2))
                hit = pool.submit(
                    self.read, self.cache, Mock(side_effect=AssertionError)
                )
                self.assertEqual(hit.result(timeout=0.5), {"value": "cached"})
            finally:
                release.set()
            pending.result(timeout=3)

    def test_lease_renews_for_a_request_longer_than_its_initial_lifetime(self):
        other = self.make_cache()
        calls = []
        started = threading.Event()

        def slow():
            calls.append(1)
            started.set()
            time.sleep(0.4)
            return {"done": True}

        def short_lease(backend, key):
            return CacheLease(backend, key, seconds=0.15)

        with patch(
            "history_agent.history.cache.CacheLease", side_effect=short_lease
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.read, self.cache, slow)
                self.assertTrue(started.wait(2))
                time.sleep(0.2)
                second = pool.submit(self.read, other, slow)
                self.assertEqual(
                    first.result(timeout=3), second.result(timeout=3)
                )
        self.assertEqual(len(calls), 1)

    def test_live_heads_auth_writes_and_review_history_are_never_cached(self):
        for action, args in (
            ("get_warning", {**SOURCE, "snapshot_id": "HEAD"}),
            ("agent_source", {**SOURCE, "snapshot_id": "HEAD"}),
            ("agent_compare_warnings", {**SOURCE, "base_snapshot_id": "HEAD"}),
            ("get_snapshots", {"project_id": 0, "stream_id": "main"}),
            ("get_all_reviews", {"project_id": 0}),
            ("agent_set_review", {"project_id": 0}),
            ("auth", {"user": "user"}),
            ("get_tokens", {"project_id": 0, "file": "a.c"}),
        ):
            with self.subTest(action=action):
                load = Mock(return_value={"value": 1})
                self.read(self.cache, load, action, args)
                self.read(self.cache, load, action, args)
                self.assertEqual(load.call_count, 2)

    def test_invalidation_reaches_other_clients_and_preserves_source_cache(
        self,
    ):
        second = self.make_cache()
        source = Mock(return_value={"text": "immutable"})
        warnings = Mock(
            side_effect=[{"classification": "UD"}, {"classification": "TP"}]
        )
        self.read(self.cache, source)
        self.read(self.cache, warnings, "agent_find_warnings")
        second.invalidate()
        self.assertEqual(
            self.read(self.cache, warnings, "agent_find_warnings"),
            {"classification": "TP"},
        )
        self.read(self.cache, source)
        source.assert_called_once()

    def test_failed_reads_are_not_cached_and_locks_are_released(self):
        load = Mock(side_effect=[HistoryError("failed"), {"ok": True}])
        with self.assertRaises(HistoryError):
            self.read(self.cache, load)
        self.assertEqual(self.read(self.make_cache(), load), {"ok": True})
        self.assertEqual(load.call_count, 2)
        self.assertFalse(self.cache.backend.client.keys("*:flight"))

    def test_unavailable_redis_falls_back_without_repeated_connection_waits(
        self,
    ):
        self.server.connected = False
        load = Mock(return_value={"ok": True})
        with self.assertLogs("history_agent.history.redis_backend", "WARNING"):
            self.assertEqual(self.read(self.cache, load), {"ok": True})
        self.assertFalse(self.cache.backend.available)
        with patch.object(self.cache.backend.client, "get") as get:
            self.assertEqual(self.read(self.cache, load), {"ok": True})
            get.assert_not_called()
        load.assert_called_once()

    def test_corrupt_payload_and_expired_response_are_reloaded(self):
        key = self.cache.key("agent_source", SOURCE, None, "static")
        self.cache.backend.client.set(key, "broken-json")
        load = Mock(return_value={"ok": True})
        self.assertEqual(self.read(self.cache, load), {"ok": True})
        self.cache.backend.client.delete(key)
        self.assertEqual(self.read(self.make_cache(), load), {"ok": True})
        self.assertEqual(load.call_count, 2)

    def test_evicted_generation_does_not_revive_old_mutable_results(self):
        load = Mock(side_effect=[{"old": True}, {"new": True}])
        self.read(self.cache, load, "agent_find_warnings")
        self.cache.backend.client.delete(self.cache.generation_key)
        self.assertEqual(
            self.read(self.make_cache(), load, "agent_find_warnings"),
            {"new": True},
        )

    def test_invalidation_keeps_source_in_local_fallback_during_outage(self):
        self.server.connected = False
        load = Mock(return_value={"source": "ready"})
        with self.assertLogs("history_agent.history.redis_backend", "WARNING"):
            self.read(self.cache, load)
        self.cache.invalidate()
        self.read(self.cache, load)
        load.assert_called_once()

    def test_lost_lease_neither_publishes_nor_releases_another_owner(self):
        key = self.cache.key("agent_source", SOURCE, None, "static")

        def lost():
            self.cache.backend.client.set(key + ":flight", "new-owner", ex=10)
            return {"ok": True}

        self.read(self.cache, lost)
        self.assertIsNone(self.cache.backend.client.get(key))
        self.assertEqual(
            self.cache.backend.client.get(key + ":flight"), b"new-owner"
        )
