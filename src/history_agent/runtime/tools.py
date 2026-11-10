"""The tool transport contract shared by orchestration and review adapters."""

from abc import ABC, abstractmethod

from mcp.types import CallToolResult, Tool


class ToolClient(ABC):
    def local_tools(self) -> list[Tool]:
        """Additional tools currently supported by this transport."""
        return []

    def prompt_instructions(self) -> str:
        """Instructions for currently available local capabilities."""
        return ""

    @abstractmethod
    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> CallToolResult:
        """Execute one validated tool request."""
        raise NotImplementedError
