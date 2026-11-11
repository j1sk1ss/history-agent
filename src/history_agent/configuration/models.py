"""Named connections and model selection without storing API secrets."""

from __future__ import annotations

import re

from pydantic import Field, TypeAdapter, field_validator

from history_agent.configuration.connection import valid_url
from history_agent.contracts import StrictModel

TRANSPORTS = (
    "openai-compatible",
    "ollama",
    "llama",
    "deepseek",
    "openai-responses",
    "codex",
    "anthropic",
    "claude",
    "gemini",
)


class ModelOptions(StrictModel):
    label: str = Field(default="", max_length=120)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_context_bytes: int | None = Field(default=None, gt=0)
    max_tool_result_bytes: int | None = Field(default=None, gt=0)


class ProviderConnection(StrictModel):
    transport: str = "openai-compatible"
    base_url: str
    api_key_env: str | None = None
    models: dict[str, ModelOptions] = Field(min_length=1)

    @field_validator("transport")
    @classmethod
    def supported_transport(cls, value):
        if value not in TRANSPORTS:
            raise ValueError("Choose a supported API transport")
        return value

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        return valid_url(value)

    @field_validator("api_key_env")
    @classmethod
    def key_name(cls, value):
        if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Expected an API key environment variable name")
        return value

    @field_validator("models")
    @classmethod
    def model_names(cls, value):
        if any(not key.strip() or len(key) > 200 for key in value):
            raise ValueError(
                "Expected nonempty model IDs up to 200 characters"
            )
        return value


class ModelCatalog:
    @staticmethod
    def validate(values: dict) -> dict[str, ProviderConnection]:
        providers = TypeAdapter(dict[str, ProviderConnection]).validate_python(
            values
        )
        if any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key) for key in providers
        ):
            raise ValueError(
                "Provider names use letters, numbers, dots, hyphens"
            )
        return providers

    @staticmethod
    def select(values: dict, reference: str) -> dict:
        providers = ModelCatalog.validate(values)
        provider, separator, model = reference.partition("/")
        if (
            not separator
            or provider not in providers
            or model not in providers[provider].models
        ):
            raise ValueError(
                "Unknown model profile; choose a configured provider/model"
            )
        connection = providers[provider]
        options = connection.models[model].model_dump(exclude_none=True)
        options.pop("label", None)
        return {
            "provider": connection.transport,
            "model_url": connection.base_url,
            "model_key_env": connection.api_key_env,
            "model": model,
            **options,
        }

    @staticmethod
    def choices(values: dict) -> list[tuple[str, str]]:
        return [
            (f"{provider} / {options.label or model}", f"{provider}/{model}")
            for provider, connection in ModelCatalog.validate(values).items()
            for model, options in connection.models.items()
        ]

    @staticmethod
    def apply(args, explicit: set[str]):
        if args.model_profile:
            for field, value in ModelCatalog.select(
                args.providers, args.model_profile
            ).items():
                if field not in explicit:
                    setattr(args, field, value)
