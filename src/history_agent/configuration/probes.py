"""Small live requests for the settings screen's connection indicators."""

from __future__ import annotations

from argparse import Namespace

from history_agent.configuration.connection import valid_url
from history_agent.configuration.model import configured_model
from history_agent.inference.model import ChatError
from history_agent.review.system_one import SystemOne
from history_agent.runtime.parsing import parse_message


def probe_model_connection(args: Namespace) -> tuple[bool, str]:
    try:
        model = configured_model(args, discovery_timeout=min(args.timeout, 5))
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "connection_check",
                    "description": "Acknowledge the connection check.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        ]
        result = model.complete(
            [{"role": "user", "content": "Reply OK."}],
            tools,
        )
        parse_message(result, {"connection_check"})
        return True, (
            "The model answered with the configured streaming, tools and "
            "output settings."
        )
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
