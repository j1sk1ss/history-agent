"""Declarative command-line options, grouped by parser ownership."""

import argparse
from pathlib import Path

from history_agent.configuration.defaults import PROVIDERS

SESSION_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--history-timeout",),
        {
            "type": float,
            "help": "History server timeout in seconds (default: 600)",
        },
    ),
    (
        ("--history-redis-url",),
        {"help": "Redis response cache (default: redis://127.0.0.1:6379/0)"},
    ),
    (
        ("--history-cache-ttl",),
        {
            "type": int,
            "help": "Mutable history response cache lifetime (default: 300s)",
        },
    ),
    (
        ("--history-source-cache-ttl",),
        {
            "type": int,
            "help": "Concrete source/trace cache lifetime (default: 86400s)",
        },
    ),
    (("--model-profile",), {"help": "Saved provider/model to use"}),
    (
        ("--prompts-file",),
        {"help": "JSON overrides for the central prompt catalog"},
    ),
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
                "Inference transport: OpenAI-compatible (including LiteLLM), "
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
        {
            "help": (
                "Model ID or LiteLLM alias; local endpoints can auto-detect "
                "when /models lists exactly one"
            )
        },
    ),
    (
        ("--model-api-key",),
        {"help": "API key for the model connection"},
    ),
    (
        ("--model-key-env",),
        {"help": argparse.SUPPRESS},
    ),
    (
        ("--system-one-url",),
        {
            "default": "http://aport:8000/v1/systemone",
            "help": "System 1 endpoint (full URL)",
        },
    ),
    (
        ("--system-one-model",),
        {"help": "System 1 adviser; confidence >= 0.7 allows a quick verdict"},
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
            "help": ("Save each validated batch review immediately (default)"),
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
        {
            "type": int,
            "default": 12,
            "help": "Normal model requests per turn, excluding error recovery",
        },
    ),
    (("--max-output-tokens",), {"type": int, "default": 2048}),
    (
        ("--max-tool-retries",),
        {
            "type": int,
            "default": 8,
            "help": "Automatic context reloads after tool errors (default: 8)",
        },
    ),
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
)
STREAM_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--no-stream",),
        {
            "action": "store_true",
            "help": ("Use non-streaming JSON for servers without SSE support"),
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
)
REVIEW_OPTIONS: tuple[tuple[tuple[str, ...], dict[str, object]], ...] = (
    (
        ("--subset",),
        {
            "choices": ("appeared", "disappeared", "both"),
            "default": None,
            "help": ("Difference subset for --diff; defaults to appeared"),
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
            "help": argparse.SUPPRESS,
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
