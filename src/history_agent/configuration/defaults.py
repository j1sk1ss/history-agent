"""Explicitly saved connection and chat settings in a private file."""

from __future__ import annotations

import json
import math
import os
import re
from argparse import Namespace
from pathlib import Path

from history_agent.configuration.capabilities import CapabilitySettings
from history_agent.configuration.connection import valid_url
from history_agent.configuration.files import private_write

PROVIDER_DEFAULTS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "llama": "http://127.0.0.1:8000/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openai-responses": "https://api.openai.com/v1",
    "codex": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
PROVIDER_KEYS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai-responses": "OPENAI_API_KEY",
    "codex": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
PROVIDERS: tuple[str, ...] = ("openai-compatible", *PROVIDER_DEFAULTS)
LOCAL_PROVIDERS: tuple[str, ...] = ("openai-compatible", "ollama", "llama")
TEXT_FIELDS: tuple[str, ...] = (
    "prompts_file",
    "provider",
    "model_url",
    "model",
    "model_key_env",
    "system_one_url",
    "system_one_model",
    "history_url",
    "history_user",
    "history_password",
)
BOOL_FIELDS: tuple[str, ...] = (
    "allow_write",
    "no_thinking",
    "no_stream",
    "no_stream_usage",
    "stats",
    "no_patterns",
)
LIMIT_FIELDS: tuple[str, ...] = (
    "max_steps",
    "max_output_tokens",
    "max_context_bytes",
    "max_tool_result_bytes",
    "timeout",
)
SAVED_FIELDS: tuple[str, ...] = (
    *TEXT_FIELDS,
    *BOOL_FIELDS,
    *LIMIT_FIELDS,
    "capabilities",
)


def config_path() -> Path:
    override: str | None = os.environ.get("HISTORY_AGENT_CONFIG")
    if override:
        return Path(override).expanduser()
    root: Path = Path(
        os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    )
    return root / "history-agent" / "config.json"


def validate_defaults(
    values: dict[str, str | bool | int | float | None],
) -> dict[str, str | bool | int | float | None]:
    if not isinstance(values, dict) or set(values) - set(SAVED_FIELDS):
        raise ValueError(
            "Expected chat settings; unknown configuration fields"
        )
    if "capabilities" in values:
        CapabilitySettings.model_validate(values["capabilities"])
    for name, value in values.items():
        if (
            name in TEXT_FIELDS
            and value is not None
            and not isinstance(value, str)
        ):
            raise ValueError(f"{name} must be text")
        if name in BOOL_FIELDS and type(value) is not bool:
            raise ValueError(f"{name} must be true or false")
        if name in LIMIT_FIELDS:
            expected: tuple[type[int] | type[float], ...] = (
                (int, float) if name == "timeout" else (int,)
            )
            if (
                type(value) not in expected
                or value <= 0
                or (name == "timeout" and not math.isfinite(value))
            ):
                raise ValueError(f"{name} must be positive")
    provider: str = values.get("provider", "openai-compatible")
    if provider not in PROVIDERS:
        raise ValueError("Unknown model provider")
    for name in ("model_url", "history_url", "system_one_url"):
        if values.get(name):
            valid_url(values[name])
    if values.get("model_key_env") and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", values["model_key_env"]
    ):
        raise ValueError(
            "API key environment variable must be a variable name"
        )
    if values.get("no_thinking") and provider not in LOCAL_PROVIDERS:
        raise ValueError(
            "Disable thinking only for local compatible providers"
        )
    if (values.get("history_user") is None) != (
        values.get("history_password") is None
    ):
        raise ValueError("Enter both the history server username and password")
    return values


def load_defaults() -> dict[str, str | bool | int | float | None]:
    path: Path = config_path()
    if not path.exists():
        return {}
    try:
        return validate_defaults(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"Cannot read chat configuration {path}: {error}"
        ) from None


def save_defaults(args: Namespace) -> None:
    # Commands, prompts, UI flags and job scope are never persisted.
    values: dict[str, str | bool | int | float | None] = validate_defaults(
        {name: getattr(args, name, None) for name in SAVED_FIELDS}
    )
    path: Path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    private_write(
        path,
        (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
