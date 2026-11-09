"""Bounded conversation loop for one model and one tool transport."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence

from mcp import Client
from mcp.types import Tool

from history_agent.inference.model import ChatError, Model, compact
from history_agent.review.verdict import FINAL_TOOL
from history_agent.runtime.completion import complete_model
from history_agent.runtime.execution import ToolExecutor
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.parsing import parse_message
from history_agent.runtime.prompts import SYSTEM
from history_agent.runtime.tools import ToolClient


class Harness:
    def __init__(
        self,
        client: Client | ToolClient,
        model: Model,
        tools: list[Tool],
        *,
        allow_write: bool = False,
        max_steps: int = 12,
        max_context_bytes: int = 65536,
        max_tool_result_bytes: int = 16384,
        stats: bool = False,
        on_event: Callable[..., None] | None = None,
    ) -> None:
        self.client: Client | ToolClient = client
        self.model: Model = model
        self.on_event: Callable[..., None] | None = on_event
        self.allow_write: bool = allow_write
        allowed: set[str] = {
            "history_discover",
            "history_read",
            "history_current",
        } | ({"history_write"} if allow_write else set())
        self.tools: list[dict[str, object]] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
            if tool.name in allowed
        ]
        self.allowed: set[str] = {
            tool["function"]["name"] for tool in self.tools
        }
        if not {"history_discover", "history_read"} <= self.allowed:
            raise ChatError(
                "MCP server must provide history_discover and history_read"
            )
        self.max_steps: int
        self.max_context_bytes: int
        self.max_steps, self.max_context_bytes = max_steps, max_context_bytes
        self.max_tool_result_bytes: int
        self.stats: bool
        self.max_tool_result_bytes, self.stats = max_tool_result_bytes, stats
        self.result_tool: str | None = None
        self.reset()

    def set_result_schema(self, schema: dict[str, object]) -> None:
        self.result_tool = FINAL_TOOL
        self.tools = [
            tool
            for tool in self.tools
            if tool["function"]["name"] != FINAL_TOOL
        ]
        self.tools.append(
            {
                "type": "function",
                "function": {
                    "name": FINAL_TOOL,
                    "description": (
                        "Finish the assigned warning investigation with "
                        "this structured verdict. This returns a proposal "
                        "to the host; it does not save a review. Call "
                        "alone, after reading source evidence."
                    ),
                    "parameters": schema,
                },
            }
        )
        self.allowed.add(FINAL_TOOL)

    def reset(self) -> None:
        mode: str = (
            "Review writes are enabled, but require the user's request."
            if self.allow_write
            else "This session is read-only. Review writes are disabled."
        )
        self.messages: list[dict[str, object]] = [
            {"role": "system", "content": SYSTEM + " " + mode}
        ]
        self.blocked: bool = False

    def context_bytes(self) -> int:
        return len(
            compact({"messages": self.messages, "tools": self.tools}).encode(
                "utf-8"
            )
        )

    def emit(self, kind: str, **data: object) -> None:
        if self.on_event:
            self.on_event(kind, **data)

    def prefetch(
        self,
        prefetched: Sequence[tuple[str, dict[str, object], dict[str, object]]],
    ) -> None:
        for index, (action, arguments, data) in enumerate(prefetched):
            contents: str = compact(data)
            if len(contents.encode("utf-8")) > self.max_tool_result_bytes:
                raise ChatError(
                    "Resolved history link exceeds the tool-result byte budget"
                )
            call_id: str = f"host-read-{len(self.messages)}-{index}"
            self.messages.extend(
                tool_exchange(call_id, action, arguments, contents)
            )
            self.emit(
                "tool_prefetch",
                action=action,
                arguments=arguments,
                response=contents,
            )

    def report_usage(
        self,
        result: dict[str, object],
        step: int,
        size: int,
        started: float,
    ) -> None:
        if not self.stats or self.on_event:
            return
        usage: dict[str, object] = result.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        details: dict[str, object] = usage.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}
        print(
            f"step={step} context_bytes={size} "
            f"prompt_tokens={usage.get('prompt_tokens', '?')} "
            f"cached_tokens={details.get('cached_tokens', '?')} "
            "completion_tokens="
            f"{usage.get('completion_tokens', '?')} "
            f"request_s={time.monotonic() - started:.2f}",
            file=sys.stderr,
        )

    async def ask(
        self,
        text: str,
        *,
        prefetched: Sequence[
            tuple[str, dict[str, object], dict[str, object]]
        ] = (),
    ) -> str:
        if self.blocked:
            error: ChatError = ChatError(
                "Session stopped after an error. Inspect any write outcome; "
                "use /reset for a fresh context."
            )
            self.emit("failure", message=str(error))
            raise error
        self.messages.append({"role": "user", "content": text})
        try:
            self.prefetch(prefetched)
            return await self.run_turn()
        except BaseException as error:
            self.blocked = True
            message: str = (
                str(error)
                if isinstance(error, ChatError)
                else (
                    "Session interrupted; inspect any pending review write "
                    "before retrying."
                )
            )
            self.emit("failure", message=message)
            raise

    async def run_turn(self) -> str:
        executor: ToolExecutor = ToolExecutor(
            self.client,
            self.emit,
            self.max_tool_result_bytes,
            quiet=self.on_event is not None,
            identity=id(self),
        )
        for step in range(1, self.max_steps + 1):
            result: dict[str, object] = await self.request(step)
            message: dict[str, object]
            calls: list[tuple[dict[str, object], dict[str, object]]]
            message, calls = self.parse_message(result)
            self.messages.append(message)
            answer: str | None = self.final_answer(message, calls)
            if answer is not None:
                return answer
            if step == self.max_steps:
                raise ChatError(
                    "Model step limit reached before executing more tools; "
                    "use /reset for a new task"
                )
            for call, arguments in calls:
                contents: str = await executor.run(call, arguments)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": contents,
                    }
                )
                self.emit(
                    "tool_result",
                    name=call["function"]["name"],
                    action=arguments.get("action"),
                    event_id=executor.event_id(call),
                    arguments=arguments.get("arguments", arguments),
                    response=contents,
                )
        raise ChatError("Model step limit reached")

    async def request(self, step: int) -> dict[str, object]:
        size: int = self.context_bytes()
        if size > self.max_context_bytes:
            raise ChatError(
                "Context byte budget exceeded; use /reset, smaller source "
                "windows or a higher --max-context-bytes"
            )
        started: float = time.monotonic()
        self.emit("request_start", step=step, context_bytes=size)
        result: dict[str, object] = await complete_model(
            self.model, self.messages, self.tools, self.emit
        )
        usage: object = result.get("usage")
        self.emit(
            "request_end", usage=usage if isinstance(usage, dict) else {}
        )
        self.report_usage(result, step, size, started)
        return result

    def final_answer(
        self,
        message: dict[str, object],
        calls: list[tuple[dict[str, object], dict[str, object]]],
    ) -> str | None:
        if not calls:
            return message["content"] or ""
        if not self.result_tool or not any(
            call["function"]["name"] == self.result_tool for call, _ in calls
        ):
            return None
        if len(calls) != 1:
            raise ChatError(
                "Submit the final verdict alone, without other tool calls; "
                "no tools were executed"
            )
        return compact(calls[0][1])

    def parse_message(
        self,
        result: dict[str, object],
    ) -> tuple[
        dict[str, object], list[tuple[dict[str, object], dict[str, object]]]
    ]:
        return parse_message(result, self.allowed)
