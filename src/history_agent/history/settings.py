"""History connection settings and transport errors."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from history_agent.history.cache_settings import validate_history_limits


class HistoryError(Exception):
    def __init__(self, message: str, *, auth_error: bool = False) -> None:
        super().__init__(message)
        self.auth_error: bool = auth_error


@dataclass(frozen=True)
class Settings:
    url: str
    user: str | None = None
    password: str | None = None
    session: str | None = None
    timeout: float = 600
    redis_url: str = field(
        default_factory=lambda: os.environ.get(
            "HISTORY_REDIS_URL",
            "redis://127.0.0.1:6379/0",
        )
    )
    cache_ttl: int = 300
    source_cache_ttl: int = 86400

    def __post_init__(self):
        validate_history_limits(self)

    @classmethod
    def from_env(cls) -> Settings:
        config: dict[str, str | None] = {}
        path: str | None = os.environ.get("HISTORY_CONFIG")
        if path:
            try:
                with open(path, encoding="utf-8") as source:
                    config = json.load(source)
                allowed: set[str] = {
                    "HISTORY_URL",
                    "HISTORY_USER",
                    "HISTORY_PASSWORD",
                    "HISTORY_SESSION",
                    "HISTORY_TIMEOUT",
                    "HISTORY_REDIS_URL",
                    "HISTORY_CACHE_TTL",
                    "HISTORY_SOURCE_CACHE_TTL",
                }
                if (
                    not isinstance(config, dict)
                    or set(config) - allowed
                    or any(
                        value is not None and not isinstance(value, str)
                        for value in config.values()
                    )
                ):
                    raise ValueError()
            except (OSError, ValueError) as error:
                raise ValueError(
                    "Cannot read valid history settings from HISTORY_CONFIG"
                ) from error

        def value(name: str, default: str | None = None) -> str | None:
            return (
                os.environ[name]
                if name in os.environ
                else config.get(name, default)
            )

        return cls(
            url=value("HISTORY_URL", "http://127.0.0.1:8060"),
            user=value("HISTORY_USER"),
            password=value("HISTORY_PASSWORD"),
            session=value("HISTORY_SESSION"),
            timeout=float(value("HISTORY_TIMEOUT", "600")),
            redis_url=value("HISTORY_REDIS_URL", "redis://127.0.0.1:6379/0"),
            cache_ttl=int(value("HISTORY_CACHE_TTL", "300")),
            source_cache_ttl=int(value("HISTORY_SOURCE_CACHE_TTL", "86400")),
        )
