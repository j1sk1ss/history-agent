"""Build the same model transport for settings checks and conversations."""

import re
from argparse import Namespace

from history_agent.configuration.connection import discover_model, valid_url
from history_agent.configuration.credentials import model_api_key
from history_agent.configuration.defaults import (
    COMPATIBLE_PROVIDERS,
    LOCAL_PROVIDERS,
    PROVIDER_DEFAULTS,
    REQUIRED_KEY_PROVIDERS,
)
from history_agent.inference.model import ChatError, Model


def model_base(url: str) -> str:
    base = valid_url(url)
    if base.endswith(("/responses", "/messages")):
        raise ValueError("history-chat requires a Chat Completions endpoint")
    base = base.removesuffix("/chat/completions")
    return base if re.search(r"/v\d+$", base) else base + "/v1"


def configured_model(
    args: Namespace,
    *,
    discovery_timeout: float | None = None,
) -> Model:
    url = args.model_url or PROVIDER_DEFAULTS.get(args.provider)
    if not url:
        raise ChatError("Enter the model URL or pass --model-url")
    if args.provider in COMPATIBLE_PROVIDERS:
        base = model_base(url)
    else:
        base = valid_url(url).rstrip("/")
        if base.endswith(("/messages", "/responses")):
            base = base.rsplit("/", 1)[0]
    key = model_api_key(args)
    if args.provider in REQUIRED_KEY_PROVIDERS and not key:
        raise ChatError(
            f"{args.provider} requires an API key; enter it in settings "
            "or pass --model-api-key"
        )
    name = args.model
    if not name and args.provider in LOCAL_PROVIDERS:
        name = discover_model(
            base,
            api_key=key,
            timeout=discovery_timeout or args.timeout,
        )
    if not name:
        raise ChatError(f"Specify --model for provider {args.provider}")
    if args.no_thinking and args.provider not in LOCAL_PROVIDERS:
        raise ChatError(
            "--no-thinking only applies to compatible local chat-template "
            "servers"
        )
    return Model(
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
