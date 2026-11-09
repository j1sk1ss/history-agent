"""Configure a model endpoint and a history MCP adapter."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from history_agent.configuration.clients import BUILDERS
from history_agent.configuration.connection import prepare
from history_agent.configuration.files import Edit, write_plan
from history_agent.configuration.setup_args import parse_args


def build_plan(args: argparse.Namespace) -> list[Edit]:
    prepare(args)
    executable: Path = Path(sys.executable)
    server_script: Path = Path(__file__).resolve().parents[3] / "mcp_server.py"
    history_config: Path = args.install_dir / "history.json"
    stdio: dict[str, object] = {
        "command": str(executable),
        "args": [str(server_script), "--transport", "stdio"],
        "env": {"HISTORY_CONFIG": str(history_config)},
    }
    key: str | None = os.environ.get(args.model_key_env)
    history: dict[str, str] = {"HISTORY_URL": args.history_url}
    for name in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION"):
        if name in os.environ:
            history[name] = os.environ[name]
    if not history_config.exists() and not any(
        n in history
        for n in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION")
    ):
        history.update(HISTORY_USER="", HISTORY_PASSWORD="")
    plan: list[Edit] = [Edit(history_config, "json", history)]
    return plan + BUILDERS[args.client](args, stdio, key)


def main(
    argv: list[str] | None = None,
    *,
    args: argparse.Namespace | None = None,
) -> None:
    args = args or parse_args(argv)
    try:
        plan: list[Edit] = build_plan(args)
        print(f"Client: {args.client}; model: {args.model}; API: {args.api}")
        print(f"Model endpoint: {args.model_endpoint}")
        print(f"History endpoint: {args.history_url}")
        if args.client == "claude-code":
            print(
                "Claude Code requires a Messages-compatible endpoint; "
                "Anthropic does not support non-Claude models."
            )
        if args.client == "codex":
            print(
                "Codex requires Responses API support; a Chat "
                "Completions-only endpoint cannot be used directly."
            )
        if args.dry_run:
            for edit in plan:
                print("Would configure:", edit.path)
            return
        if not (
            Path(__file__).resolve().parents[3] / "mcp_server.py"
        ).is_file():
            raise ValueError(
                "Keep configure.py, mcp_server.py and src in the same project"
                " checkout"
            )
        changed: list[Path]
        backups: list[Path]
        changed, backups = write_plan(plan)
        for path in changed:
            print("Configured:", path)
        for path in backups:
            print("Backup:", path)
        print(
            "Restart the selected client, enable history-mcp and request "
            "history_discover(action='workflow')."
        )
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
