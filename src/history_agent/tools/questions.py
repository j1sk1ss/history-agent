"""Clarifications keep the current tool call pending until the host replies."""

from collections.abc import Awaitable, Callable

from pydantic import Field, field_validator

from history_agent.contracts import StrictModel
from history_agent.prompts import Prompts
from history_agent.tools.registry import ToolRegistry

AnswerUser = Callable[[str, list[str]], Awaitable[str | None]]


class QuestionRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("question", "options")
    @classmethod
    def nonempty(cls, value):
        entries = value if isinstance(value, list) else [value]
        if any(not entry.strip() or len(entry) > 2000 for entry in entries):
            raise ValueError("Questions and options must contain text")
        return value


class QuestionTools:
    @staticmethod
    def definition():
        return ToolRegistry.create(
            "ask_user", Prompts.get("tools.ask_user"), QuestionRequest
        )

    @staticmethod
    async def ask(request: QuestionRequest, answer: AnswerUser | None) -> dict:
        if answer is None:
            return {
                "status": "unavailable",
                "guidance": Prompts.get("tools.no_answer"),
            }
        text = await answer(request.question, request.options)
        if text is None or not text.strip():
            return {
                "status": "unanswered",
                "guidance": Prompts.get("tools.no_answer"),
            }
        return {"status": "answered", "answer": text[:8000]}
