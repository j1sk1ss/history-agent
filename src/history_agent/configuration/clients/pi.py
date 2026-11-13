"""Build configuration for pi."""

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
    # Pi is a lightweight harness used only for annotation: connect the
    # shared history MCP server; Pi manages its own models and auth.
    plan.append(
        Edit(
            config_path(root, ".pi/agent/mcp.json"),
            "json",
            {
                "mcpServers": {
                    SERVER_NAME: {
                        **stdio,
                        "description": (
                            "Svace warning history: discover operations, "
                            "read warnings, traces and source, and save "
                            "TP/FP/WF/UC/UD reviews"
                        ),
                    }
                }
            },
        )
    )
    return plan
