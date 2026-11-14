"""Dispatch native model providers and translate transport failures."""

from __future__ import annotations

import http.client
import urllib.error
from collections.abc import Callable

from history_agent.inference.anthropic import anthropic
from history_agent.inference.common import request_failure
from history_agent.inference.gemini import gemini
from history_agent.inference.model import ChatError, Model
from history_agent.inference.responses import responses


def complete_native(
    model: Model,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    emit: Callable[..., None],
) -> dict[str, object]:
    try:
        if model.provider in ("anthropic", "claude"):
            return anthropic(model, messages, tools, emit)
        if model.provider == "gemini":
            return gemini(model, messages, tools, emit)
        return responses(model, messages, tools, emit)
    except (
        OSError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as error:
        detail = request_failure(error)
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        raise ChatError(detail + " No tools executed.") from None
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError):
        raise ChatError(
            "Provider returned invalid JSON or tool calls; no tools executed"
        ) from None
