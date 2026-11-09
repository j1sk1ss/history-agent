from __future__ import annotations

import asyncio
from argparse import Namespace
from contextlib import suppress

from mcp import Client

from ..client import HistoryClient
from ..model import Model
from .app import HistoryApp


async def run_tui(
    client: Client, model: Model, args: Namespace, history: HistoryClient,
) -> dict[str, Namespace | str | None] | None:
    from ..session import converse

    app: HistoryApp = HistoryApp(args)

    async def backend() -> None:
        await app.ready.wait()
        try:
            await converse(
                client, model, args, history, ui_override=app.adapter
            )
        except Exception as error:
            app.adapter.error_message(type(error).__name__ + ": " + str(error))
        finally:
            app.adapter.event("backend_done")

    task: asyncio.Task[None] = asyncio.create_task(backend())
    try:
        return await app.run_async()
    finally:
        task.cancel()
        if app.adapter.work and not app.adapter.work.done():
            app.adapter.work.cancel()
            with suppress(asyncio.CancelledError):
                await app.adapter.work
        with suppress(asyncio.CancelledError):
            await task
