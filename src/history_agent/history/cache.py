"""Redis read-through cache, local fallback and shared in-flight requests."""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future

from history_agent.history.cache_lease import CacheLease
from history_agent.history.cache_policy import digest, policy
from history_agent.history.redis_backend import PUBLISH, RedisBackend
from history_agent.history.settings import HistoryError


class ResponseCache:
    def __init__(self, settings, endpoint, *, backend=None) -> None:
        self.settings = settings
        self.prefix = "history-agent:v1:" + digest(endpoint) + ":"
        self.identity = digest(
            [
                settings.user,
                settings.password,
                settings.session,
            ]
        )
        self.backend = backend or RedisBackend(settings.redis_url)
        self.generation_key = self.prefix + "review-generation"
        self.local_generation = uuid.uuid4().hex
        self.dirty = False
        self.entries = OrderedDict()
        self.bytes = 0
        self.flights: dict[str, Future] = {}
        self.lock = threading.Lock()

    def invalidate(self):
        with self.lock:
            self.local_generation = uuid.uuid4().hex
            # Old mutable entries become unreachable through their generation;
            # keep immutable L1 evidence available even during Redis outages.
            self.dirty = True
        if self.backend.call("set", self.generation_key, uuid.uuid4().hex):
            self.dirty = False

    def key(self, action, query, body, kind):
        generation = "static"
        if kind == "reviews":
            if self.dirty and self.backend.available:
                self.invalidate()
            generation = self.backend.generation(self.generation_key)
            if generation is None:
                generation = self.local_generation
        return self.prefix + digest(
            [
                self.identity,
                generation,
                action,
                (policy(action, query or {}, self.settings) or ("", 0))[1],
                {
                    k: str(v).casefold() if k == "checker_name" else str(v)
                    for k, v in (query or {}).items()
                },
                {k: str(v) for k, v in (body or {}).items()},
            ]
        )

    def read(self, action, query, body, load):
        selected = policy(action, query or {}, self.settings)
        if not selected:
            return load()
        kind, ttl = selected
        key = self.key(action, query, body, kind)
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > time.monotonic():
                self.entries.move_to_end(key)
                return json.loads(entry[1])
            future = self.flights.get(key)
            owner = future is None
            if owner:
                future = self.flights[key] = Future()
        if not owner:
            return copy.deepcopy(future.result())
        try:
            result = self.shared(key, ttl, load)
            local_ttl = (
                min(1, ttl)
                if kind == "reviews" and self.backend.available
                else ttl
            )
            self.remember(key, result, local_ttl)
            future.set_result(result)
            return copy.deepcopy(result)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.flights.pop(key, None)

    def shared(self, key, ttl, load):
        deadline = time.monotonic() + self.settings.timeout * 4 + 30
        delay = 0.02
        lease = CacheLease(self.backend, key + ":flight")
        while self.backend.available:
            raw = self.backend.call("get", key)
            if raw is not None:
                try:
                    result = json.loads(raw)
                    if isinstance(result, dict):
                        return result
                except (ValueError, UnicodeError):
                    pass
                self.backend.call("delete", key)
            if lease.acquire():
                try:
                    # Another process may have published since our first GET.
                    raw = self.backend.call("get", key)
                    if raw is not None:
                        try:
                            cached = json.loads(raw)
                            if isinstance(cached, dict):
                                return cached
                        except (ValueError, UnicodeError):
                            pass
                        self.backend.call("delete", key)
                    result = load()
                    if not lease.lost.is_set():
                        self.backend.call(
                            "eval",
                            PUBLISH,
                            2,
                            lease.key,
                            key,
                            lease.token,
                            json.dumps(result, ensure_ascii=False),
                            ttl,
                        )
                    return result
                finally:
                    lease.close()
            if time.monotonic() >= deadline:
                raise HistoryError(
                    "An identical history request is still running; "
                    "retry the same request later"
                )
            time.sleep(delay)
            delay = min(0.25, delay * 1.5)
        return load()

    def remember(self, key, result, ttl):
        raw = json.dumps(result, ensure_ascii=False).encode()
        if len(raw) > 16 * 1024 * 1024:
            return
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous:
                self.bytes -= len(previous[1])
            self.entries[key] = (time.monotonic() + ttl, raw)
            self.bytes += len(raw)
            while self.bytes > 32 * 1024 * 1024 or len(self.entries) > 256:
                _, (_, evicted) = self.entries.popitem(last=False)
                self.bytes -= len(evicted)
