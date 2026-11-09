"""Constrain investigation reads to one warning's scope."""

from __future__ import annotations

import json

from mcp import Client
from mcp.types import CallToolResult

from history_agent.inference.model import ChatError
from history_agent.runtime.tools import ToolClient


class ItemClient(ToolClient):
    """
    Scope batch reads and record actual source evidence without retaining
    old conversations.
    """

    def __init__(
        self, client: Client, scope: dict[str, str | int],
        record: set[tuple[str, int, str]],
    ) -> None:
        self.client: Client
        self.scope: dict[str, str | int]
        self.record: set[tuple[str, int, str]]
        self.client, self.scope, self.record = client, scope, record
        self.snapshots: set[str] = {scope["snapshot_id"]}

    def observe(
        self, action: str | None, args: dict[str, object],
        result: dict[str, object],
    ) -> None:
        def overrides(value: object) -> None:
            if isinstance(value, dict):
                for key, entry in value.items():
                    if (
                        key == "snapshot_override"
                        and isinstance(entry, str)
                        and entry
                    ):
                        self.snapshots.add(entry)
                    else:
                        overrides(entry)
            elif isinstance(value, list):
                for entry in value:
                    overrides(entry)

        overrides(result)
        if action == "agent_source" and isinstance(result, dict):
            for line in result.get("lines", []):
                if isinstance(line, dict) and isinstance(
                    line.get("number"), int
                ):
                    self.record.add(
                        (args["file"], line["number"], args["snapshot_id"])
                    )

    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> CallToolResult:
        if name not in ("history_discover", "history_read"):
            raise ChatError(
                "Batch model cannot write reviews or start another job"
            )
        args: dict[str, object] = arguments.get("arguments", {})
        if not isinstance(args, dict):
            raise ChatError("Batch read arguments must be an object")
        if name == "history_read":
            action: str | None = arguments.get("action")
            assigned_checker: str | None = self.scope.get("checker_name")
            if assigned_checker and action in (
                "agent_checker_info",
                "agent_checker_documentation",
            ):
                requested: object = args.get("checker_name")
                if (
                    not isinstance(requested, str)
                    or requested.casefold() != assigned_checker.casefold()
                ):
                    raise ChatError(
                        "Batch checker documentation belongs to a "
                        "different item"
                    )
            scoped: set[str] = {
                "get_warning",
                "get_warning_trace",
                "get_warning_trace_and_role",
                "get_warnings",
                "get_file_tree",
                "get_tokens",
                "get_ref_tokens",
                "agent_source",
                "agent_resolve_symbol",
                "agent_find_warnings",
            }
            if action in scoped and "snapshot_id" not in args:
                args = {**args, "snapshot_id": self.scope["snapshot_id"]}
                arguments = {**arguments, "arguments": args}
            if (
                args.get("project_id", self.scope["project_id"])
                != self.scope["project_id"]
            ):
                raise ChatError("Batch read left the assigned project")
            if (
                args.get("stream_id", self.scope["stream_id"])
                != self.scope["stream_id"]
            ):
                raise ChatError("Batch read left the assigned branch")
            snapshot: str | None = args.get("snapshot_id")
            if (
                snapshot
                and snapshot not in self.snapshots
                and not (
                    snapshot == "HEAD"
                    and arguments.get("action") == "get_warning"
                )
            ):
                raise ChatError(
                    "Batch read used a snapshot outside the warning/trace"
                )
        result: CallToolResult = await self.client.call_tool(name, arguments)
        if name == "history_read" and not result.is_error:
            try:
                data: dict[str, object] = json.loads(
                    "".join(
                        part.text
                        for part in result.content
                        if part.type == "text"
                    )
                )
                self.observe(arguments.get("action"), args, data)
            except (ValueError, KeyError):
                pass
        return result
