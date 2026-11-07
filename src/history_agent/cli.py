from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

from mcp import Client

from .client import HistoryClient, Settings
from .config import (
    LIMIT_FIELDS,
    LOCAL_PROVIDERS,
    PROVIDER_DEFAULTS,
    PROVIDER_KEYS,
    PROVIDERS,
    load_defaults,
    validate_defaults,
)
from .model import ChatError, Model
from .server import create_server
from .session import converse
from .setup import discover_model, valid_url


def model_base(url):
    base = valid_url(url)
    if base.endswith(("/responses", "/messages")):
        raise ValueError("history-chat requires a Chat Completions endpoint")
    base = base.removesuffix("/chat/completions")
    return base if re.search(r"/v\d+$", base) else base + "/v1"


def parse_args(argv=None, *, defaults=None):
    parser = argparse.ArgumentParser(
        description="Code review harness for local and hosted models"
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Open settings and explicitly save chat defaults",
    )
    parser.add_argument(
        "--provider",
        default="openai-compatible",
        choices=PROVIDERS,
        help="Inference transport: OpenAI-compatible, Responses (Codex), "
        "Anthropic or Gemini",
    )
    parser.add_argument(
        "--model-url",
        help="Provider API base; defaults to official API or local service",
    )
    parser.add_argument(
        "--model", help="Model ID; auto-detected if /models lists exactly one"
    )
    parser.add_argument(
        "--model-key-env",
        help="Environment variable containing inference API key; provider "
        "default if omitted",
    )
    parser.add_argument(
        "--history-url",
        help="Override HISTORY_URL/HISTORY_CONFIG; supports remote HTTP(S)",
    )
    parser.add_argument(
        "--prompt",
        help="One request, then exit; otherwise start an interactive chat",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        default=True,
        help="Save each validated batch review immediately (default)",
    )
    parser.add_argument(
        "--read-only",
        dest="allow_write",
        action="store_false",
        help="Disable review writes, overriding saved settings",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=12,
        help="Model requests per user turn",
    )
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        default=65536,
        help="JSON input budget, not tokenizer tokens",
    )
    parser.add_argument("--max-tool-result-bytes", type=int, default=16384)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Send Qwen/llama-server enable_thinking=false template option",
    )
    parser.add_argument(
        "--thinking",
        dest="no_thinking",
        action="store_false",
        help="Use the model default thinking mode",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print request time, input bytes and reported token/cache usage",
    )
    parser.add_argument("--no-stats", dest="stats", action="store_false")
    display = parser.add_mutually_exclusive_group()
    display.add_argument(
        "--ui",
        dest="ui",
        action="store_true",
        help="Force the live terminal interface",
    )
    display.add_argument(
        "--plain",
        dest="ui",
        action="store_false",
        help="Plain output for scripts/pipes",
    )
    display.add_argument(
        "--legacy-ui", action="store_true", help=argparse.SUPPRESS
    )
    parser.set_defaults(ui=None, history_user=None, history_password=None)
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Use non-streaming JSON for servers without SSE support",
    )
    parser.add_argument("--stream", dest="no_stream", action="store_false")
    parser.add_argument(
        "--no-stream-usage",
        action="store_true",
        help="Omit stream_options for servers that reject include_usage",
    )
    parser.add_argument(
        "--stream-usage", dest="no_stream_usage", action="store_false"
    )
    job = parser.add_mutually_exclusive_group()
    job.add_argument(
        "--batch",
        nargs=3,
        metavar=("PROJECT", "BRANCH", "SNAPSHOT"),
        help="Review all warnings in an isolated-context job",
    )
    job.add_argument(
        "--diff",
        nargs=4,
        metavar=("PROJECT", "BRANCH", "CURRENT", "BASE"),
        help="Review a snapshot difference; default appeared (UI First)",
    )
    job.add_argument(
        "--resume",
        metavar="JOB_ID",
        help="Resume a checkpoint using its original scope and write mode",
    )
    parser.add_argument(
        "--subset",
        choices=("appeared", "disappeared", "both"),
        default=None,
        help="Difference subset for --diff; defaults to appeared",
    )
    parser.add_argument(
        "--base-project",
        type=int,
        help="Base project for --diff; defaults to current",
    )
    parser.add_argument(
        "--base-branch", help="Base branch for --diff; defaults to current"
    )
    parser.add_argument(
        "--batch-instructions",
        default="",
        help="Project-specific rules to retain across batch context resets",
    )
    parser.add_argument(
        "--batch-state-dir",
        type=Path,
        default=Path.home() / ".local/state/history-agent/jobs",
    )
    parser.add_argument(
        "--overwrite-reviews",
        action="store_true",
        help="Permit replacing existing reviews in explicitly selected "
        "batch jobs",
    )
    parser.add_argument(
        "--no-patterns",
        action="store_true",
        help="Disable checker-local reusable review checklists",
    )
    parser.add_argument("--patterns", dest="no_patterns", action="store_false")
    parser.set_defaults(**validate_defaults(defaults or {}))
    args = parser.parse_args(argv)
    try:
        validate_defaults({name: getattr(args, name) for name in LIMIT_FIELDS})
    except ValueError as error:
        parser.error(str(error))
    if args.overwrite_reviews and not args.allow_write:
        parser.error("--overwrite-reviews requires --allow-write")
    if args.prompt is not None and (args.batch or args.diff or args.resume):
        parser.error(
            "Use --batch-instructions for a batch job, or --prompt for a "
            "chat request"
        )
    if not args.diff and any(
        value is not None
        for value in (args.subset, args.base_project, args.base_branch)
    ):
        parser.error(
            "--subset, --base-project and --base-branch require --diff"
        )
    if args.legacy_ui and args.ui is None:
        args.ui = True
    return args


