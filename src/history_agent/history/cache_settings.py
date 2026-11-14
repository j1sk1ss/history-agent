"""Connection validation shared by saved settings and history clients."""

import math
from urllib.parse import urlsplit


def validate_redis_url(value):
    if not value:
        return
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("redis", "rediss")
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(
            "Redis URL must be redis:// or rediss://host:port/db"
        ) from None


def validate_history_limits(settings):
    validate_redis_url(settings.redis_url)
    for name in ("timeout", "cache_ttl", "source_cache_ttl"):
        value = getattr(settings, name)
        if (
            type(value) not in (int, float)
            or (name != "timeout" and type(value) is not int)
            or value <= 0
            or not math.isfinite(value)
        ):
            raise ValueError("History " + name + " must be positive")
