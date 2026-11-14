"""Bounded Redis operations with a circuit breaker and owned leases."""

import logging
import threading
import time
import uuid

from redis import Redis, RedisError
from redis.backoff import NoBackoff
from redis.retry import Retry

RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
PUBLISH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('set', KEYS[2], ARGV[2], 'EX', ARGV[3])
    redis.call('del', KEYS[1])
    return 1
end
return 0
"""


class RedisBackend:
    def __init__(self, url: str, *, client=None) -> None:
        self.client = client or (
            Redis.from_url(
                url,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                retry=Retry(NoBackoff(), 0),
                decode_responses=False,
            )
            if url
            else None
        )
        self.until = 0.0
        self._state_lock = threading.Lock()
        self.warned = False

    @property
    def available(self):
        return self.client is not None and time.monotonic() >= self.until

    def call(self, method, *args, **kwargs):
        if not self.available:
            return None
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except (RedisError, OSError, ValueError):
            with self._state_lock:
                self.until = time.monotonic() + 30
                if not self.warned:
                    logging.getLogger(__name__).warning(
                        "Redis cache unavailable; using bounded local cache. "
                        "Redis will be checked again in 30 seconds."
                    )
                    self.warned = True
            return None

    def generation(self, key):
        if not self.available:
            return None
        value = self.call("get", key)
        if value is not None:
            return value.decode() if isinstance(value, bytes) else value
        if not self.available:
            return None
        # A random generation prevents old entries reviving after eviction.
        self.call("set", key, uuid.uuid4().hex, nx=True)
        value = self.call("get", key)
        return value.decode() if isinstance(value, bytes) else value
