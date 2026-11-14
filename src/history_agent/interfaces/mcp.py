"""MCP transport shared by Codex, Claude and local-model clients."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from importlib.resources import files

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from history_agent.history.client import HistoryClient, HistoryError, Settings
from history_agent.prompts import Prompts
from history_agent.skills.catalog import catalog as skill_catalog
from history_agent.skills.catalog import read_skill


def workflow() -> str:
    return (
        files("history_agent")
        .joinpath("skills/history-review/SKILL.md")
        .read_text(encoding="utf-8")
    )


def api_reference() -> str:
    return (
        files("history_agent")
        .joinpath("skills/history-review/references/agent-api.md")
        .read_text(encoding="utf-8")
    )


class HistoryTools:
    def __init__(self, client: HistoryClient) -> None:
        self.client: HistoryClient = client

    def execute(
        self,
        operation: Callable[..., dict[str, object]],
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        try:
            return operation(*args, **kwargs)
        except HistoryError as error:
            raise ToolError(str(error)) from error

    def history_discover(self, action: str | None = None) -> dict[str, object]:
        """
        List history actions, get one action's schema, or get the
        'workflow'/'api' instructions.
        """
        if action == "skills":
            return {"skills": skill_catalog()}
        if action and action.startswith("skill:"):
            return {
                "skill": action.split(":", 1)[1],
                "instructions": read_skill(action.split(":", 1)[1]),
            }
        if action == "workflow":
            return {"instructions": workflow()}
        if action == "api":
            return {"instructions": api_reference()}
        return self.execute(self.client.discover, action)

    def history_read(
        self,
        action: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        """
        Read warnings, traces, source or DXR. Get the action's schema with
        history_discover first.
        """
        return self.execute(self.client.execute, action, arguments)

    def history_current(
        self,
        project_id: int | None = None,
        project_name: str | None = None,
        stream_id: str | None = None,
    ) -> dict[str, object]:
        """
        Select a default project/branch/snapshot; alphabetic projects,
        first snapshot, no searching by the model.
        """
        args: dict[str, str | int] = {
            key: value
            for key, value in {
                "project_id": project_id,
                "project_name": project_name,
                "stream_id": stream_id,
            }.items()
            if value is not None
        }
        return self.execute(self.client.execute, "current", args)

    def history_write(
        self,
        action: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        """
        Save an authorized warning review using agent_set_review; requires
        classification and explanation.
        """
        return self.execute(self.client.execute, action, arguments, write=True)


def review_workflow() -> str:
    return workflow()


def http_api_reference() -> str:
    return api_reference()


def review_warning(
    project_id: str,
    warning_id: str,
    branch: str = "master",
) -> str:
    """
    Investigate a warning with the shared history tools. Save a review
    only when authorized.
    """
    return (
        Prompts.get("interfaces.review_warning").format(
            warning_id=repr(warning_id),
            project_id=repr(project_id),
            branch=repr(branch),
        )
        + workflow()
    )


def create_server(
    client: HistoryClient,
    *,
    named_ids: bool = False,
) -> MCPServer:
    instructions = Prompts.get("interfaces.instructions")
    tools = HistoryTools(client)
    if named_ids:
        from history_agent.interfaces.named_tools import (
            NamedHistoryTools,
            named_instructions,
        )

        tools = NamedHistoryTools(tools)
        instructions += " " + named_instructions()
    server: MCPServer = MCPServer(
        "history-agent",
        version="0.1.0",
        instructions=instructions,
    )
    readonly: ToolAnnotations = ToolAnnotations(
        read_only_hint=True, open_world_hint=False
    )
    for handler in (
        tools.history_discover,
        tools.history_read,
        tools.history_current,
    ):
        server.tool(annotations=readonly)(handler)
    server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )(tools.history_write)
    server.resource("history://workflow", mime_type="text/markdown")(
        review_workflow
    )
    server.resource("history://api", mime_type="text/markdown")(
        http_api_reference
    )
    server.prompt()(tools.review_warning if named_ids else review_warning)
    return server


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--transport", choices=("stdio", "streamable-http"), default="stdio"
    )
    parser.add_argument(
        "--prompts-file",
        default=os.environ.get("HISTORY_AGENT_PROMPTS"),
        help="JSON overrides for the shared prompt catalog",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Local HTTP MCP port (default: 8765)",
    )
    options: argparse.Namespace = parser.parse_args()
    Prompts.configure(options.prompts_file)
    server: MCPServer = create_server(
        HistoryClient(Settings.from_env()),
        named_ids=True,
    )
    if options.transport == "stdio":
        server.run()
    else:
        server.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=options.port,
            json_response=True,
        )


if __name__ == "__main__":
    main()
