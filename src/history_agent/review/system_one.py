"""Expose a working System 1 classifier as an agent-callable adviser."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError

from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import Field

from history_agent.contracts import StrictModel
from history_agent.inference.model import ChatError, compact
from history_agent.inference.transport import _records, _request
from history_agent.prompts import Prompts
from history_agent.review.classification import STATUSES
from history_agent.review.system_one_context import (
    load_warning_context as load_warning_context,
)
from history_agent.review.system_one_context import (
    warning_context as warning_context,
)
from history_agent.runtime.tools import ToolClient
from history_agent.tools.registry import ToolRegistry

CRITERIA: dict[str, str] = {
    key: Prompts.get("system_one." + key.lower()) for key in STATUSES
}


@dataclass
class SystemOne:
    url: str
    model: str
    timeout: float
    available: bool = field(default=False, init=False)
    max_input_bytes: int = 65536

    def probe(self) -> bool:
        self.available = (
            self.classify(
                "Connectivity test: int main(void) { return 0; }. "
                "Check whether this code divides by zero.",
                timeout=min(self.timeout, 5),
            )
            is not None
        )
        return self.available

    def classify(
        self,
        state: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, object] | None:
        if len(state.encode("utf-8")) > self.max_input_bytes:
            return None
        try:
            with _request(
                self.url,
                "",
                {
                    "model": self.model,
                    "state": state,
                    "questions": {
                        "sentiment": {
                            "type": "choice",
                            "instructions": Prompts.get(
                                "system_one.instructions"
                            ),
                            "criteria": {
                                key: Prompts.get("system_one." + key.lower())
                                for key in STATUSES
                            },
                        }
                    },
                },
                {},
                timeout if timeout is not None else self.timeout,
                stream=False,
            ) as response:
                data = next(_records(response, False))
                answer = data["answers"]["sentiment"]
            choice = answer.get("choice")
            score = answer.get("confidence")
            if (
                choice not in STATUSES
                or type(score) not in (int, float)
                or not 0 <= score <= 100
            ):
                return None
            confidence: float = score / 100 if score > 1 else float(score)
            result = {
                "model": self.model,
                "classification": choice,
                "confidence": confidence,
                "can_skip_investigation": confidence >= 0.7,
            }
            explanation = answer.get("explanation", answer.get("reason"))
            if isinstance(explanation, str) and explanation.strip():
                result["explanation"] = explanation[:4000]
            return result
        except (
            URLError,
            OSError,
            HTTPException,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            StopIteration,
            ChatError,
        ) as error:
            if isinstance(error, HTTPError):
                error.close()
            return None


class SystemOneRequest(StrictModel):
    code: str = Field(min_length=1)
    environment: str = ""


ADVISER: Tool = ToolRegistry.create(
    "ask_system_one", Prompts.get("tools.system_one"), SystemOneRequest
)


class SystemOneClient(ToolClient):
    def __init__(
        self,
        client: Client | ToolClient,
        system_one: SystemOne | None,
    ) -> None:
        self.client = client
        self.system_one = system_one
        self.preflight_done = False
        self.context: Callable[[], dict[str, object]] | None = None
        self.load_context: (
            Callable[[], Awaitable[dict[str, object]]] | None
        ) = None

    def local_tools(self) -> list[Tool]:
        inherited = (
            self.client.local_tools()
            if isinstance(self.client, ToolClient)
            else []
        )
        return inherited + (
            [ADVISER]
            if (self.system_one and self.system_one.available)
            else []
        )

    def prompt_instructions(self) -> str:
        inherited = (
            self.client.prompt_instructions()
            if isinstance(self.client, ToolClient)
            else ""
        )
        advice = (
            Prompts.get("runtime.system_one").format(
                model=self.system_one.model
            )
            if (
                self.system_one
                and self.system_one.available
                and not self.preflight_done
            )
            else ""
        )
        return " ".join(part for part in (inherited, advice) if part)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        if name != ADVISER.name:
            return await self.client.call_tool(name, arguments)
        code = arguments.get("code")
        environment = arguments.get("environment", "")
        metadata = self.context() if self.context else {}
        if (
            set(arguments) - {"code", "environment"}
            or not isinstance(code, str)
            or not code.strip()
            or not isinstance(environment, str)
        ):
            data = {"error": "Provide only code and environment as text."}
        elif not self.system_one or not self.system_one.available:
            data = {"error": "Adviser unavailable; investigate normally."}
        else:
            if self.load_context:
                metadata = await self.load_context()
            complete = (
                all(
                    metadata.get(key)
                    for key in (
                        "warning_description",
                        "tp_conditions",
                    )
                )
                and metadata.get("trace_complete", True)
                and metadata.get("tp_complete", True)
            )
            state = compact(
                {
                    "code": code,
                    "environment": environment,
                    "warning_description": metadata.get("warning_description")
                    or "Assess the supplied code for a genuine defect.",
                    "tp_conditions": metadata.get("tp_conditions")
                    or CRITERIA["TP"],
                    "trace": metadata.get("trace", []),
                }
            )
            if len(state.encode()) > self.system_one.max_input_bytes:
                data = {
                    "error": "Input budget exceeded; investigate manually."
                }
            else:
                data = await asyncio.to_thread(self.system_one.classify, state)
            if data is None:
                self.system_one.available = False
                data = {"error": "Adviser unavailable; investigate normally."}
            elif not complete:
                data["can_skip_investigation"] = False
                data["guidance"] = (
                    "Preliminary code assessment: warning-specific context "
                    "or trace source is incomplete. "
                    "Use the result as an investigation hint."
                )
        return CallToolResult(
            content=[TextContent(type="text", text=compact(data))],
            is_error=False,
        )
