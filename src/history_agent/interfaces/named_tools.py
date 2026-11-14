"""Named history references for models connecting directly over MCP."""

from mcp.server.mcpserver.exceptions import ToolError

from history_agent.inference.model import ChatError
from history_agent.prompts import Prompts
from history_agent.runtime.identities import INSTRUCTIONS
from history_agent.runtime.model_context import ModelContext
from history_agent.skills.catalog import read_skill


class NamedHistoryTools:
    def __init__(self, raw):
        self.raw = raw
        self.context = ModelContext()

    def execute(self, operation, *args, **kwargs):
        try:
            return self.context.packet(operation(*args, **kwargs))
        except (ChatError, ToolError) as error:
            detail = self.context.names.redact_error(str(error))
            raise ToolError(detail) from error

    def history_discover(self, action: str | None = None) -> dict[str, object]:
        """Discover actions and schemas; ID fields use returned host names."""
        return self.execute(self.raw.history_discover, action)

    def history_read(self, action: str, arguments: dict[str, object]) -> dict:
        """Read history using returned host names, never raw IDs."""
        try:
            raw = self.context.names.decode(arguments)
        except ChatError as error:
            raise ToolError(str(error)) from error
        return self.execute(self.raw.history_read, action, raw)

    def history_current(
        self,
        project_id: str | None = None,
        project_name: str | None = None,
        stream_id: str | None = None,
    ) -> dict:
        """Resolve scope; optional ID constraints take existing host names."""
        try:
            raw = self.context.names.decode(
                {
                    key: value
                    for key, value in {
                        "project_id": project_id,
                        "project_name": project_name,
                        "stream_id": stream_id,
                    }.items()
                    if value is not None
                }
            )
        except ChatError as error:
            raise ToolError(str(error)) from error
        return self.execute(self.raw.history_current, **raw)

    def history_write(self, action: str, arguments: dict[str, object]) -> dict:
        """Save an authorized review; use returned host names in ID fields."""
        try:
            raw = self.context.names.decode(arguments)
        except ChatError as error:
            raise ToolError(str(error)) from error
        return self.execute(self.raw.history_write, action, raw)

    def review_warning(
        self,
        project_id: str,
        warning_id: str,
        branch: str | None = None,
    ) -> str:
        """Investigate a warning using previously returned host names."""
        names = {"project_id": project_id, "warning_id": warning_id}
        if branch is not None:
            names["stream_id"] = branch
        self.context.names.decode(names)
        return Prompts.get("interfaces.review_warning").format(
            project_id=project_id,
            warning_id=warning_id,
            branch=branch or "the branch returned by history_current",
        ) + read_skill("history-review")


def named_instructions() -> str:
    return INSTRUCTIONS
