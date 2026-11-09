"""History connection settings and transport errors."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


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
    timeout: float = 20

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
        )
