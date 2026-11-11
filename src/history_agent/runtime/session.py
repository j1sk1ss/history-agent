"""Drive startup and terminal commands for an interactive conversation."""

from __future__ import annotations

import sys
from argparse import Namespace

from mcp import Client

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.inference.model import ChatError, Model
from history_agent.prompts import Prompts
from history_agent.runtime.commands import HELP, install_completion
from history_agent.runtime.conversation import Conversation
from history_agent.runtime.request_scope import (
    command_arguments,
    initial_arguments,
)
from history_agent.ui.bridge import UIAdapter
from history_agent.ui.terminal import TerminalUI


async def converse(
    client: Client,
    model: Model,
    args: Namespace,
    history: HistoryClient,
    *,
    ui_override: TerminalUI | UIAdapter | None = None,
) -> None:
    session: Session = Session(client, model, args, history, ui_override)
    await session.run()


class Session:
    def __init__(
        self,
        client: Client,
        model: Model,
        args: Namespace,
        history: HistoryClient,
        ui: TerminalUI | UIAdapter | None,
    ) -> None:
        self.conversation: Conversation = Conversation(
            client, model, args, history, ui
        )

    async def run(self) -> None:
        await self.conversation.initialize()
        if self.conversation.ui:
            self.conversation.ui.welcome(
                self.conversation.harness.context_bytes()
            )
        if not await self.start_requests():
            return
        self.announce()
        while True:
            try:
                text: str = (await self.read_input()).strip()
            except EOFError:
                return
            if text in ("/quit", "/quite"):
                return
            if not text:
                continue
            try:
                await self.handle_input(text)
            except (ChatError, HistoryError, ValueError, OSError) as error:
                if self.conversation.ui:
                    self.conversation.ui.error_message(str(error))
                else:
                    print(str(error), file=sys.stderr)

    async def start_requests(self) -> bool:
        if (
            self.conversation.args.batch
            or self.conversation.args.diff
            or self.conversation.args.resume
        ):
            arguments: dict[str, object]
            resume: bool
            arguments, resume = initial_arguments(self.conversation.args)
            await self.conversation.start(
                self.conversation.run_batch(arguments, resume)
            )
            return self.conversation.interactive_ui
        if self.conversation.args.prompt is not None:
            await self.conversation.start(
                self.conversation.respond(self.conversation.args.prompt)
            )
            return self.conversation.interactive_ui
        return True

    def announce(self) -> None:
        if sys.stdin.isatty() and (not self.conversation.interactive_ui):
            install_completion(self.conversation.args.batch_state_dir)
        if self.conversation.ui:
            return
        write_mode: str = (
            "review writes enabled"
            if self.conversation.args.allow_write
            else "read-only"
        )
        print(
            (
                "history-chat: "
                f"{self.conversation.model.name}"
                "; "
                f"{write_mode}"
                "; initial context="
                f"{self.conversation.harness.context_bytes()}"
                " bytes; /help, Tab completes commands"
            ),
            file=sys.stderr,
        )

    async def read_input(self) -> str:
        if self.conversation.interactive_ui:
            return await self.conversation.ui.prompt_async()
        if self.conversation.ui:
            return self.conversation.ui.prompt()
        return input("history> ")

    async def handle_input(self, text: str) -> None:
        if text == "/help":
            if self.conversation.ui:
                self.conversation.ui.console.print(HELP, markup=False)
            else:
                print(HELP)
            return
        if text == "/reset":
            self.conversation.reset()
            return
        if text == "/reload":
            if self.conversation.retry is None:
                raise ChatError("No request to reload yet")
            self.conversation.harness.reset()
            self.conversation.harness.messages[0]["content"] += Prompts.get(
                "runtime.batch_hint"
            )
            self.conversation.router.completed = None
            await self.conversation.perform(self.conversation.retry())
            return
        if text.split()[0] in ("/batch", "/diff", "/resume"):
            arguments: dict[str, object]
            resume: bool
            arguments, resume = await command_arguments(
                text, self.conversation.args, self.conversation.history
            )
            await self.conversation.perform(
                self.conversation.run_batch(arguments, resume)
            )
            return
        if text.startswith("/"):
            raise ChatError(
                (
                    "Unknown command. Use /help or Tab to see available "
                    "commands."
                )
            )
        await self.conversation.perform(self.conversation.respond(text))
