import asyncio

from textual.message import Message


class BackendEvent(Message):
    def __init__(self, kind, data):
        super().__init__()
        self.kind, self.data = kind, data


class DisplayConsole:
    def __init__(self, adapter):
        self.adapter = adapter

    def print(self, text="", **kwargs):
        self.adapter.event("notice", text=str(text))


class UIAdapter:
    async_prompt = True

    def __init__(self, app):
        self.app, self.work = app, None
        self.last_error = None
        self.console = DisplayConsole(self)

    def event(self, kind, **data):
        if kind == "begin":
            self.last_error = None
        if kind == "failure" or (kind == "notice" and data.get("error")):
            message = data.get("message", data.get("text", ""))
            if message == self.last_error:
                return
            self.last_error = message
        self.app.post_message(BackendEvent(kind, data))

    def welcome(self, context):
        self.event("welcome", context=context)

    def begin(self, prompt):
        self.event("begin", text=prompt)

    def finish(self, answer=None):
        self.event("finish", answer=answer)

    def reset(self):
        self.event(
            "notice", text="Model context cleared. Session usage preserved."
        )

    def error_message(self, text):
        self.event("notice", text=text, error=True)

    async def prompt_async(self):
        self.event("input_ready")
        return await self.app.inputs.get()

    async def execute(self, operation):
        self.work = asyncio.create_task(operation)
        try:
            return await self.work
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            from ..model import ChatError

            raise ChatError(
                "Stopped. Use /reload to continue or /reset for a new chat."
            ) from None
        finally:
            self.work = None
