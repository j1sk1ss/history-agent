"""Small live requests for the settings screen's connection indicators."""

from __future__ import annotations

import os
import re
from argparse import Namespace

from history_agent.configuration.connection import discover_model, valid_url
from history_agent.configuration.defaults import (
    COMPATIBLE_PROVIDERS,
    LOCAL_PROVIDERS,
    PROVIDER_DEFAULTS,
    PROVIDER_KEYS,
    REQUIRED_KEY_PROVIDERS,
)
from history_agent.inference.model import ChatError, Model
from history_agent.review.system_one import SystemOne


def probe_model_connection(args: Namespace) -> tuple[bool, str]:
    try:
        url = args.model_url or PROVIDER_DEFAULTS.get(args.provider)
        if not url:
            return False, "Enter the model URL."
        base = valid_url(url)
        if args.provider in COMPATIBLE_PROVIDERS:
            if base.endswith(("/messages", "/responses")):
                return False, (
                    "This provider requires a Chat Completions endpoint."
                )
            base = base.removesuffix("/chat/completions")
            if not re.search(r"/v\d+$", base):
                base += "/v1"
        elif base.endswith(("/messages", "/responses")):
            base = base.rsplit("/", 1)[0]
        key_env = args.model_key_env or PROVIDER_KEYS.get(
            args.provider, "HISTORY_AGENT_MODEL_API_KEY"
        )
        key = os.environ.get(key_env)
        if args.provider in REQUIRED_KEY_PROVIDERS and not key:
            return False, f"Set the API key in {key_env}."
        timeout = min(args.timeout, 5)
        name = args.model
        if not name and args.provider in LOCAL_PROVIDERS:
            name = discover_model(base, key_env, timeout=timeout)
        if not name:
            return False, "Enter the model ID."
        model = Model(
            base, name, key, timeout, max_output_tokens=32,
            no_thinking=args.no_thinking and args.provider in LOCAL_PROVIDERS,
            stream=False, stream_usage=False, provider=args.provider,
        )
        result = model.complete([{"role": "user", "content": "Reply OK."}], [])
        if not result.get("choices"):
            return False, "The model returned no completion."
        return True, "The configured model answered a test request."
    except (ChatError, OSError, ValueError, TypeError, KeyError) as error:
        return False, str(error)


def probe_system_one_connection(args: Namespace) -> tuple[bool, str]:
    if not args.system_one_model:
        return False, "System 1 is disabled: enter a model ID to enable it."
    if not args.system_one_url:
        return False, "Enter the System 1 URL."
    try:
        valid_url(args.system_one_url)
        model = SystemOne(
            args.system_one_url, args.system_one_model, min(args.timeout, 5)
        )
        if model.probe():
            return True, (
                "The configured System 1 model answered a test request."
            )
        return False, "System 1 did not return a valid answer."
    except (OSError, ValueError, TypeError) as error:
        return False, str(error)
