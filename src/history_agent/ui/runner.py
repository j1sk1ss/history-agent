import asyncio
from contextlib import suppress

from .app import HistoryApp


async def run_tui(client, model, args, history):
    from ..session import converse

    app = HistoryApp(args)

    async def backend():
        await app.ready.wait()
        try:
            await converse(
                client, model, args, history, ui_override=app.adapter
            )
        except Exception as error:
            app.adapter.error_message(type(error).__name__ + ": " + str(error))
        finally:
            app.adapter.event("backend_done")

    task = asyncio.create_task(backend())
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
