"""Arguments for configuring external agent clients."""

from __future__ import annotations

import argparse
from pathlib import Path

from history_agent.configuration.constants import CLIENTS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--client",
        "--type",
        required=True,
        type=lambda s: s.lower().replace(" ", "-"),
        choices=CLIENTS,
    )
    parser.add_argument(
        "--model-url",
        required=True,
        help="Model API origin, versioned base URL, or full endpoint",
    )
    parser.add_argument(
        "--history-url",
        "--server-url",
        required=True,
        help="Local or remote history HTTP(S) URL",
    )
    parser.add_argument(
        "--model",
        help="Model ID; detected from /models when the endpoint lists "
        "exactly one",
    )
    parser.add_argument(
        "--model-api",
        choices=("auto", "chat-completions", "responses", "messages"),
        default="auto",
    )
    parser.add_argument(
        "--model-key-env",
        default="HISTORY_AGENT_MODEL_API_KEY",
        help="Environment variable containing the model API key",
    )
    parser.add_argument("--context-window", type=int, default=32768)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument(
        "--config-root",
        type=Path,
        default=Path.home(),
        help="User home to configure; also useful for previews/tests",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        help="Dependency environment and connection settings directory "
        "(default: CONFIG_ROOT/.local/share/history-agent)",
    )
    parser.add_argument(
        "--vscode-user-dir",
        type=Path,
        help="Override VS Code User directory, for profiles/portable "
        "installations",
    )
    parser.add_argument(
        "--opencode-format",
        choices=("auto", "current", "legacy"),
        default="auto",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show files and non-secret endpoints; do not install packages"
        " or write files",
    )
    args: argparse.Namespace = parser.parse_args(argv)
    args.config_root = args.config_root.expanduser().resolve()
    args.install_dir = (
        (args.install_dir or args.config_root / ".local/share/history-agent")
        .expanduser()
        .resolve()
    )
    return args
