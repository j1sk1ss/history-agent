"""Validate an entire model response before executing any tool calls."""

from __future__ import annotations

import json

from history_agent.inference.model import ChatError, compact


def parse_message(
    result: dict[str, object], allowed: set[str]
) -> tuple[
    dict[str, object], list[tuple[dict[str, object], dict[str, object]]]
]:
    try:
        choice: dict[str, object]
        (choice,) = result["choices"]
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ChatError(
                (
                    "Model response was cut short; no tools from this"
                    " response were executed"
                )
            )
        raw: dict[str, object] = choice["message"]
        if raw.get("role") != "assistant" or not isinstance(
            raw.get("content"), (str, type(None))
        ):
            raise ValueError()
        message: dict[str, object] = {
            "role": "assistant",
            "content": raw.get("content"),
        }
        reasoning: object = raw.get("reasoning_content", raw.get("reasoning"))
        if isinstance(reasoning, str):
            message["reasoning_content"] = reasoning
        if isinstance(raw.get("gemini_parts"), list):
            message["gemini_parts"] = raw["gemini_parts"]
        if isinstance(raw.get("responses_items"), list):
            message["responses_items"] = raw["responses_items"]
        calls: list[dict[str, object]] = raw.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 8:
            raise ValueError()
        parsed: list[tuple[dict[str, object], dict[str, object]]] = (
            parse_calls(calls, allowed)
        )
        if parsed:
            message["tool_calls"] = [call for call, _ in parsed]
        elif not message["content"]:
            raise ChatError("Model returned no answer or tool calls")
        return (message, parsed)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ChatError(
            (
                "Model returned malformed tool calls or messages;"
                " no tools from this response were executed"
            )
        ) from None


def parse_calls(
    calls: list[dict[str, object]], allowed: set[str]
) -> list[tuple[dict[str, object], dict[str, object]]]:
    parsed: list[tuple[dict[str, object], dict[str, object]]]
    ids: set[str]
    clean: list[dict[str, object]]
    parsed, ids, clean = ([], set(), [])
    for call in calls:
        if (
            call["type"] != "function"
            or not isinstance(call["id"], str)
            or (not call["id"])
            or (call["id"] in ids)
        ):
            raise ValueError()
        name: str = call["function"]["name"]
        if name not in allowed:
            raise ChatError(
                (
                    "Model requested a disabled or unknown tool; no "
                    "tools from this response were executed"
                )
            )
        arguments: dict[str, object] = json.loads(
            call["function"]["arguments"]
        )
        if not isinstance(arguments, dict):
            raise ValueError()
        sanitized: dict[str, object] = {
            "id": call["id"],
            "type": "function",
            "function": {"name": name, "arguments": compact(arguments)},
        }
        clean.append(sanitized)
        parsed.append((sanitized, arguments))
        ids.add(call["id"])
    return parsed
