"""One catalog for model instructions, with validated local overrides."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path

_OVERRIDES: ContextVar[dict[str, str]] = ContextVar("prompts", default={})


class Prompts:
    _defaults: dict[str, str] = json.loads(
        Path(__file__).with_name("defaults.json").read_text(encoding="utf-8")
    )

    @staticmethod
    def configure(path: str | None = None) -> None:
        values = Prompts.validate(path) if path else {}
        _OVERRIDES.set(values)

    @staticmethod
    def validate(path: str) -> dict[str, str]:
        values = json.loads(
            Path(path).expanduser().read_text(encoding="utf-8")
        )
        if (
            not isinstance(values, dict)
            or set(values) - Prompts._defaults.keys()
        ):
            raise ValueError("Unknown prompt names; see prompts/defaults.json")
        for key, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Prompt {key} must be nonempty text")
            expected = set(
                re.findall(r"\{([a-z_]+)\}", Prompts._defaults[key])
            )
            actual = set(re.findall(r"\{([a-z_]+)\}", value))
            if expected != actual:
                raise ValueError(
                    f"Prompt {key} must preserve {sorted(expected)}"
                )
        for key in values:
            Prompts._resolve(key, values, ())
        return values

    @staticmethod
    def get(key: str) -> str:
        return Prompts._resolve(key, _OVERRIDES.get(), ())

    @staticmethod
    def _resolve(
        key: str, values: dict[str, str], stack: tuple[str, ...]
    ) -> str:
        if key not in Prompts._defaults or key in stack:
            raise ValueError(f"Unknown or recursive prompt reference: {key}")
        return re.sub(
            r"\{\$([a-z_.]+)\}",
            lambda match: Prompts._resolve(match[1], values, (*stack, key)),
            values.get(key, Prompts._defaults[key]),
        )
