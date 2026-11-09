"""The tool transport contract shared by orchestration and review adapters."""

from abc import ABC, abstractmethod

from mcp.types import CallToolResult


class ToolClient(ABC):
    @abstractmethod
    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> CallToolResult:
        """Execute one validated tool request."""
        raise NotImplementedError
