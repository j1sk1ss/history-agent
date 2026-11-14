"""Direct API keys with compatibility for older environment references."""

import os
import re
from argparse import Namespace

PROVIDER_KEYS: dict[str, str] = {
    "litellm": "LITELLM_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai-responses": "OPENAI_API_KEY",
    "codex": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
REQUIRED_KEY_PROVIDERS: frozenset[str] = frozenset(PROVIDER_KEYS) - {"litellm"}


def validate_key(value: str | None) -> str | None:
    if value is not None and any(ord(char) < 32 for char in value):
        raise ValueError("API keys must not contain control characters")
    return value.strip() if value is not None else None


def validate_key_env(value: str | None) -> str | None:
    if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Expected an API key environment variable name")
    return value


def resolve_api_key(value: str | None, legacy_env: str | None = None):
    # An explicitly empty key disables authentication, including env fallback.
    if value is not None:
        return validate_key(value) or None
    validate_key_env(legacy_env)
    return validate_key(os.environ.get(legacy_env)) if legacy_env else None


def provider_api_key(
    provider: str, value: str | None, legacy_env: str | None = None,
) -> str | None:
    return resolve_api_key(
        value,
        legacy_env or PROVIDER_KEYS.get(
            provider, "HISTORY_AGENT_MODEL_API_KEY"
        ),
    )


def model_api_key(args: Namespace) -> str | None:
    return provider_api_key(
        args.provider, getattr(args, "model_api_key", None),
        getattr(args, "model_key_env", None),
    )
