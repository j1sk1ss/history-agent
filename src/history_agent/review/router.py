"""Route review actions through the local batch runner."""

from __future__ import annotations

import asyncio
import json

from jsonschema import Draft202012Validator
from mcp import Client
from mcp.types import CallToolResult, TextContent

from history_agent.history.client import HistoryError
from history_agent.history.current import resolve_current
from history_agent.history.links import find_history_urls
from history_agent.inference.model import ChatError, compact
from history_agent.review.batch import BatchRunner
from history_agent.review.schemas import (
    BATCH_ACTION,
    BATCH_META,
    DIFF_ACTION,
    DIFF_META,
    DIFF_SCHEMA,
)
from history_agent.runtime.tools import ToolClient


def reply(data: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=compact(data))], is_error=False
    )


class BatchRouter(ToolClient):
    """
    Keep the three-tool interface; discover/execute the local action
    lazily.
    """

    def __init__(self, client: Client, runner: BatchRunner) -> None:
        self.client: Client
        self.runner: BatchRunner
        self.client, self.runner = client, runner
        self.completed: dict[str, object] | None = None
        self.user_request: str = ""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        action: str | None = arguments.get("action")
        if action in (BATCH_ACTION, DIFF_ACTION):
            return await self.run_batch(name, action, arguments)
        result = await self.client.call_tool(name, arguments)

        if name == "history_discover" and not action and not result.is_error:
            data: dict[str, object] = json.loads(
                "".join(
                    part.text for part in result.content if part.type == "text"
                )
            )
            data["actions"].append(
                {
                    "name": BATCH_ACTION,
                    "description": BATCH_META["description"],
                    "tool": "history_read/history_write",
                }
            )
            data["actions"].append(
                {
                    "name": DIFF_ACTION,
                    "description": DIFF_META["description"],
                    "tool": "history_read/history_write",
                }
            )
            return reply(data)

        return result

    async def run_batch(
        self,
        name: str,
        action: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        if name == "history_discover":
            return reply(DIFF_META if action == DIFF_ACTION else BATCH_META)

        if name not in ("history_read", "history_write"):
            raise ChatError("Unsupported batch tool")

        raw: dict[str, object] = arguments.get("arguments", {})

        if not isinstance(raw, dict):
            raise ChatError("review_batch arguments must be an object")

        if action == DIFF_ACTION and not Draft202012Validator(
            DIFF_SCHEMA
        ).is_valid(raw):
            raise ChatError(
                "Invalid review_diff arguments; specify current/base "
                "snapshots or only job_id to resume"
            )

        values: dict[str, object] = dict(raw)

        values = await self.default_scope(action, values)
        if "job_id" not in values and self.user_request:
            instructions: str = values.get("instructions", "")
            values["instructions"] = self.user_request + (
                "\nRetained project constraints: " + instructions
                if instructions and instructions != self.user_request
                else ""
            )

        result: dict[str, object] | CallToolResult = await self.runner.run(
            values,
            write=self.runner.allow_write or name == "history_write",
        )

        self.completed = result

        return reply(result)

    async def default_scope(
        self,
        action: str,
        values: dict[str, object],
    ) -> dict[str, object]:
        if (
            action == BATCH_ACTION
            and "job_id" not in values
            and any(
                key not in values
                for key in ("project_id", "stream_id", "snapshot_id")
            )
        ):
            if find_history_urls(self.user_request):
                raise ChatError(
                    "A history URL was provided: use the resolved "
                    "link scope, not"
                    " current defaults"
                )
            defaults: dict[str, object] = await asyncio.to_thread(
                resolve_current,
                self.runner.history.execute,
                {
                    key: values[key]
                    for key in ("project_id", "stream_id")
                    if key in values
                },
                HistoryError,
            )
            values = {**defaults["scope"], **values}

        return values
