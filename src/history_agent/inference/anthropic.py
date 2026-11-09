"""Native anthropic requests and response state."""

from __future__ import annotations

import json
from collections.abc import Callable

from history_agent.inference.conversations import (
    api_messages,
)
from history_agent.inference.model import ChatError, Model
from history_agent.inference.transport import _records, _request, result


def anthropic(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    system: str
    converted: list[dict[str, object]]
    system, converted = api_messages(messages, "anthropic")
    payload: dict[str, object] = {
        "model": model.name,
        "max_tokens": model.max_output_tokens,
        "messages": converted,
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ],
        "stream": model.stream,
    }
    if system:
        payload["system"] = system
    headers: dict[str, str] = {
        "x-api-key": model.key or "",
        "anthropic-version": "2023-06-01",
    }
    with _request(
        model.base,
        "/messages",
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        state: AnthropicState = AnthropicState(emit)
        for item in _records(response, model.stream):
            state.feed(item)
        return state.build_result()


class AnthropicState:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit: Callable[..., None] = emit
        self.text: list[str]
        self.thinking: list[str]
        self.calls: list[tuple[str, str, str | dict[str, object]]]
        self.blocks: dict[int, dict[str, object]]
        self.usage: dict[str, object]
        self.text, self.thinking, self.calls, self.blocks, self.usage = (
            [],
            [],
            [],
            {},
            {},
        )
        self.stopped: bool
        self.finish: str | None
        self.stopped, self.finish = (False, None)

    def feed(self, item: dict[str, object]) -> None:
        if "error" in item or item.get("type") == "error":
            raise ChatError("Anthropic returned an API error")
        typ: str | None = item.get("type")
        if typ == "message_start":
            self.on_message_start(item)
            return
        if typ == "content_block_start":
            self.on_content_block_start(item)
            return
        if typ == "content_block_delta":
            self.on_content_block_delta(item)
            return
        if typ == "message_delta":
            self.on_message_delta(item)
            return
        if typ == "message_stop":
            self.on_message_stop(item)
            return
        if typ == "message":
            self.on_message(item)
            return

    def on_message_start(self, item: dict[str, object]) -> None:
        self.usage.update(item.get("message", {}).get("usage") or {})

    def on_content_block_start(self, item: dict[str, object]) -> None:
        idx: int = item["index"]
        b: dict[str, object] = item["content_block"]
        self.blocks[idx] = b.copy()
        if b.get("type") == "text" and b.get("text"):
            self.text.append(b["text"])
            self.emit("content", text=b["text"])

    def on_content_block_delta(self, item: dict[str, object]) -> None:
        b = self.blocks.get(item["index"], {})
        delta: dict[str, object] = item.get("delta", {})
        if delta.get("type") == "text_delta" and delta.get("text"):
            self.text.append(delta["text"])
            self.emit("content", text=delta["text"])
        elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
            self.thinking.append(delta["thinking"])
            self.emit("reasoning", text=delta["thinking"])
        elif delta.get("type") == "input_json_delta":
            b["json_fragments"] = b.get("json_fragments", "") + delta.get(
                "partial_json", ""
            )
            if delta.get("partial_json"):
                self.emit("tool_delta", text=delta["partial_json"])

    def on_message_delta(self, item: dict[str, object]) -> None:
        self.usage.update(item.get("usage") or {})
        self.finish = item.get("delta", {}).get("stop_reason")

    def on_message_stop(self, item: dict[str, object]) -> None:
        self.stopped = True

    def on_message(self, item: dict[str, object]) -> None:
        self.stopped = True
        self.finish = item.get("stop_reason")
        self.usage.update(item.get("usage") or {})
        for b in item.get("content", []):
            if b.get("type") == "text":
                self.text.append(b.get("text", ""))
                self.emit("content", text=b.get("text", ""))
            elif b.get("type") == "thinking":
                self.thinking.append(b.get("thinking", ""))
                self.emit("reasoning", text=b.get("thinking", ""))
            elif b.get("type") == "tool_use":
                self.calls.append((b["id"], b["name"], b.get("input", {})))

    def build_result(self) -> dict[str, object]:
        if not self.stopped:
            raise ChatError(
                (
                    "Anthropic stream ended before completion; no tool calls"
                    " executed"
                )
            )
        for idx in sorted(self.blocks):
            b = self.blocks[idx]
            if b.get("type") == "tool_use":
                arg: str | None = b.get("json_fragments")
                self.calls.append(
                    (
                        b["id"],
                        b["name"],
                        json.loads(arg) if arg else b.get("input", {}),
                    )
                )
        stop: str | None = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }.get(self.finish)
        if stop is None:
            raise ChatError("Unexpected Anthropic finish reason")
        stats: dict[str, object] = {
            "prompt_tokens": self.usage.get("input_tokens"),
            "completion_tokens": self.usage.get("output_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": self.usage.get("cache_read_input_tokens")
            },
        }
        self.emit("usage", usage=stats)
        return result(
            "".join(self.text),
            self.calls,
            stop,
            stats,
            reasoning="".join(self.thinking),
        )
