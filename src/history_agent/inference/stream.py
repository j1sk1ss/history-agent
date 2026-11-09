"""Read SSE records and assemble complete Chat Completions responses."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from http.client import HTTPResponse

from history_agent.inference.common import MAX_RESPONSE_BYTES, ChatError


def sse_events(response: HTTPResponse) -> Iterator[str]:
    """
    Read UTF-8 SSE records, including multiline data and a final
    unseparated record.
    """
    total: int
    data: list[str]
    total, data = (0, [])
    while True:
        raw: bytes = response.readline(MAX_RESPONSE_BYTES + 1)
        total += len(raw)
        if total > MAX_RESPONSE_BYTES:
            raise ChatError(
                ("Model stream exceeds 2 MiB; lower --max-output-tokens")
            )
        if not raw:
            if data:
                yield "\n".join(data)
            return
        line: str = raw.decode(
            "utf-8-sig" if total == len(raw) else "utf-8"
        ).rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            value: str = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)


class CompletionStream:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit: Callable[..., None] = emit
        self.content: list[str]
        self.reasoning: list[str]
        self.calls: dict[int, dict[str, object]]
        self.content, self.reasoning, self.calls = ([], [], {})
        self.finish_reason: str | None
        self.usage: dict[str, object] | None
        self.finish_reason, self.usage = (None, None)

    def feed(self, chunk: dict[str, object]) -> None:
        if not isinstance(chunk, dict) or "error" in chunk:
            raise ValueError()
        usage: dict[str, object] | None = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
            self.emit("usage", usage=usage)
        choices: list[dict[str, object]] = chunk.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError()
        if not choices:
            return
        choice: dict[str, object] = choices[0]
        if choice.get("index", 0) != 0:
            raise ValueError()
        delta: dict[str, object] = choice.get("delta", {})
        if (
            not isinstance(delta, dict)
            or delta.get("role", "assistant") != "assistant"
        ):
            raise ValueError()
        if self.finish_reason is not None and any(
            (
                delta.get(key)
                for key in (
                    "content",
                    "reasoning_content",
                    "reasoning",
                    "tool_calls",
                )
            )
        ):
            raise ValueError()
        self.feed_text(delta)
        self.feed_calls(delta)
        reason: str | None = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise ValueError()
            self.finish_reason = reason

    def result(self) -> dict[str, object]:
        if self.finish_reason not in (
            "stop",
            "tool_calls",
            "length",
            "content_filter",
        ):
            raise ChatError(
                (
                    "Model stream ended before a valid finish marker;"
                    " no tools from this response were executed"
                )
            )
        if self.calls and self.finish_reason not in (
            "tool_calls",
            "length",
            "content_filter",
        ):
            raise ChatError(
                (
                    "Model stream has inconsistent tool calls; no "
                    "tools from this response were executed"
                )
            )
        message: dict[str, object] = {
            "role": "assistant",
            "content": "".join(self.content) or None,
        }
        if self.reasoning:
            message["reasoning_content"] = "".join(self.reasoning)
        if self.calls:
            message["tool_calls"] = [
                self.calls[index] for index in sorted(self.calls)
            ]
        return {
            "choices": [
                {"message": message, "finish_reason": self.finish_reason}
            ],
            "usage": self.usage,
        }

    def feed_text(self, delta: dict[str, object]) -> None:
        for field, destination, event in (
            ("content", self.content, "content"),
            ("reasoning_content", self.reasoning, "reasoning"),
        ):
            text: str | None = delta.get(field)
            if field == "reasoning_content" and text is None:
                text = delta.get("reasoning")
            if text is not None:
                if not isinstance(text, str):
                    raise ValueError()
                destination.append(text)
                if text:
                    self.emit(event, text=text)

    def feed_calls(self, delta: dict[str, object]) -> None:
        calls: list[dict[str, object]] = delta.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError()
        for part in calls:
            index: int = part["index"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or (not 0 <= index < 8)
            ):
                raise ValueError()
            target: dict[str, object] = self.calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if part.get("type", "function") != "function":
                raise ValueError()
            if part.get("id") is not None:
                if not isinstance(part["id"], str):
                    raise ValueError()
                target["id"] += part["id"]
            function: dict[str, object] = part.get("function") or {}
            if not isinstance(function, dict):
                raise ValueError()
            for key in ("name", "arguments"):
                value: str | None = function.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError()
                    target["function"][key] += value
                    if value:
                        self.emit("tool_delta", text=value)


def read_completion(
    response: HTTPResponse, emit: Callable[..., None]
) -> dict[str, object]:
    if response.headers.get_content_type() == "text/event-stream":
        stream: CompletionStream = CompletionStream(emit)
        for data in sse_events(response):
            if data.strip() == "[DONE]":
                break
            stream.feed(json.loads(data))
        return stream.result()
    raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ChatError(
            ("Model response exceeds 2 MiB; lower --max-output-tokens")
        )
    result: dict[str, object] = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(
        result.get("choices"), list
    ):
        raise ValueError()
    if len(result["choices"]) == 1:
        message: dict[str, object] = result["choices"][0].get("message", {})
        for field, kind in (
            ("reasoning_content", "reasoning"),
            ("content", "content"),
        ):
            text: str | None = message.get(field)
            if field == "reasoning_content" and text is None:
                text = message.get("reasoning")
            if isinstance(text, str):
                emit(kind, text=text)
    if isinstance(result.get("usage"), dict):
        emit("usage", usage=result["usage"])
    return result
