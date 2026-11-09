"""Build configuration for codex."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from history_agent.configuration.constants import PROVIDER_NAME, SERVER_NAME
from history_agent.configuration.files import Edit, config_path


def build(
    args: Namespace, stdio: dict[str, object], key: str | None,
) -> list[Edit]:
    root: Path = args.config_root
    plan: list[Edit] = []
    provider: dict[str, object] = {
        "name": "History agent model",
        "base_url": args.model_base,
        "wire_api": "responses",
    }
    if key:
        provider["env_key"] = args.model_key_env
    plan.append(
        Edit(
            config_path(root, ".codex/config.toml"),
            "toml",
            {
                "model": args.model,
                "model_provider": PROVIDER_NAME,
                "model_providers": {PROVIDER_NAME: provider},
                "mcp_servers": {SERVER_NAME: stdio},
            },
        )
    )
    return plan
