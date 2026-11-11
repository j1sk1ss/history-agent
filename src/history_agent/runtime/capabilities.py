"""Configure the tools and initial context exposed by a harness."""

from mcp.types import Tool

from history_agent.inference.model import compact
from history_agent.prompts import Prompts
from history_agent.review.verdict import FINAL_TOOL
from history_agent.runtime.tools import ToolClient


class HarnessCapabilities:
    def sync_client_tools(self) -> None:
        if not isinstance(self.client, ToolClient):
            return
        self.tools = [
            tool
            for tool in self.tools
            if tool["function"]["name"] not in self.client_tool_names
        ]
        self.allowed.difference_update(self.client_tool_names)
        extra: list[Tool] = self.client.local_tools()
        self.client_tool_names = {tool.name for tool in extra}
        self.tools.extend(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in extra
        )
        self.allowed.update(self.client_tool_names)
        content: str = self.messages[0]["content"]
        if self.client_instructions:
            content = content.replace(" " + self.client_instructions, "", 1)
        self.client_instructions = self.client.prompt_instructions()
        if self.client_instructions:
            content += " " + self.client_instructions
        self.messages[0]["content"] = content

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
                    "description": Prompts.get("runtime.final_tool"),
                    "parameters": schema,
                },
            }
        )
        self.allowed.add(FINAL_TOOL)

    def reset(self) -> None:
        mode: str = (
            Prompts.get("runtime.write_enabled")
            if self.allow_write
            else Prompts.get("runtime.read_only")
        )
        self.messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": Prompts.get("runtime.system") + " " + mode,
            }
        ]
        self.sync_client_tools()
        self.blocked: bool = False

    def context_bytes(self) -> int:
        return len(
            compact({"messages": self.messages, "tools": self.tools}).encode(
                "utf-8"
            )
        )
