"""Native responses requests and response state."""

from __future__ import annotations

from collections.abc import Callable

from history_agent.inference.conversations import (
    responses_input,
)
from history_agent.inference.model import ChatError, Model
from history_agent.inference.transport import _records, _request, result


def responses(
    model: Model,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    emit: Callable[..., None],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model.name,
        "input": responses_input(messages),
        "stream": model.stream,
        "max_output_tokens": model.max_output_tokens,
        "tools": [
            {
                "type": "function",
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"]["parameters"],
            }
            for t in tools
        ],
    }
    if model.provider == "codex":
        payload["reasoning"] = {"summary": "auto"}
        payload["include"] = ["reasoning.encrypted_content"]
    headers: dict[str, str] = {"Authorization": "Bearer " + (model.key or "")}
    with _request(
        model.base,
        "/responses",
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        state: ResponsesState = ResponsesState(emit)
        for item in _records(response, model.stream):
            state.feed(item)
        return state.build_result()


class ResponsesState:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit: Callable[..., None] = emit
        self.text: list[str]
        self.thought: list[str]
        self.calls: list[tuple[str, str, str | dict[str, object]]]
        self.current: dict[str, dict[str, object]]
        self.text, self.thought, self.calls, self.current = ([], [], [], {})
        self.output_items: dict[int, dict[str, object]] = {}
        self.completed_output: list[dict[str, object]] | None = None
        self.done: bool
        self.usage: dict[str, object]
        self.done, self.usage = (False, {})

    def feed(self, item: dict[str, object]) -> None:
        typ: str | None = item.get("type")
        if typ in (
            "response.failed",
            "response.incomplete",
            "error",
        ) or item.get("error"):
            raise ChatError(
                "Responses API returned a failed/incomplete response"
            )
        if typ == "response.output_text.delta":
            self.on_response_output_text_delta(item)
            return
        if typ == "response.reasoning_summary_text.delta":
            self.on_response_reasoning_summary_text_delta(item)
            return
        if typ == "response.function_call_arguments.delta":
            self.on_response_function_call_arguments_delta(item)
            return
        if typ == "response.output_item.added":
            self.on_response_output_item_added(item)
            return
        if typ == "response.output_item.done":
            self.on_response_output_item_done(item)
            return
        if typ == "response.completed":
            self.on_response_completed(item)
            return
        if "output" in item and isinstance(item["output"], list):
            self.on_nonstream(item)

    def on_response_output_text_delta(self, item: dict[str, object]) -> None:
        t: str = item.get("delta", "")
        self.text.append(t)
        self.emit("content", text=t)

    def on_response_reasoning_summary_text_delta(
        self, item: dict[str, object]
    ) -> None:
        t = item.get("delta", "")
        self.thought.append(t)
        self.emit("reasoning", text=t)

    def on_response_function_call_arguments_delta(
        self, item: dict[str, object]
    ) -> None:
        entry: dict[str, object] = self.current.setdefault(
            item["item_id"], {"arguments": ""}
        )
        entry["arguments"] += item.get("delta", "")
        self.emit("tool_delta", text=item.get("delta", ""))

    def on_response_output_item_added(self, item: dict[str, object]) -> None:
        b: dict[str, object] = item.get("item", {})
        if b.get("type") == "function_call":
            self.current[b["id"]] = {**b, "arguments": b.get("arguments", "")}

    def on_response_output_item_done(self, item: dict[str, object]) -> None:
        b = item.get("item", {})
        self.output_items[item.get("output_index", len(self.output_items))] = b
        if b.get("type") == "function_call":
            self.current[b["id"]] = b

    def on_response_completed(self, item: dict[str, object]) -> None:
        self.done = True
        self.completed_output = item.get("response", {}).get("output")
        self.usage = item.get("response", {}).get("usage") or {}

    def on_nonstream(self, item: dict[str, object]) -> None:
        self.done = True
        self.usage = item.get("usage") or {}
        self.completed_output = item["output"]
        for b in item["output"]:
            if b.get("type") == "function_call":
                self.current[b.get("id", b["call_id"])] = b
            elif b.get("type") == "message":
                for part in b.get("content", []):
                    if part.get("type") == "output_text":
                        t = part.get("text", "")
                        self.text.append(t)
                        self.emit("content", text=t)
            elif b.get("type") == "reasoning":
                for summary in b.get("summary", []):
                    t = summary.get("text", "")
                    self.thought.append(t)
                    self.emit("reasoning", text=t)

    def build_result(self) -> dict[str, object]:
        if not self.done:
            raise ChatError(
                "Responses stream ended without completion; no tools executed"
            )
        retained: list[dict[str, object]] = (
            self.completed_output
            if isinstance(self.completed_output, list)
            else [
                self.output_items[index] for index in sorted(self.output_items)
            ]
        )
        if not self.text:
            for item in retained:
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if block.get("type") == "output_text":
                            chunk: str = block.get("text", "")
                            self.text.append(chunk)
                            self.emit("content", text=chunk)
        if not self.thought:
            for item in retained:
                if item.get("type") == "reasoning":
                    for part in item.get("summary", []):
                        chunk = part.get("text", "")
                        self.thought.append(chunk)
                        self.emit("reasoning", text=chunk)
        if not self.current:
            for item in retained:
                if item.get("type") == "function_call":
                    self.current[item.get("id", item["call_id"])] = item
        for b in self.current.values():
            if b.get("type") == "function_call":
                self.calls.append(
                    (b["call_id"], b["name"], b.get("arguments", "{}"))
                )
        stats: dict[str, object] = {
            "prompt_tokens": self.usage.get("input_tokens"),
            "completion_tokens": self.usage.get("output_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": (
                    self.usage.get("input_tokens_details") or {}
                ).get("cached_tokens")
            },
        }
        self.emit("usage", usage=stats)
        normalized: dict[str, object] = result(
            "".join(self.text),
            self.calls,
            "tool_calls" if self.calls else "stop",
            stats,
            reasoning="".join(self.thought),
        )
        if retained:
            normalized["choices"][0]["message"]["responses_items"] = retained
        return normalized
