"""Build configuration for claude-code."""

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
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": args.model_base.removesuffix("/v1"),
        "ANTHROPIC_MODEL": args.model,
        "ANTHROPIC_AUTH_TOKEN": key or "no-key-required",
        "ANTHROPIC_API_KEY": "",
    }
    plan += [
        Edit(
            config_path(root, ".claude.json"),
            "json",
            {"mcpServers": {SERVER_NAME: {"type": "stdio", **stdio}}},
        ),
        Edit(
            config_path(root, ".claude/settings.json"),
            "json",
            {"env": env, "model": args.model},
        ),
    ]
    return plan
