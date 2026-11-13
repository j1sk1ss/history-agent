"""Select current warning metadata for host-side classification."""

from __future__ import annotations

import json

from mcp import Client

from history_agent.history.client import HistoryError
from history_agent.history.constants import URL_ACTION
from history_agent.inference.model import ChatError
from history_agent.review.system_one_trace import (
    history_reads,
    load_trace_context,
)
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.tools import ToolClient


def warning_context(messages: list[dict[str, object]]) -> dict[str, str]:
    """Select warning text and checker conditions from actual history reads."""
    calls: dict[str, dict[str, object]] = {}
    warning: dict[str, object] = {}
    articles: dict[str, dict[int, str]] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") == "history_read":
                    try:
                        calls[call["id"]] = json.loads(function["arguments"])
                    except (KeyError, TypeError, ValueError):
                        continue
        elif message.get("role") == "tool":
            request = calls.get(message.get("tool_call_id"), {})
            action = request.get("action")
            if action not in (
                "get_warning",
                "agent_checker_info",
                "agent_checker_documentation",
                URL_ACTION,
            ):
                continue
            try:
                data = json.loads(message["content"])
                if action == URL_ACTION:
                    if data.get("read_action") == "get_warning":
                        warning = {}
                    continue
                if action == "get_warning":
                    warning = data.get("warning", {})
                    if not isinstance(warning, dict):
                        warning = {}
                    continue
                arguments = request.get("arguments", {})
                checker = arguments.get(
                    "checker_name", data.get("checker_name")
                )
                document = data.get("documentation", data)
                text = document.get("text")
                offset = document.get("offset", arguments.get("offset", 0))
                if isinstance(checker, str) and isinstance(text, str):
                    articles.setdefault(checker.casefold(), {})[offset] = text
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    checker = warning.get("checker_name", "")
    description = warning.get("message") or warning.get("description", "")
    if not isinstance(checker, str) or not isinstance(description, str):
        return {}
    pages = articles.get(checker.casefold(), {})
    parts = []
    end = None
    for offset in sorted(pages):
        if end is not None and offset != end:
            parts.append("\n")
        parts.append(pages[offset])
        end = offset + len(pages[offset])
    return {
        "checker_name": checker,
        "warning_description": (checker + ": " + description).strip(": "),
        "tp_conditions": "".join(parts),
    }


async def load_warning_context(
    client: Client | ToolClient,
    messages: list[dict[str, object]],
) -> dict[str, object]:
    """Fetch missing metadata inside the host, without extra model turns."""
    packets = list(messages)
    metadata = warning_context(packets)
    target: dict[str, object] = {}
    calls: dict[str, dict[str, object]] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") == "history_read":
                    try:
                        request = json.loads(function["arguments"])
                        calls[call["id"]] = request
                        if request.get("action") == "get_warning":
                            target = request.get("arguments", {})
                    except (KeyError, TypeError, ValueError):
                        continue
        elif message.get("role") == "tool":
            request = calls.get(message.get("tool_call_id"), {})
            if request.get("action") != URL_ACTION:
                continue
            try:
                data = json.loads(message["content"])
                if data.get("read_action") == "get_warning":
                    target = data.get("read_arguments", {})
            except (KeyError, TypeError, ValueError, AttributeError):
                continue

    async def read(action: str, arguments: dict[str, object]) -> bool:
        try:
            result = await client.call_tool(
                "history_read",
                {
                    "action": action,
                    "arguments": arguments,
                },
            )
        except (HistoryError, ChatError):
            return False
        if result.is_error:
            return False
        contents = "".join(
            part.text for part in result.content if part.type == "text"
        )
        packets.extend(
            tool_exchange(
                "system-one-context-" + str(len(packets)),
                action,
                arguments,
                contents,
            )
        )
        return True

    if not metadata.get("warning_description") and target:
        await read("get_warning", target)
        metadata = warning_context(packets)
    checker = metadata.get("checker_name")
    if checker and not metadata.get("tp_conditions"):
        arguments = {"checker_name": checker}
        await read("agent_checker_info", arguments)
        metadata = warning_context(packets)
        if not metadata.get("tp_conditions"):
            await read("agent_checker_documentation", arguments)
            metadata = warning_context(packets)
    for _ in range(4):
        documents = checker_documents(packets, checker)
        last = documents[-1] if documents else {}
        offset = last.get("next_offset")
        if not last.get("has_more") or type(offset) is not int:
            break
        if not await read(
            "agent_checker_documentation",
            {
                "checker_name": checker,
                "offset": offset,
            },
        ):
            break
    metadata = warning_context(packets)
    return {
        **metadata,
        "tp_complete": complete_documentation(packets, checker),
        **await load_trace_context(client, packets, target),
    }


def checker_documents(packets, checker):
    pages = {}
    for action, arguments, data in history_reads(packets):
        if action not in ("agent_checker_info", "agent_checker_documentation"):
            continue
        if (
            str(arguments.get("checker_name", "")).casefold()
            != str(checker).casefold()
        ):
            continue
        document = data.get("documentation", data)
        if isinstance(document, dict) and isinstance(
            document.get("text"), str
        ):
            pages[document.get("offset", arguments.get("offset", 0))] = (
                document
            )
    return [pages[key] for key in sorted(pages)]


def complete_documentation(packets, checker):
    documents = checker_documents(packets, checker)
    offset = 0
    for document in documents:
        if document.get("offset", 0) != offset:
            return False
        offset += len(document["text"])
    return bool(documents) and not documents[-1].get("has_more", False)
