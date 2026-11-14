"""Validated host policy for questions, documentation and language servers."""

from __future__ import annotations

import ipaddress
import re

from pydantic import Field, field_validator

from history_agent.configuration.credentials import (
    resolve_api_key,
    validate_key,
    validate_key_env,
)
from history_agent.contracts import StrictModel

DEFAULT_LIBRARIES = {
    "requests": ["requests.readthedocs.io"],
    "numpy": ["numpy.org"],
    "pandas": ["pandas.pydata.org"],
    "sqlalchemy": ["docs.sqlalchemy.org"],
    "pydantic": ["docs.pydantic.dev"],
    "lodash": ["lodash.com"],
    "react": ["react.dev"],
    "libuv": ["docs.libuv.org"],
    "boost": ["www.boost.org", "boost.org"],
    "lua": ["www.lua.org"],
}


class DocumentationSettings(StrictModel):
    enabled: bool = True
    api_key: str | None = Field(default=None, repr=False)
    api_key_env: str | None = Field(
        default="BRAVE_SEARCH_API_KEY", exclude=True, repr=False
    )
    libraries: dict[str, list[str]] = Field(
        default_factory=lambda: {
            k: list(v) for k, v in DEFAULT_LIBRARIES.items()
        }
    )

    @field_validator("api_key_env")
    @classmethod
    def environment_name(cls, value: str) -> str:
        return validate_key_env(value)

    @field_validator("api_key")
    @classmethod
    def key_value(cls, value):
        return validate_key(value)

    @field_validator("libraries")
    @classmethod
    def public_hosts(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, hosts in value.items():
            if not re.fullmatch(r"[a-z0-9_.+-]{1,80}", name) or not hosts:
                raise ValueError(
                    "Each library needs a name and documentation hosts"
                )
            for host in hosts:
                if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
                    raise ValueError(
                        "Documentation hosts must be public DNS names"
                    )
                if host.endswith((".local", ".localhost", ".internal")):
                    raise ValueError(
                        "Local documentation hosts are not allowed"
                    )
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    continue
                raise ValueError("IP addresses are not documentation hosts")
        return value


class LanguageServerSettings(StrictModel):
    enabled: bool = False
    timeout: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    commands: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "lua": ["lua-language-server"],
            "c": ["clangd", "--background-index=false"],
            "cpp": ["clangd", "--background-index=false"],
            "python": ["pylsp"],
            "java": ["jdtls"],
            "javascript": ["typescript-language-server", "--stdio"],
        }
    )

    @field_validator("commands")
    @classmethod
    def known_languages(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        supported = {"lua", "c", "cpp", "python", "java", "javascript"}
        if set(value) - supported or any(
            not args or any(not arg.strip() for arg in args)
            for args in value.values()
        ):
            raise ValueError(
                "Expected supported languages and nonempty argv lists"
            )
        return value


class CapabilitySettings(StrictModel):
    questions: bool = True
    documentation: DocumentationSettings = Field(
        default_factory=DocumentationSettings
    )
    lsp: LanguageServerSettings = Field(default_factory=LanguageServerSettings)

    def for_storage(self) -> dict:
        values = self.model_dump()
        values["documentation"]["api_key"] = resolve_api_key(
            self.documentation.api_key, self.documentation.api_key_env
        ) or ""
        return values
