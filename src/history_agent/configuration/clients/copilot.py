"""Build configuration for copilot."""

from __future__ import annotations

import os
import sys
from argparse import Namespace
from pathlib import Path

from history_agent.configuration.constants import SERVER_NAME
from history_agent.configuration.files import Edit, config_path


def vscode_directory(args: Namespace) -> Path:
    if args.vscode_user_dir:
        return args.vscode_user_dir.expanduser().resolve()
    if sys.platform == "darwin":
        return args.config_root / "Library/Application Support/Code/User"
    if os.name == "nt":
        return args.config_root / "AppData/Roaming/Code/User"
    return args.config_root / ".config/Code/User"


def build(
    args: Namespace,
    stdio: dict[str, object],
    key: str | None,
) -> list[Edit]:
    root: Path = args.config_root
    plan: list[Edit] = []
    model: dict[str, object] = {
        "id": args.model,
        "name": args.model,
        "url": args.model_endpoint,
        "toolCalling": True,
        "vision": False,
        "maxInputTokens": args.context_window - args.max_output_tokens,
        "maxOutputTokens": args.max_output_tokens,
        "apiType": args.api,
    }

    plan += [
        Edit(
            config_path(root, ".copilot/mcp-config.json"),
            "json",
            {
                "mcpServers": {
                    SERVER_NAME: {"type": "stdio", **stdio, "tools": ["*"]}
                }
            },
        ),
        Edit(
            vscode_directory(args) / "chatLanguageModels.json",
            "providers",
            {
                "name": "History agent model",
                "vendor": "customendpoint",
                "apiType": args.api,
                "apiKey": key or "",
                "models": [model],
            },
        ),
    ]
    return plan
