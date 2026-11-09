"""Declarative command-line options, grouped by parser ownership."""

import argparse
from pathlib import Path

from history_agent.configuration.defaults import PROVIDERS

PARSER_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--configure",),
        {
            "action": "store_true",
            "help": "Open settings and explicitly save chat defaults",
        },
    ),
    (
        ("--provider",),
        {
            "default": "openai-compatible",
            "choices": PROVIDERS,
            "help": (
                "Inference transport: OpenAI-compatible, "
                "Responses (Codex), Anthropic or Gemini"
            ),
        },
    ),
    (
        ("--model-url",),
        {
            "help": (
                "Provider API base; defaults to official API or local service"
            )
        },
    ),
    (
        ("--model",),
        {"help": "Model ID; auto-detected if /models lists exactly one"},
    ),
    (
        ("--model-key-env",),
        {
            "help": (
                "Environment variable containing inference API "
                "key; provider default if omitted"
            )
        },
    ),
    (
        ("--history-url",),
        {
            "help": (
                "Override HISTORY_URL/HISTORY_CONFIG; supports remote HTTP(S)"
            )
        },
    ),
    (
        ("--prompt",),
        {
            "help": (
                "One request, then exit; otherwise start an interactive chat"
            )
        },
    ),
    (
        ("--allow-write",),
        {
            "action": "store_true",
            "default": True,
            "help": "Save each validated batch review immediately (default)",
        },
    ),
    (
        ("--read-only",),
        {
            "dest": "allow_write",
            "action": "store_false",
            "help": "Disable review writes, overriding saved settings",
        },
    ),
    (
        ("--max-steps",),
        {"type": int, "default": 12, "help": "Model requests per user turn"},
    ),
    (("--max-output-tokens",), {"type": int, "default": 2048}),
    (
        ("--max-context-bytes",),
        {
            "type": int,
            "default": 65536,
            "help": "JSON input budget, not tokenizer tokens",
        },
    ),
    (("--max-tool-result-bytes",), {"type": int, "default": 16384}),
    (("--timeout",), {"type": float, "default": 60}),
    (
        ("--no-thinking",),
        {
            "action": "store_true",
            "help": (
                "Send Qwen/llama-server enable_thinking=false template option"
            ),
        },
    ),
    (
        ("--thinking",),
        {
            "dest": "no_thinking",
            "action": "store_false",
            "help": "Use the model default thinking mode",
        },
    ),
    (
        ("--stats",),
        {
            "action": "store_true",
            "help": (
                "Print request time, input bytes and reported "
                "token/cache usage"
            ),
        },
    ),
    (("--no-stats",), {"dest": "stats", "action": "store_false"}),
    (
        ("--no-stream",),
        {
            "action": "store_true",
            "help": "Use non-streaming JSON for servers without SSE support",
        },
    ),
    (("--stream",), {"dest": "no_stream", "action": "store_false"}),
    (
        ("--no-stream-usage",),
        {
            "action": "store_true",
            "help": (
                "Omit stream_options for servers that reject include_usage"
            ),
        },
    ),
    (
        ("--stream-usage",),
        {"dest": "no_stream_usage", "action": "store_false"},
    ),
    (
        ("--subset",),
        {
            "choices": ("appeared", "disappeared", "both"),
            "default": None,
            "help": "Difference subset for --diff; defaults to appeared",
        },
    ),
    (
        ("--base-project",),
        {"type": int, "help": "Base project for --diff; defaults to current"},
    ),
    (
        ("--base-branch",),
        {"help": "Base branch for --diff; defaults to current"},
    ),
    (
        ("--batch-instructions",),
        {
            "default": "",
            "help": (
                "Project-specific rules to retain across batch context resets"
            ),
        },
    ),
    (
        ("--batch-state-dir",),
        {
            "type": Path,
            "default": Path.home() / ".local/state/history-agent/jobs",
        },
    ),
    (
        ("--overwrite-reviews",),
        {
            "action": "store_true",
            "help": (
                "Permit replacing existing reviews in explicitly "
                "selected batch jobs"
            ),
        },
    ),
    (
        ("--no-patterns",),
        {
            "action": "store_true",
            "help": "Disable checker-local reusable review checklists",
        },
    ),
    (("--patterns",), {"dest": "no_patterns", "action": "store_false"}),
)

DISPLAY_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--ui",),
        {
            "dest": "ui",
            "action": "store_true",
            "help": "Force the live terminal interface",
        },
    ),
    (
        ("--plain",),
        {
            "dest": "ui",
            "action": "store_false",
            "help": "Plain output for scripts/pipes",
        },
    ),
    (("--legacy-ui",), {"action": "store_true", "help": argparse.SUPPRESS}),
)

JOB_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--batch",),
        {
            "nargs": 3,
            "metavar": ("PROJECT", "BRANCH", "SNAPSHOT"),
            "help": "Review all warnings in an isolated-context job",
        },
    ),
    (
        ("--diff",),
        {
            "nargs": 4,
            "metavar": ("PROJECT", "BRANCH", "CURRENT", "BASE"),
            "help": (
                "Review a snapshot difference; default appeared (UI First)"
            ),
        },
    ),
    (
        ("--resume",),
        {
            "metavar": "JOB_ID",
            "help": (
                "Resume a checkpoint using its original scope and write mode"
            ),
        },
    ),
)
