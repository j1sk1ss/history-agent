from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import replace

from mcp import Client

from history_agent.configuration.connection import discover_model, valid_url
from history_agent.configuration.defaults import (
    COMPATIBLE_PROVIDERS,
    LOCAL_PROVIDERS,
    PROVIDER_DEFAULTS,
    PROVIDER_KEYS,
    REQUIRED_KEY_PROVIDERS,
    load_defaults,
)
from history_agent.history.client import HistoryClient, Settings
from history_agent.inference.model import ChatError, Model
from history_agent.interfaces.arguments import parse_args
from history_agent.interfaces.mcp import create_server
from history_agent.prompts import Prompts
from history_agent.runtime.session import converse


def model_base(url: str) -> str:
    base: str = valid_url(url)
    if base.endswith(("/responses", "/messages")):
        raise ValueError("history-chat requires a Chat Completions endpoint")
    base = base.removesuffix("/chat/completions")
    return base if re.search(r"/v\d+$", base) else base + "/v1"


def history_settings(args: argparse.Namespace) -> Settings:
    settings: Settings = Settings.from_env()
    if args.history_url:
        settings = replace(settings, url=args.history_url)
    if args.history_user is not None and args.history_password is not None:
        settings = replace(
            settings,
            user=args.history_user,
            password=args.history_password,
            session=None,
        )
    if (
        settings.user is None
        and settings.password is None
        and not settings.session
    ):
        settings = replace(settings, user="", password="")
    return settings


async def run(
    args: argparse.Namespace,
) -> dict[str, argparse.Namespace | str | None] | str | None:
    Prompts.configure(getattr(args, "prompts_file", None))
    url: str | None = args.model_url or PROVIDER_DEFAULTS.get(args.provider)
    if not url:
        raise ChatError(
            "Specify --model-url for an OpenAI-compatible inference server"
        )
    if args.provider in COMPATIBLE_PROVIDERS:
        base: str = model_base(url)
    else:
        base = valid_url(url).rstrip("/")
        if base.endswith("/messages") or base.endswith("/responses"):
            base = base.rsplit("/", 1)[0]
    env: str = args.model_key_env or PROVIDER_KEYS.get(
        args.provider, "HISTORY_AGENT_MODEL_API_KEY"
    )
    key: str | None = os.environ.get(env)
    if args.provider in REQUIRED_KEY_PROVIDERS and not key:
        raise ChatError(f"{args.provider} requires an API key in {env}")
    if args.provider not in LOCAL_PROVIDERS:
        if not args.model:
            raise ChatError(f"Specify --model for provider {args.provider}")
        name: str = args.model
    else:
        name = args.model or await asyncio.to_thread(discover_model, base, env)
    if args.no_thinking and args.provider not in LOCAL_PROVIDERS:
        raise ChatError(
            "--no-thinking only applies to compatible local chat-template"
            " servers"
        )
    settings: Settings = history_settings(args)
    model: Model = Model(
        base,
        name,
        key,
        args.timeout,
        args.max_output_tokens,
        args.no_thinking,
        stream=not args.no_stream,
        stream_usage=not args.no_stream_usage,
        provider=args.provider,
    )

    failure: ChatError | None = None
    history: HistoryClient = HistoryClient(settings)
    async with Client(create_server(history)) as client:
        try:
            use_ui: bool = (
                args.ui
                if args.ui is not None
                else sys.stdin.isatty() and sys.stdout.isatty()
            )
            if use_ui and not args.legacy_ui:
                try:
                    from history_agent.ui.runner import run_tui
                except ModuleNotFoundError as error:
                    if error.name != "textual":
                        raise
                    raise ChatError(
                        "Install UI dependencies: python3 -m pip install -r "
                        "requirements.txt; "
                        "or use --plain"
                    ) from None
                return await run_tui(client, model, args, history)
            else:
                await converse(client, model, args, history)
        except (ChatError, ValueError, OSError) as error:
            failure = error
    if failure:
        raise failure


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        defaults: dict[str, str | bool | int | float | None] = (
            load_defaults()
            if not any(flag in argv for flag in ("-h", "--help"))
            else {}
        )
        args: argparse.Namespace | None = parse_args(argv, defaults=defaults)
        configure: bool = args.configure or (not argv and not defaults)
        while True:
            if configure:
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise ChatError(
                        "Open settings in a terminal with python3 chat.py "
                        "--configure, "
                        "or pass --model-url and --model for a one-off run"
                    )
                try:
                    from history_agent.ui.config import ConfigurationApp
                except ModuleNotFoundError as error:
                    if error.name != "textual":
                        raise
                    raise ChatError(
                        "Install UI dependencies: python3 -m pip install -r "
                        "requirements.txt"
                    ) from None
                args = ConfigurationApp(args).run()
                if args is None:
                    return
                configure: bool = False
            result: dict[str, argparse.Namespace | str | None] | str | None = (
                asyncio.run(run(args))
            )
            if isinstance(result, dict) and "reload_job" in result:
                if result["reload_job"]:
                    args.resume = result["reload_job"]
                    args.prompt = args.batch = args.diff = None
                    args.subset = args.base_project = args.base_branch = None
                continue
            if isinstance(result, dict) and "settings" in result:
                args = result["settings"]
                configure = False
            elif result == "configure":
                configure = True
            else:
                return
            # Reopening settings starts a chat; do not replay a completed CLI
            # job.
            args.prompt = args.batch = args.diff = args.resume = None
            args.subset = args.base_project = args.base_branch = None
            args.overwrite_reviews = False
    except KeyboardInterrupt:
        print(
            "Interrupted. Inspect any pending review write before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except (ChatError, ValueError, OSError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
