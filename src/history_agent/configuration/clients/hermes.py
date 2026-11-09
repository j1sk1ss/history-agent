"""Build configuration for hermes."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from history_agent.configuration.constants import SERVER_NAME
from history_agent.configuration.files import Edit, config_path


def build(
    args: Namespace, stdio: dict[str, object], key: str | None,
) -> list[Edit]:
    root: Path = args.config_root
    plan: list[Edit] = []
    plan.append(
        Edit(
            config_path(root, ".hermes/config.yaml"),
            "yaml",
            {
                "model": {
                    "default": args.model,
                    "provider": "custom",
                    "base_url": args.model_base,
                },
                "mcp_servers": {SERVER_NAME: stdio},
            },
        )
    )
    plan.append(
        Edit(
            config_path(root, ".hermes/.env"),
            "dotenv",
            {"OPENAI_API_KEY": key or ""},
        )
    )
    return plan
