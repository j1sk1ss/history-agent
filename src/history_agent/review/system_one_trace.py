"""Collect trace events and small source windows for System 1."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator

from mcp import Client

from history_agent.history.client import HistoryError
from history_agent.inference.model import ChatError
from history_agent.runtime.tools import ToolClient


def history_reads(
    messages: list[dict[str, object]],
) -> Iterator[tuple[str, dict[str, object], dict[str, object]]]:
    calls = {}
    for message in messages:
        try:
            if message.get("role") == "assistant":
                for call in message.get("tool_calls", []):
                    function = call.get("function", {})
                    if function.get("name") == "history_read":
                        calls[call["id"]] = json.loads(function["arguments"])
            elif message.get("role") == "tool":
                request = calls.get(message.get("tool_call_id"), {})
                data = json.loads(message["content"])
                if isinstance(data, dict) and request.get("action"):
                    yield request["action"], request.get("arguments", {}), data
        except (KeyError, TypeError, ValueError, AttributeError):
            continue


def trace_points(value: object, snapshot: str) -> list[dict[str, object]]:
    """Flatten nested trace events, preserving order and source provenance."""
    points = []
    if isinstance(value, list):
        for entry in value:
            points.extend(trace_points(entry, snapshot))
    elif isinstance(value, dict):
        location = value.get("location", value)
        if isinstance(location, str):
            match = re.fullmatch(r"(.+?):(\d+)(?::\d+)?", location)
            location = (
                {
                    "file": match[1],
                    "line": int(match[2]),
                }
                if match
                else {}
            )
        if not isinstance(location, dict):
            location = {}
        current = (
            location.get("snapshot_override")
            or value.get("snapshot_override")
            or snapshot
        )
        if not isinstance(current, str):
            current = snapshot
        file = (
            location.get("file_path")
            or location.get("file")
            or location.get("path")
        )
        line = location.get("line")
        if isinstance(line, str) and line.isdigit():
            line = int(line)
        message = (
            value.get("message")
            or value.get("description")
            or value.get("text")
        )
        point = {}
        if isinstance(message, str) and message.strip():
            point["message"] = message
        if isinstance(file, str) and file and type(line) is int and line > 0:
            point.update(file=file, line=line, _snapshot=current)
        elif "location" in value or file or line:
            point["source_unavailable"] = True
        if point:
            points.append(point)
        for key, entry in value.items():
            if key != "location" and isinstance(entry, (dict, list)):
                points.extend(trace_points(entry, snapshot))
    elif isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (list, dict)):
            points.extend(trace_points(decoded, snapshot))
        else:
            points.append({"message": value})
    return points


def source_lines(
    data: dict[str, object],
    line: int,
) -> list[dict[str, object]]:
    entries = data.get("lines", [])
    if not isinstance(entries, list):
        return []
    return [
        {"number": entry["number"], "text": entry["text"]}
        for entry in entries
        if isinstance(entry, dict)
        and type(entry.get("number")) is int
        and max(1, line - 5) <= entry["number"] <= line + 5
        and isinstance(entry.get("text"), str)
    ]


def cached_trace(records, target):
    warning = {}
    trace = None
    cache = {}
    for action, arguments, data in records:
        matches = all(
            arguments.get(key) == target[key]
            for key in ("project_id", "stream_id", "snapshot_id", "warning_id")
            if key in target
        )
        if action == "get_warning" and matches:
            warning = data.get("warning", {})
        elif (
            action
            in (
                "get_warning_trace_and_role",
                "get_warning_trace",
            )
            and matches
        ):
            trace = data.get("trace", data.get("traces", data))
        elif action == "agent_source":
            line = arguments.get("line")
            if (
                type(line) is int
                and arguments.get("before", 10) >= 5
                and arguments.get("after", 10) >= 5
            ):
                key = (
                    arguments.get("project_id"),
                    arguments.get("snapshot_id"),
                    arguments.get("file"),
                    line,
                )
                lines = source_lines(data, line)
                expected = set(range(max(1, line - 5), line + 6))
                if expected <= {entry["number"] for entry in lines}:
                    cache[key] = lines

    return warning, trace, cache


async def load_trace_context(
    client: Client | ToolClient,
    messages: list[dict[str, object]],
    target: dict[str, object],
) -> dict[str, object]:
    warning, trace, cache = cached_trace(list(history_reads(messages)), target)

    semaphore = asyncio.Semaphore(4)

    async def read(action: str, arguments: dict[str, object]) -> dict | None:
        async with semaphore:
            try:
                result = await client.call_tool(
                    "history_read",
                    {
                        "action": action,
                        "arguments": arguments,
                    },
                )
                if not result.is_error:
                    data = json.loads(
                        "".join(
                            part.text
                            for part in result.content
                            if part.type == "text"
                        )
                    )
                    if isinstance(data, dict):
                        return data
            except (HistoryError, ChatError, TypeError, ValueError):
                pass
        return None

    if trace is None and isinstance(warning, dict):
        trace = warning.get("trace", warning.get("traces")) or None
    if trace is None and target:
        result = await read("get_warning_trace_and_role", target)
        if result is not None:
            trace = result.get("trace", result.get("traces", result))
    points = trace_points(trace, target.get("snapshot_id", ""))
    project = target.get("project_id")
    needed = {}
    for point in points[:64]:
        if "file" in point:
            key = (project, point["_snapshot"], point["file"], point["line"])
            if project is None or not point["_snapshot"]:
                cache[key] = []
            if key not in cache:
                needed[key] = {
                    "project_id": project,
                    "snapshot_id": point["_snapshot"],
                    "file": point["file"],
                    "line": point["line"],
                    "before": 5,
                    "after": 5,
                }

    async def load(key: tuple, arguments: dict[str, object]) -> None:
        data = await read("agent_source", arguments)
        cache[key] = source_lines(data or {}, arguments["line"])

    await asyncio.gather(*(load(key, args) for key, args in needed.items()))
    return attach_code(
        points[:64], cache, project, trace is not None and len(points) <= 64
    )


def attach_code(points, cache, project, complete):
    clean = []
    for point in points:
        event = {
            key: value for key, value in point.items() if key != "_snapshot"
        }
        if event.get("source_unavailable"):
            complete = False
        if "file" in point:
            key = (project, point["_snapshot"], point["file"], point["line"])
            event["code"] = cache.get(key, [])
            if not any(
                line["number"] == point["line"] for line in event["code"]
            ):
                event["source_unavailable"] = True
                complete = False
        clean.append(event)
    return {"trace": clean, "trace_complete": complete}