def history_settings(args):
    settings = Settings.from_env()
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


async def run(args):
    url = args.model_url or PROVIDER_DEFAULTS.get(args.provider)
    if not url:
        raise ChatError(
            "Specify --model-url for an OpenAI-compatible inference server"
        )
    if args.provider in (*LOCAL_PROVIDERS, "deepseek"):
        base = model_base(url)
    else:
        base = valid_url(url).rstrip("/")
        if base.endswith("/messages") or base.endswith("/responses"):
            base = base.rsplit("/", 1)[0]
    env = args.model_key_env or PROVIDER_KEYS.get(
        args.provider, "HISTORY_AGENT_MODEL_API_KEY"
    )
    key = os.environ.get(env)
    if args.provider in PROVIDER_KEYS and not key:
        raise ChatError(f"{args.provider} requires an API key in {env}")
    if args.provider not in LOCAL_PROVIDERS:
        if not args.model:
            raise ChatError(f"Specify --model for provider {args.provider}")
        name = args.model
    else:
        name = args.model or await asyncio.to_thread(discover_model, base, env)
    if args.no_thinking and args.provider not in LOCAL_PROVIDERS:
        raise ChatError(
            "--no-thinking only applies to compatible local chat-template"
            " servers"
        )
    settings = history_settings(args)
    model = Model(
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

    failure = None
    history = HistoryClient(settings)
    async with Client(create_server(history)) as client:
        try:
            use_ui = (
                args.ui
                if args.ui is not None
                else sys.stdin.isatty() and sys.stdout.isatty()
            )
            if use_ui and not args.legacy_ui:
                try:
                    from .ui import run_tui
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        defaults = (
            load_defaults()
            if not any(flag in argv for flag in ("-h", "--help"))
            else {}
        )
        args = parse_args(argv, defaults=defaults)
        configure = args.configure or (not argv and not defaults)
        while True:
            if configure:
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise ChatError(
                        "Open settings in a terminal with python3 chat.py "
                        "--configure, "
                        "or pass --model-url and --model for a one-off run"
                    )
                try:
                    from .ui.config import ConfigurationApp
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
                configure = False
            result = asyncio.run(run(args))
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
