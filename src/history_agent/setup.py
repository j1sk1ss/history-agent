"""
Install configuration for a model endpoint and the independent history MCP
adapter.

Client programs, inference servers and the history server are supplied by
the user. No model inference or warning mutations are performed during
installation.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CLIENTS = ("copilot", "opencode", "openclaw", "hermes", "claude-code", "codex")
SERVER_NAME = "history-mcp"
PROVIDER_NAME = "history-agent"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args(argv)
    args.config_root = args.config_root.expanduser().resolve()
    args.install_dir = (
        (args.install_dir or args.config_root / ".local/share/history-agent")
        .expanduser()
        .resolve()
    )
    return args


def valid_url(value):
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Use an HTTP(S) URL without credentials, query or fragment; "
            "put credentials in environment variables"
        )
    return value.rstrip("/")


def prepare(args):
    if getattr(args, "prepared", False):
        return
    args.history_url = valid_url(args.history_url)
    url = valid_url(args.model_url)
    expected = {"codex": "responses", "claude-code": "messages"}.get(
        args.client, "chat-completions"
    )
    args.api = expected if args.model_api == "auto" else args.model_api
    if args.client in ("codex", "claude-code") and args.api != expected:
        raise ValueError(
            f"{args.client} requires the {expected} API; "
            "provide a compatible endpoint/gateway"
        )
    if (
        args.client in ("opencode", "openclaw", "hermes")
        and args.api != "chat-completions"
    ):
        raise ValueError(
            f"This {args.client} setup supports chat-completions endpoints"
        )
    paths = {
        "chat-completions": "/chat/completions",
        "responses": "/responses",
        "messages": "/messages",
    }
    explicit = next(
        (kind for kind, suffix in paths.items() if url.endswith(suffix)), None
    )
    if explicit and explicit != args.api:
        raise ValueError(
            f"The supplied URL uses {explicit}, but {args.client} is "
            f"configured for {args.api}; supply a compatible endpoint"
        )
    base = url[: -len(paths[explicit])] if explicit else url
    if not re.search(r"/v\d+$", base):
        base += "/v1"
    args.model_base = base
    args.model_endpoint = base + paths[args.api]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.model_key_env):
        raise ValueError(
            "--model-key-env must be an environment variable name"
        )
    if (
        args.context_window <= args.max_output_tokens
        or args.max_output_tokens < 1
    ):
        raise ValueError(
            "The context window must be larger than the positive output "
            "token budget"
        )
    if not args.model:
        args.model = discover_model(base, args.model_key_env, args.api)
    if not args.model.strip() or any(ord(c) < 32 for c in args.model):
        raise ValueError(
            "Model ID must be non-empty and contain no control characters"
        )
    user, password = (
        os.environ.get("HISTORY_USER"),
        os.environ.get("HISTORY_PASSWORD"),
    )
    if (user is None) != (password is None):
        raise ValueError(
            "Set both HISTORY_USER and HISTORY_PASSWORD, or only "
            "HISTORY_SESSION"
        )
    args.prepared = True


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def discover_model(base, key_env, api="chat-completions"):
    headers = {"Accept": "application/json"}
    key = os.environ.get(key_env)
    if api == "messages":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(base + "/models", headers=headers)
    try:
        opener = urllib.request.build_opener(NoRedirects())
        with opener.open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        data = json.loads(raw)
        models = sorted(
            {
                m["id"]
                for m in data["data"]
                if isinstance(m.get("id"), str) and m["id"]
            }
        )
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        raise ValueError(
            "Cannot discover a model from /models; pass --model "
            "explicitly and check endpoint/authentication"
        ) from None
    if len(models) != 1:
        raise ValueError(
            "The endpoint must list exactly one model for automatic "
            "selection; pass --model explicitly"
        )
    return models[0]


def merge(original, patch):
    for name, value in patch.items():
        if (
            name in ("env", "environment")
            and isinstance(value, dict)
            and "HISTORY_CONFIG" in value
        ):
            # Migrate old inline history settings: leaving them here would
            # override the new config file.
            old_env = dict(original.get(name, {}))
            for key in (
                "HISTORY_URL",
                "HISTORY_USER",
                "HISTORY_PASSWORD",
                "HISTORY_SESSION",
                "HISTORY_CONFIG",
            ):
                old_env.pop(key, None)
            original[name] = {**old_env, **value}
            continue
        if name == "HISTORY_URL" and original.get(name) not in (None, value):
            # Credentials for one history service must not leak to a different
            # configured service.
            for key in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION"):
                original.pop(key, None)
        if isinstance(value, dict) and isinstance(
            original.get(name), MutableMapping
        ):
            merge(original[name], value)
        else:
            original[name] = copy.deepcopy(value)
    return original


@dataclass
class Edit:
    path: Path
    format: str
    patch: object


def config_path(root, name):
    # An explicitly supplied root is self-contained; do not accidentally touch
    # the real user profile.
    return root / name


def vscode_directory(args):
    if args.vscode_user_dir:
        return args.vscode_user_dir.expanduser().resolve()
    if sys.platform == "darwin":
        return args.config_root / "Library/Application Support/Code/User"
    if os.name == "nt":
        return args.config_root / "AppData/Roaming/Code/User"
    return args.config_root / ".config/Code/User"


def build_plan(args):
    prepare(args)
    root = args.config_root
    executable = Path(sys.executable)
    server_script = Path(__file__).resolve().parents[2] / "mcp_server.py"
    history_config = args.install_dir / "history.json"
    stdio = {
        "command": str(executable),
        "args": [str(server_script), "--transport", "stdio"],
        "env": {"HISTORY_CONFIG": str(history_config)},
    }
    key = os.environ.get(args.model_key_env)
    history = {"HISTORY_URL": args.history_url}
    for name in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION"):
        if name in os.environ:
            history[name] = os.environ[name]
    # Local single-project histories require a cookie login even with anonymous
    # credentials.
    if not history_config.exists() and not any(
        n in history
        for n in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION")
    ):
        history.update(HISTORY_USER="", HISTORY_PASSWORD="")
    plan = [Edit(history_config, "json", history)]
    model = {
        "id": args.model,
        "name": args.model,
        "url": args.model_endpoint,
        "toolCalling": True,
        "vision": False,
        "maxInputTokens": args.context_window - args.max_output_tokens,
        "maxOutputTokens": args.max_output_tokens,
        "apiType": args.api,
    }
    if args.client == "copilot":
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
    elif args.client == "opencode":
        path = config_path(root, ".config/opencode/opencode.json")
        if path.with_suffix(".jsonc").exists():
            path = path.with_suffix(".jsonc")
        legacy = args.opencode_format == "legacy"
        if args.opencode_format == "auto" and path.exists():
            data = read_data(path, "json")
            legacy = "provider" in data or (
                "mcp" in data and "servers" not in data["mcp"]
            )
        options = {"baseURL": args.model_base}
        if key:
            options["apiKey"] = "{env:" + args.model_key_env + "}"
        provider = {
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
        mcp = {
            "type": "local",
            "command": [str(executable), *stdio["args"]],
            "environment": stdio["env"],
        }
        if not legacy:
            provider["env"] = [args.model_key_env] if key else []
        patch = {
            "$schema": "https://opencode.ai/config.json",
            "model": PROVIDER_NAME + "/" + args.model,
            "provider" if legacy else "providers": {PROVIDER_NAME: provider},
            "mcp": {SERVER_NAME: mcp}
            if legacy
            else {"servers": {SERVER_NAME: mcp}},
        }
        plan.append(Edit(path, "json", patch))
    elif args.client == "openclaw":
        provider = {
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
                            "model": {
                                "primary": PROVIDER_NAME + "/" + args.model
                            }
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
    elif args.client == "hermes":
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
    elif args.client == "claude-code":
        env = {
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
    else:
        provider = {
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


def read_data(path, format):
    if not path.exists():
        return [] if format == "providers" else {}
    text = path.read_text(encoding="utf-8")
    try:
        if format in ("json", "providers"):
            try:
                return json.loads(text)
            except ValueError:
                import json5

                return json5.loads(text, allow_duplicate_keys=False)
        if format == "toml":
            import tomlkit

            return tomlkit.parse(text)
        if format == "yaml":
            from ruamel.yaml import YAML

            return YAML().load(text) or {}
    except Exception:
        raise ValueError(
            f"Cannot parse existing configuration: {path}; "
            "no files were changed"
        ) from None
    raise ValueError("Unsupported configuration format")


def render(edit):
    if edit.format == "dotenv":
        text = edit.path.read_text() if edit.path.exists() else ""
        for name, value in edit.patch.items():
            line = name + "=" + json.dumps(value, ensure_ascii=False)
            pattern = re.compile(
                r"^(?:export\s+)?" + re.escape(name) + r"=.*$", re.MULTILINE
            )
            text = (
                pattern.sub(lambda _: line, text)
                if pattern.search(text)
                else text.rstrip("\n") + "\n" + line + "\n"
            )
        return text
    data = read_data(edit.path, edit.format)
    if edit.format == "providers":
        if not isinstance(data, list):
            raise ValueError(f"Expected a provider array: {edit.path}")
        for i, provider in enumerate(data):
            if (
                provider.get("name") == edit.patch["name"]
                and provider.get("vendor") == edit.patch["vendor"]
            ):
                data[i] = merge(provider, edit.patch)
                break
        else:
            data.append(copy.deepcopy(edit.patch))
    else:
        if not isinstance(data, MutableMapping):
            raise ValueError(f"Expected a configuration object: {edit.path}")
        data = merge(data, edit.patch)
    if edit.format == "toml":
        import tomlkit

        return tomlkit.dumps(data)
    if edit.format == "yaml":
        from ruamel.yaml import YAML

        stream = io.StringIO()
        YAML().dump(data, stream)
        return stream.getvalue()
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_plan(plan):
    # Render every file first: a malformed existing config must not leave a
    # partial setup.
    rendered = [(edit.path, render(edit)) for edit in plan]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    changed = []
    backups = []
    try:
        for path, text in rendered:
            old = path.read_bytes() if path.exists() else None
            if old == text.encode("utf-8"):
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if old is not None:
                backup = path.with_name(
                    path.name + ".history-agent-" + stamp + ".bak"
                )
                private_write(backup, old)
                backups.append(backup)
            private_write(path, text.encode("utf-8"))
            changed.append((path, old))
    except BaseException:
        for path, old in reversed(changed):
            if old is None:
                path.unlink()
            else:
                private_write(path, old)
        raise
    return [path for path, _ in changed], backups


def private_write(path, content):
    # Replace atomically; neither an incomplete config nor credentials are
    # exposed through broad permissions.
    fd, temporary = tempfile.mkstemp(prefix=".history-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None, *, args=None):
    args = args or parse_args(argv)
    try:
        plan = build_plan(args)
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
            Path(__file__).resolve().parents[2] / "mcp_server.py"
        ).is_file():
            raise ValueError(
                "Keep configure.py, mcp_server.py and src in the same project"
                " checkout"
            )
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
