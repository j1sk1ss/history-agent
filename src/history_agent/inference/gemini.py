"""Native gemini requests and response state."""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable

from history_agent.inference.conversations import (
    gemini_messages,
)
from history_agent.inference.model import ChatError, Model, compact
from history_agent.inference.transport import _records, _request, result


def gemini(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    system: str
    converted: list[dict[str, object]]
    system, converted = gemini_messages(messages)
    payload: dict[str, object] = {
        "contents": converted,
        "generationConfig": {"maxOutputTokens": model.max_output_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t["function"]["name"],
                        "description": t["function"]["description"],
                        "parametersJsonSchema": t["function"]["parameters"],
                    }
                    for t in tools
                ]
            }
        ]
    model_id: str = urllib.parse.quote(model.name, safe="-_.")
    endpoint: str = (
        f"/models/{model_id}:streamGenerateContent?alt=sse"
        if model.stream
        else f"/models/{model_id}:generateContent"
    )
    headers: dict[str, str] = {"x-goog-api-key": model.key or ""}
    with _request(
        model.base,
        endpoint,
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        state: GeminiState = GeminiState(emit)
        for item in _records(response, model.stream):
            state.feed(item)
        return state.build_result()


class GeminiState:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit: Callable[..., None] = emit
        self.text: list[str]
        self.thought: list[str]
        self.calls: list[tuple[str, str, str | dict[str, object]]]
        self.preserved: list[dict[str, object]]
        self.text, self.thought, self.calls, self.preserved = ([], [], [], [])
        self.usage: dict[str, object]
        self.finish: str | None
        self.usage, self.finish = ({}, None)

    def feed(self, item: dict[str, object]) -> None:
        if item.get("error"):
            raise ChatError("Gemini returned an API error")
        self.usage.update(item.get("usageMetadata") or {})
        for candidate in item.get("candidates", []):
            self.finish = candidate.get("finishReason") or self.finish
            for part in candidate.get("content", {}).get("parts", []):
                self.preserved.append(part)
                if isinstance(part.get("text"), str):
                    if part.get("thought"):
                        self.thought.append(part["text"])
                        self.emit("reasoning", text=part["text"])
                    else:
                        self.text.append(part["text"])
                        self.emit("content", text=part["text"])
                if isinstance(part.get("functionCall"), dict):
                    call: dict[str, object] = part["functionCall"]
                    args: dict[str, object] = call.get("args") or {}
                    self.calls.append(
                        (
                            f"gemini-{len(self.calls)}-{call['name']}",
                            call["name"],
                            args,
                        )
                    )
                    self.emit("tool_delta", text=compact(args))

    def build_result(self) -> dict[str, object]:
        stop: str | None = {
            "STOP": "tool_calls" if self.calls else "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
        }.get(self.finish)
        if stop is None:
            raise ChatError(
                (
                    "Gemini response is incomplete or blocked; no tool calls"
                    " executed"
                )
            )
        stats: dict[str, object] = {
            "prompt_tokens": self.usage.get("promptTokenCount"),
            "completion_tokens": self.usage.get("candidatesTokenCount"),
            "prompt_tokens_details": {
                "cached_tokens": self.usage.get("cachedContentTokenCount")
            },
        }
        self.emit("usage", usage=stats)
        return result(
            "".join(self.text),
            self.calls,
            stop,
            stats,
            reasoning="".join(self.thought),
            gemini_parts=self.preserved,
        )
