"""Own model context, review requests and retry state for one chat."""

from __future__ import annotations

import asyncio
import json
import sys
from argparse import Namespace
from collections.abc import Callable, Coroutine
from functools import partial

from mcp import Client
from mcp.types import Tool

from history_agent.configuration.capabilities import CapabilitySettings
from history_agent.history.client import HistoryClient, HistoryError
from history_agent.inference.model import ChatError, Model, compact
from history_agent.prompts import Prompts
from history_agent.review.batch import BatchRunner
from history_agent.review.client import ItemClient
from history_agent.review.journal import Journal
from history_agent.review.router import BatchRouter
from history_agent.review.system_one import (
    SystemOne,
    SystemOneClient,
    load_warning_context,
    warning_context,
)
from history_agent.runtime.agent import Harness
from history_agent.runtime.conversation_events import ConversationEvents
from history_agent.runtime.interaction import UserInteraction
from history_agent.runtime.link_context import prepare_links
from history_agent.runtime.summary import ConversationSummary, pretty_json_text
from history_agent.tools.client import CapabilityClient
from history_agent.ui.bridge import UIAdapter
from history_agent.ui.terminal import TerminalUI


class Conversation(ConversationSummary, ConversationEvents):
    def __init__(
        self,
        client: Client,
        model: Model,
        args: Namespace,
        history: HistoryClient,
        ui: TerminalUI | UIAdapter | None,
    ) -> None:
        self.client: Client = client
        self.model: Model = model
        self.args: Namespace = args
        self.history: HistoryClient = history
        self.ui: TerminalUI | UIAdapter | None = ui

    async def initialize(self) -> None:
        use_ui: bool = (
            self.args.ui
            if self.args.ui is not None
            else sys.stdin.isatty() and sys.stdout.isatty()
        )
        if use_ui and self.ui is None:
            self.ui = TerminalUI(
                self.model.name,
                allow_write=self.args.allow_write,
                show_thoughts=True,
            )
        self.capabilities = CapabilitySettings.model_validate(
            getattr(self.args, "capabilities", {})
        )
        self.tools: list[Tool] = (await self.client.list_tools()).tools
        await self.prepare_tools()
        self.system_one: SystemOne | None = None
        if self.args.system_one_model:
            adviser = SystemOne(
                self.args.system_one_url,
                self.args.system_one_model,
                self.args.timeout,
            )
            if await asyncio.to_thread(adviser.probe):
                self.system_one = adviser
        self.runner: BatchRunner = BatchRunner(
            self.history,
            self.client,
            self.make_reviewer,
            self.args.batch_state_dir,
            {"base": self.model.base, "model": self.model.name},
            allow_write=self.args.allow_write,
            on_event=self.batch_event,
            reuse_patterns=not self.args.no_patterns,
        )
        self.router: BatchRouter = BatchRouter(self.client, self.runner)
        transport = SystemOneClient(
            self.capability_client(self.router), self.system_one
        )
        self.harness: Harness = Harness(
            transport,
            self.model,
            self.tools,
            allow_write=self.args.allow_write,
            max_steps=self.args.max_steps,
            max_tool_retries=self.args.max_tool_retries,
            max_context_bytes=self.args.max_context_bytes,
            max_tool_result_bytes=self.args.max_tool_result_bytes,
            stats=self.args.stats,
            on_event=self.model_events(),
        )
        transport.context = lambda: warning_context(self.harness.messages)
        transport.load_context = lambda: load_warning_context(
            self.router, self.harness.messages
        )
        self.harness.messages[0]["content"] += Prompts.get(
            "runtime.batch_hint"
        )
        self.retry: Callable[[], Coroutine[None, None, None]] | None = None

    def capability_client(self, client):
        async def answer(question, options):
            return await UserInteraction.answer(self.ui, question, options)

        return CapabilityClient(client, self.capabilities, answer)

    def make_reviewer(self, item_client: ItemClient) -> Harness:
        transport = SystemOneClient(
            self.capability_client(item_client), self.system_one
        )
        reviewer = Harness(
            transport,
            self.model,
            self.worker_tools,
            allow_write=False,
            max_steps=self.args.max_steps,
            max_tool_retries=self.args.max_tool_retries,
            max_context_bytes=self.args.max_context_bytes,
            max_tool_result_bytes=self.args.max_tool_result_bytes,
            stats=self.args.stats,
            on_event=self.model_events(job_id=self.runner.last_job_id),
        )
        transport.context = lambda: warning_context(reviewer.messages)
        transport.load_context = lambda: load_warning_context(
            item_client, reviewer.messages
        )
        return reviewer

    async def perform(self, operation: Coroutine[None, None, None]) -> None:
        if self.ui and getattr(self.ui, "async_prompt", False):
            return await self.ui.execute(operation)
        return await operation

    async def respond(self, text: str) -> None:
        self.retry = partial(self.respond, text)
        self.runner.last_job_id = None
        self.router.user_request = text
        if self.ui:
            self.ui.begin(text)
        result: str | None = None
        try:
            prefetched = await prepare_links(self, text)
            result = await self.harness.ask(text, prefetched=prefetched)
            if not self.ui:
                print(pretty_json_text(result))
            if self.router.completed:
                self.retain_summary(self.router.completed)
        finally:
            if self.runner.last_job_id:
                self.retry = partial(
                    self.run_batch,
                    {"job_id": self.runner.last_job_id},
                    resume=True,
                )
            if self.ui:
                self.ui.finish(result)

    async def run_batch(
        self,
        arguments: dict[str, object],
        resume: bool = False,
    ) -> None:
        self.retry = partial(self.run_batch, dict(arguments), resume=resume)
        self.runner.last_job_id = None
        if self.ui:
            self.ui.begin(
                "Resume review job" if resume else "Review all warnings"
            )
        summary: dict[str, object] | None = None
        try:
            write: bool = self.args.allow_write
            if resume:
                journal: Journal = Journal(
                    self.args.batch_state_dir, arguments["job_id"]
                )
                try:
                    write = write or journal.get("write")
                finally:
                    journal.close()
            arguments = await self.router.pin_snapshots(arguments)
            summary = await self.runner.run(arguments, write=write)
            self.retain_summary(summary)
            text: str = compact(
                {
                    key: summary[key]
                    for key in (
                        "job_id",
                        "total",
                        "states",
                        "classifications",
                        "timing",
                    )
                }
            )
            if summary.get("comparison"):
                text = compact(
                    {**json.loads(text), "comparison": summary["comparison"]}
                )
            if self.ui:
                self.ui.event("batch_summary", text=text)
            else:
                print(pretty_json_text(text))
        except BaseException as error:
            self.diagnostics.event(
                "batch_error", error=str(error),
                error_type=type(error).__name__,
                job_id=self.runner.last_job_id,
            )
            if self.ui:
                self.ui.event(
                    "failure",
                    message=str(error)
                    if isinstance(error, (ChatError, HistoryError))
                    else (
                        "Batch interrupted; use its checkpoint to resume and "
                        "inspect any pending write."
                    ),
                )
            raise
        finally:
            if self.runner.last_job_id:
                self.retry = partial(
                    self.run_batch,
                    {"job_id": self.runner.last_job_id},
                    resume=True,
                )
            if self.ui:
                self.ui.finish()

    async def start(self, operation: Coroutine[None, None, None]) -> None:
        try:
            await self.perform(operation)
        except (ChatError, HistoryError, ValueError, OSError) as error:
            if not (self.ui and getattr(self.ui, "async_prompt", False)):
                raise
            self.ui.error_message(str(error))

    @property
    def interactive_ui(self) -> bool:
        return bool(self.ui and getattr(self.ui, "async_prompt", False))

    def reset(self) -> None:
        self.router.link_scope.bind({})
        if self.runner.last_summary:
            self.retain_summary(self.runner.last_summary)
        else:
            self.harness.reset()
            self.harness.messages[0]["content"] += Prompts.get(
                "runtime.batch_hint"
            )
        if self.ui:
            self.ui.reset()
        else:
            print(
                "Fresh context. Review history is unchanged.", file=sys.stderr
            )
