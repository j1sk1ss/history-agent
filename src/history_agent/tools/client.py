"""Compose host capabilities with a scoped history transport."""

import asyncio
from http.client import HTTPException

from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from history_agent.configuration.capabilities import CapabilitySettings
from history_agent.inference.model import compact
from history_agent.prompts import Prompts
from history_agent.runtime.tools import ToolClient
from history_agent.tools.code_context import (
    CodeContextRequest,
    CodeContextTools,
)
from history_agent.tools.documentation import (
    DocumentationRequest,
    DocumentationTools,
)
from history_agent.tools.questions import (
    AnswerUser,
    QuestionRequest,
    QuestionTools,
)


class CapabilityClient(ToolClient):
    def __init__(
        self,
        client,
        settings: CapabilitySettings,
        answer: AnswerUser | None,
    ):
        self.client, self.settings, self.answer = client, settings, answer

    def local_tools(self):
        tools = (
            self.client.local_tools()
            if isinstance(self.client, ToolClient)
            else []
        )
        if self.settings.questions:
            tools.append(QuestionTools.definition())
        if self.settings.documentation.enabled:
            tools.append(DocumentationTools.definition())
        if self.settings.lsp.enabled:
            tools.append(CodeContextTools.definition())
        return tools

    def prompt_instructions(self):
        return Prompts.get("tools.instructions")

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        try:
            if name == "ask_user" and self.settings.questions:
                data = await QuestionTools.ask(
                    QuestionRequest.model_validate(arguments),
                    self.answer,
                )
            elif (
                name == "library_docs" and self.settings.documentation.enabled
            ):
                request = DocumentationRequest.model_validate(arguments)
                data = await asyncio.to_thread(
                    DocumentationTools.execute,
                    request,
                    self.settings.documentation,
                )
            elif name == "code_context" and self.settings.lsp.enabled:
                data = await CodeContextTools.execute(
                    CodeContextRequest.model_validate(arguments),
                    self.client,
                    self.settings.lsp,
                )
            else:
                return await self.client.call_tool(name, arguments)
        except TimeoutError:
            data = {
                "error": "Tool request timed out; use smaller source windows"
            }
        except (
            ValueError,
            OSError,
            HTTPException,
            ValidationError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            data = {"error": str(error)}
        return CallToolResult(
            content=[TextContent(type="text", text=compact(data))],
            is_error="error" in data,
        )
