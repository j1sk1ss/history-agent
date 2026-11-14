"""Execute one tool call and enforce response and write guarantees."""

import json
import sys
import time
from collections.abc import Callable

from mcp import Client
from mcp.types import CallToolResult

from history_agent.inference.model import ChatError, compact
from history_agent.runtime.action_catalog import (
    refresh_actions,
    validate_action,
)
from history_agent.runtime.recovery import ToolFailure
from history_agent.runtime.tools import ToolClient


class ToolExecutor:
    def __init__(
        self,
        client: Client | ToolClient,
        emit: Callable[..., None],
        byte_limit: int,
        *,
        quiet: bool,
        identity: int,
        tools: list[dict[str, object]] | None = None,
    ) -> None:
        self.client: Client | ToolClient = client
        self.emit: Callable[..., None] = emit
        self.byte_limit: int = byte_limit
        self.quiet: bool = quiet
        self.identity: int = identity
        self.tools = tools or []

    def event_id(self, call: dict[str, object]) -> str:
        return f"{self.identity}:{call['id']}"

    async def run(
        self,
        call: dict[str, object],
        arguments: dict[str, object],
    ) -> str:
        name: str = call["function"]["name"]
        started: float = time.monotonic()
        event_id: str = self.event_id(call)
        self.emit(
            "tool_start",
            name=name,
            action=arguments.get("action"),
            event_id=event_id,
            arguments=arguments,
        )
        if not self.quiet:
            print("tool:", name, file=sys.stderr)
        uncertain = name == "history_write" or arguments.get("action") in (
            "review_batch",
            "review_diff",
        )
        try:
            validate_action(name, arguments, self.tools)
            outcome: CallToolResult = await self.client.call_tool(
                name, arguments
            )
        except Exception as error:
            self.emit(
                "tool_end",
                name=name,
                event_id=event_id,
                error=True,
                seconds=time.monotonic() - started,
                bytes=0,
            )
            self.error_result(call, arguments, compact({
                "error": str(error) or type(error).__name__,
                **(error.context if isinstance(error, ToolFailure) else {}),
            }))
            if isinstance(error, ToolFailure):
                raise
            raise ToolFailure(
                str(error) or type(error).__name__,
                uncertain_write=uncertain,
            ) from error
        self.emit(
            "tool_end",
            name=name,
            event_id=event_id,
            error=bool(outcome.is_error),
            seconds=time.monotonic() - started,
            bytes=sum(
                len(part.text.encode("utf-8"))
                for part in outcome.content
                if part.type == "text"
            ),
        )
        try:
            contents = self.contents(name, outcome)
            data = json.loads(contents)
        except ValueError:
            data = None
        except ChatError as error:
            self.error_result(call, arguments, compact({"error": str(error)}))
            raise ToolFailure(str(error), uncertain_write=uncertain) from error
        if outcome.is_error or (isinstance(data, dict) and data.get("error")):
            self.error_result(call, arguments, contents)
            rejected = isinstance(data, dict) and data.get("write_rejected")
            raise ToolFailure(
                contents, uncertain_write=uncertain and not rejected
            )
        if name == "history_write":
            self.verify_write(outcome)
        if name == "history_discover" and isinstance(data, dict):
            refresh_actions(self.tools, data)
        return contents

    def error_result(self, call: dict, arguments: dict, response: str) -> None:
        self.emit(
            "tool_result", name=call["function"]["name"],
            action=arguments.get("action"), event_id=self.event_id(call),
            arguments=arguments.get("arguments", arguments),
            response=response, error=True,
        )

    def verify_write(self, outcome: CallToolResult) -> None:
        if outcome.is_error:
            raise ChatError(
                "Review write returned an error; inspect warning/review "
                "history before any retry. Session stopped."
            )
        if not self.quiet:
            print(
                "history_write completed; verify the saved review.",
                file=sys.stderr,
            )

    def contents(self, name: str, outcome: CallToolResult) -> str:
        parts: list[str] = [
            part.text for part in outcome.content if part.type == "text"
        ]
        if len(parts) != len(outcome.content):
            raise ChatError("Unexpected non-text MCP result")
        contents: str = "\n".join(parts)
        try:
            contents = compact(json.loads(contents))
        except ValueError:
            pass
        if len(contents.encode("utf-8")) <= self.byte_limit:
            return contents
        if name == "history_write":
            raise ChatError(
                "Write completed but its result exceeds the byte budget; "
                "inspect history before continuing"
            )
        return compact(
            {
                "error": (
                    "Tool result exceeds the byte budget and was "
                    "omitted completely. Request a smaller "
                    "page/source window or a specific action schema."
                )
            }
        )
