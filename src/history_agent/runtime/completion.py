"""Run a blocking model request without delaying cancellation."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from history_agent.inference.model import ChatError, Model


async def complete_model(
    model: Model,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    on_event: Callable[..., None],
) -> dict[str, object]:
    """
    Do not make Ctrl+C wait for a blocked urllib worker to hit its socket
    timeout.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    result: asyncio.Future[dict[str, object]] = loop.create_future()
    stopped: threading.Event = threading.Event()

    def deliver(
        value: dict[str, object] | None,
        error: BaseException | None,
    ) -> None:
        if not result.done():
            if error is None:
                result.set_result(value)
            else:
                result.set_exception(error)

    def emit(kind: str, **data: object) -> None:
        if stopped.is_set():
            raise ChatError("Model request cancelled")
        on_event(kind, **data)

    def worker() -> None:
        value: dict[str, object] | None
        error: BaseException | None
        value = error = None
        try:
            value = model.complete(messages, tools, emit)
        except BaseException as caught:
            error = caught
        try:
            loop.call_soon_threadsafe(deliver, value, error)
        except RuntimeError:
            pass

    threading.Thread(
        target=worker, name="history-model-request", daemon=True
    ).start()
    try:
        return await result
    finally:
        stopped.set()
