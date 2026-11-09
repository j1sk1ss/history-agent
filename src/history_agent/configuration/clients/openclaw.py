"""Build configuration for openclaw."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from history_agent.configuration.constants import PROVIDER_NAME, SERVER_NAME
from history_agent.configuration.files import Edit, config_path


def build(
    args: Namespace,
    stdio: dict[str, object],
    key: str | None,
) -> list[Edit]:
    root: Path = args.config_root
    plan: list[Edit] = []
    provider: dict[str, object] = {
        "baseUrl": args.model_base,
        "api": "openai-completions",
        "models": [
            {
                "id": args.model,
                "name": args.model,
                "input": ["text"],
                "reasoning": False,
                "contextWindow": args.context_window,
                "maxTokens": args.max_output_tokens,
            }
        ],
    }
    if key:
        provider["apiKey"] = "${" + args.model_key_env + "}"
    plan.append(
        Edit(
            config_path(root, ".openclaw/openclaw.json"),
            "json",
            {
                "models": {"providers": {PROVIDER_NAME: provider}},
                "agents": {
                    "defaults": {
                        "model": {"primary": PROVIDER_NAME + "/" + args.model}
                    }
                },
                "mcp": {
                    "servers": {
                        SERVER_NAME: {
                            **stdio,
                            "transport": "stdio",
                            "enabled": True,
                        }
                    }
                },
            },
        )
    )
    return plan
