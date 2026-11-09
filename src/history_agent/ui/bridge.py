from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

from textual.message import Message


class BackendEvent(Message):
    def __init__(self, kind: str, data: dict[str, object]) -> None:
        super().__init__()
        self.kind: str
        self.data: dict[str, object]
        self.kind, self.data = kind, data


class DisplayConsole:
    def __init__(self, adapter: UIAdapter) -> None:
        self.adapter: UIAdapter = adapter

    def print(self, text: object = "", **kwargs: object) -> None:
        self.adapter.event("notice", text=str(text))


class UIAdapter:
    async_prompt: bool = True

    def __init__(
        self, post_event: Callable[[BackendEvent], bool],
        inputs: asyncio.Queue[str],
    ) -> None:
        self.post_event: Callable[[BackendEvent], bool] = post_event
        self.inputs: asyncio.Queue[str] = inputs
        self.work: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self.console: DisplayConsole = DisplayConsole(self)

    def event(self, kind: str, **data: object) -> None:
        if kind == "begin":
            self.last_error = None
        if kind == "failure" or (kind == "notice" and data.get("error")):
            message: str = data.get("message", data.get("text", ""))
            if message == self.last_error:
                return
            self.last_error = message
        self.post_event(BackendEvent(kind, data))

    def welcome(self, context: int) -> None:
        self.event("welcome", context=context)

    def begin(self, prompt: str) -> None:
        self.event("begin", text=prompt)

    def finish(self, answer: str | None = None) -> None:
        self.event("finish", answer=answer)

    def reset(self) -> None:
        self.event(
            "notice", text="Model context cleared. Session usage preserved."
        )

    def error_message(self, text: str) -> None:
        self.event("notice", text=text, error=True)

    async def prompt_async(self) -> str:
        self.event("input_ready")
        return await self.inputs.get()

    async def execute(self, operation: Coroutine[None, None, None]) -> None:
        self.work = asyncio.create_task(operation)
        try:
            return await self.work
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            from history_agent.inference.model import ChatError

            raise ChatError(
                "Stopped. Use /reload to continue or /reset for a new chat."
            ) from None
        finally:
            self.work = None
