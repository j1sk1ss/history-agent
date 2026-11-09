"""Build configuration for opencode."""

from __future__ import annotations

import sys
from argparse import Namespace
from collections.abc import MutableMapping
from pathlib import Path

from history_agent.configuration.constants import PROVIDER_NAME, SERVER_NAME
from history_agent.configuration.files import Edit, config_path, read_data


def build(
    args: Namespace, stdio: dict[str, object], key: str | None,
) -> list[Edit]:
    root: Path = args.config_root
    executable: Path = Path(sys.executable)
    plan: list[Edit] = []
    path: Path = config_path(root, ".config/opencode/opencode.json")
    if path.with_suffix(".jsonc").exists():
        path = path.with_suffix(".jsonc")
    legacy: bool = args.opencode_format == "legacy"
    if args.opencode_format == "auto" and path.exists():
        data: MutableMapping[str, object] | list[dict[str, object]] = (
            read_data(path, "json")
        )
        legacy = "provider" in data or (
            "mcp" in data and "servers" not in data["mcp"]
        )
    options: dict[str, str] = {"baseURL": args.model_base}
    if key:
        options["apiKey"] = "{env:" + args.model_key_env + "}"
    provider: dict[str, object] = {
        "name": "History agent model",
        "models": {args.model: {"name": args.model}},
    }
    if legacy:
        provider.update(npm="@ai-sdk/openai-compatible", options=options)
    else:
        provider.update(
            package="@opencode/ai/providers/openai-compatible",
            settings=options,
        )
    mcp: dict[str, object] = {
        "type": "local",
        "command": [str(executable), *stdio["args"]],
        "environment": stdio["env"],
    }
    if not legacy:
        provider["env"] = [args.model_key_env] if key else []
    patch: dict[str, object] = {
        "$schema": "https://opencode.ai/config.json",
        "model": PROVIDER_NAME + "/" + args.model,
        "provider" if legacy else "providers": {PROVIDER_NAME: provider},
        "mcp": {SERVER_NAME: mcp}
        if legacy
        else {"servers": {SERVER_NAME: mcp}},
    }
    plan.append(Edit(path, "json", patch))
    return plan
