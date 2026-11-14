"""Bounded conversation loop for one model and one tool transport."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence

from mcp import Client
from mcp.types import Tool

from history_agent.inference.model import ChatError, Model, compact
from history_agent.runtime.capabilities import HarnessCapabilities
from history_agent.runtime.completion import complete_model
from history_agent.runtime.execution import ToolExecutor
from history_agent.runtime.identities import IdentityError
from history_agent.runtime.messages import tool_exchange
from history_agent.runtime.model_context import ModelContext
from history_agent.runtime.parsing import parse_message
from history_agent.runtime.recovery import ToolFailure, TurnRecovery
from history_agent.runtime.tools import ToolClient


class Harness(HarnessCapabilities):
    def __init__(
        self,
        client: Client | ToolClient,
        model: Model,
        tools: list[Tool],
        *,
        allow_write: bool = False,
        max_steps: int = 12,
        max_tool_retries: int = 8,
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
        self.max_tool_retries: int = max_tool_retries
        self.max_tool_result_bytes: int
        self.stats: bool
        self.max_tool_result_bytes, self.stats = max_tool_result_bytes, stats
        self.result_tool: str | None = None
        self.client_tool_names: set[str] = set()
        self.client_instructions: str = ""
        self.model_context = ModelContext()
        self.reset()

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
        quiet = getattr(self.on_event, "quiet", self.on_event is not None)
        if not self.stats or quiet:
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
            self.recovery = TurnRecovery(
                self.messages, max_retries=self.max_tool_retries
            )
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
            quiet=getattr(self.on_event, "quiet", self.on_event is not None),
            identity=id(self),
            tools=self.tools,
        )
        for step in range(1, self.max_steps + self.max_tool_retries + 1):
            result: dict[str, object] = await self.request(step)
            message: dict[str, object]
            calls: list[tuple[dict[str, object], dict[str, object]]]
            message, calls = self.parse_message(result)
            self.messages.append(message)
            try:
                answer: str | None = self.final_answer(message, calls)
            except IdentityError as error:
                failed = (
                    calls[0][0]
                    if calls
                    else {
                        "id": "invalid-verdict",
                        "type": "function",
                        "function": {
                            "name": self.result_tool or "history_read",
                        },
                    }
                )
                failed["function"]["arguments"] = "{}"
                self.recovery.reload(self, failed, ToolFailure(str(error)))
                continue
            if answer is not None:
                return self.recovery.finish(self, answer)
            if step - self.recovery.attempts == self.max_steps:
                raise ChatError(
                    "Model step limit reached before executing more tools; "
                    "use /reset for a new task"
                )
            for call, arguments in calls:
                try:
                    arguments = self.model_context.names.decode(arguments)
                except IdentityError as error:
                    call["function"]["arguments"] = compact(
                        {
                            "action": arguments.get("action"),
                            "arguments": {},
                        }
                    )
                    self.recovery.reload(self, call, ToolFailure(str(error)))
                    break
                call["function"]["arguments"] = compact(arguments)
                try:
                    contents: str = await executor.run(call, arguments)
                except ToolFailure as error:
                    self.recovery.reload(self, call, error)
                    break
                if call["function"]["name"] == "history_write" or (
                    self.allow_write
                    and arguments.get("action")
                    in ("review_batch", "review_diff")
                ):
                    self.recovery.write_executed = True
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
        if self.allowed and not self.recovery.stop_after_feedback:
            self.sync_client_tools()
        messages = self.model_context.messages(self.messages)
        tools = self.model_context.tools(self.tools)
        if self.recovery.stop_after_feedback:
            tools = []
        size = len(compact({"messages": messages, "tools": tools}).encode())
        if size > self.max_context_bytes:
            raise ChatError(
                "Context byte budget exceeded; use /reset, smaller source "
                "windows or a higher --max-context-bytes"
            )
        started: float = time.monotonic()
        self.emit("request_start", step=step, context_bytes=size)
        result: dict[str, object] = await complete_model(
            self.model, messages, tools, self.emit
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
            return self.model_context.verdict(message["content"] or "")
        if not self.result_tool or not any(
            call["function"]["name"] == self.result_tool for call, _ in calls
        ):
            return None
        if len(calls) != 1:
            raise ChatError(
                "Submit the final verdict alone, without other tool calls; "
                "no tools were executed"
            )
        return compact(self.model_context.names.decode(calls[0][1]))

    def parse_message(
        self,
        result: dict[str, object],
    ) -> tuple[
        dict[str, object], list[tuple[dict[str, object], dict[str, object]]]
    ]:
        return parse_message(
            result,
            set() if self.recovery.stop_after_feedback else self.allowed,
        )
