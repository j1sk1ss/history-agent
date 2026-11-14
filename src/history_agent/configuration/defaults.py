"""Explicitly saved connection and chat settings in a private file."""

from __future__ import annotations

import json
import math
import os
from argparse import Namespace
from pathlib import Path

from pydantic import ValidationError

from history_agent.configuration.capabilities import CapabilitySettings
from history_agent.configuration.connection import valid_url
from history_agent.configuration.credentials import (
    PROVIDER_KEYS as PROVIDER_KEYS,
)
from history_agent.configuration.credentials import (
    REQUIRED_KEY_PROVIDERS as REQUIRED_KEY_PROVIDERS,
)
from history_agent.configuration.credentials import (
    model_api_key,
    validate_key,
    validate_key_env,
)
from history_agent.configuration.files import private_write
from history_agent.configuration.models import ModelCatalog

PROVIDER_DEFAULTS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "llama": "http://127.0.0.1:8000/v1",
    "litellm": "http://127.0.0.1:4000/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openai-responses": "https://api.openai.com/v1",
    "codex": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
PROVIDERS: tuple[str, ...] = ("openai-compatible", *PROVIDER_DEFAULTS)
LOCAL_PROVIDERS: tuple[str, ...] = ("openai-compatible", "ollama", "llama")
COMPATIBLE_PROVIDERS: tuple[str, ...] = (
    *LOCAL_PROVIDERS, "deepseek", "litellm",
)
TEXT_FIELDS: tuple[str, ...] = (
    "prompts_file",
    "model_profile",
    "provider",
    "model_url",
    "model",
    "model_api_key",
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
    "providers",
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
    if not isinstance(values, dict) or set(values) - {
        *SAVED_FIELDS, "model_key_env",
    }:
        raise ValueError(
            "Expected chat settings; unknown configuration fields"
        )
    if "capabilities" in values:
        try:
            CapabilitySettings.model_validate(values["capabilities"])
        except ValidationError as error:
            issue = error.errors(include_input=False)[0]
            raise ValueError(
                "Invalid agent tool settings: " + issue["msg"]
            ) from None
    if "providers" in values:
        ModelCatalog.validate(values["providers"])
    for name, value in values.items():
        if (
            name in (*TEXT_FIELDS, "model_key_env")
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
    if values.get("model_profile"):
        ModelCatalog.select(
            values.get("providers", {}), values["model_profile"]
        )
    provider: str = values.get("provider", "openai-compatible")
    if provider not in PROVIDERS:
        raise ValueError("Unknown model provider")
    for name in ("model_url", "history_url", "system_one_url"):
        if values.get(name):
            valid_url(values[name])
    validate_key(values.get("model_api_key"))
    validate_key_env(values.get("model_key_env"))
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
    values["model_api_key"] = model_api_key(args) or ""
    values["providers"] = ModelCatalog.for_storage(args.providers)
    values["capabilities"] = CapabilitySettings.model_validate(
        args.capabilities
    ).for_storage()
    path: Path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    private_write(
        path,
        (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
