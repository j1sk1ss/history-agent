"""Shared HTTP records and normalized completion envelopes."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from http.client import HTTPResponse

from history_agent.http import NoRedirects
from history_agent.inference.common import (
    MAX_RESPONSE_BYTES,
    ChatError,
    compact,
)
from history_agent.inference.stream import sse_events


def result(
    content: str | None, calls: list[tuple[str, str, str | dict[str, object]]],
    reason: str, usage: dict[str, object] | None = None, *,
    reasoning: str | None = None,
    gemini_parts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": content or None,
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": compact(arguments)
                    if not isinstance(arguments, str)
                    else arguments,
                },
            }
            for call_id, name, arguments in calls
        ]
    if gemini_parts:
        message["gemini_parts"] = gemini_parts
    return {
        "choices": [{"message": message, "finish_reason": reason}],
        "usage": usage,
    }


def _request(
    base: str, path: str, payload: dict[str, object], headers: dict[str, str],
    timeout: float, *, stream: bool,
) -> HTTPResponse:
    url: str = base.rstrip("/") + path
    request: urllib.request.Request = urllib.request.Request(
        url,
        data=compact(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    opener: urllib.request.OpenerDirector = urllib.request.build_opener(
        NoRedirects()
    )
    return opener.open(request, timeout=timeout)


def _records(
    response: HTTPResponse, stream: bool,
) -> Iterator[dict[str, object]]:
    if stream and response.headers.get_content_type() == "text/event-stream":
        for data in sse_events(response):
            if data.strip() == "[DONE]":
                return
            yield json.loads(data)
    else:
        raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ChatError("Model response exceeds 2 MiB")
        yield json.loads(raw)
