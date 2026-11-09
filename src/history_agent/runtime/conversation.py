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

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.history.links import find_history_urls
from history_agent.history.schemas import URL_ACTION
from history_agent.inference.model import ChatError, Model, compact
from history_agent.review.batch import BatchRunner
from history_agent.review.client import ItemClient
from history_agent.review.journal import Journal
from history_agent.review.router import BatchRouter
from history_agent.runtime.agent import Harness
from history_agent.runtime.prompts import BATCH_HINT
from history_agent.ui.bridge import UIAdapter
from history_agent.ui.terminal import TerminalUI


def pretty_json_text(value: str) -> str:
    """
    Pretty-print complete JSON on display; keep compact JSON inside model
    context.
    """
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return value


class Conversation:
    def __init__(
        self, client: Client, model: Model, args: Namespace,
        history: HistoryClient, ui: TerminalUI | UIAdapter | None,
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
        self.tools: list[Tool] = (await self.client.list_tools()).tools
        self.runner: BatchRunner = BatchRunner(
            self.history,
            self.client,
            self.make_reviewer,
            self.args.batch_state_dir,
            {"base": self.model.base, "model": self.model.name},
            allow_write=self.args.allow_write,
            allow_overwrite=self.args.overwrite_reviews,
            on_event=self.batch_event,
            reuse_patterns=not self.args.no_patterns,
        )
        self.router: BatchRouter = BatchRouter(self.client, self.runner)
        self.harness: Harness = Harness(
            self.router,
            self.model,
            self.tools,
            allow_write=self.args.allow_write,
            max_steps=self.args.max_steps,
            max_context_bytes=self.args.max_context_bytes,
            max_tool_result_bytes=self.args.max_tool_result_bytes,
            stats=self.args.stats,
            on_event=self.ui.event if self.ui else None,
        )
        self.harness.messages[0]["content"] += BATCH_HINT
        self.retry: Callable[[], Coroutine[None, None, None]] | None = None

    def make_reviewer(self, item_client: ItemClient) -> Harness:
        return Harness(
            item_client,
            self.model,
            self.tools,
            allow_write=False,
            max_steps=self.args.max_steps,
            max_context_bytes=self.args.max_context_bytes,
            max_tool_result_bytes=self.args.max_tool_result_bytes,
            stats=self.args.stats,
            on_event=self.ui.event if self.ui else None,
        )

    def batch_event(self, kind: str, **data: object) -> None:
        if self.ui:
            self.ui.event(kind, **data)
        elif kind in ("batch_started", "batch_progress"):
            if kind == "batch_started":
                print("batch job:", data["job_id"], file=sys.stderr)
            else:
                print(
                    f"batch {data['done']}/{data['total']}: "
                    f"{data['checker']} {data['warning_id']}",
                    file=sys.stderr,
                )

    async def perform(self, operation: Coroutine[None, None, None]) -> None:
        if self.ui and getattr(self.ui, "async_prompt", False):
            return await self.ui.execute(operation)
        return await operation

    def retain_summary(self, summary: dict[str, object]) -> None:
        self.harness.reset()
        self.harness.messages[0]["content"] += BATCH_HINT
        small: dict[str, object] = {
            key: summary[key]
            for key in (
                "job_id",
                "scope",
                "write",
                "total",
                "states",
                "classifications",
            )
        }
        if summary.get("comparison"):
            small["comparison"] = summary["comparison"]
        self.harness.messages.append(
            {
                "role": "assistant",
                "content": "Previous batch summary: " + compact(small),
            }
        )
        self.router.completed = None

    async def respond(self, text: str) -> None:
        self.retry = partial(self.respond, text)
        self.runner.last_job_id = None
        self.router.user_request = text
        if self.ui:
            self.ui.begin(text)
        result: str | None = None
        try:
            prefetched: list[
                tuple[str, dict[str, object], dict[str, object]]
            ] = []
            urls: list[str] = find_history_urls(text)
            if len(urls) > 4:
                raise ChatError("Send at most four history links per request")
            for url in urls:
                try:
                    resolved: dict[str, object] = await asyncio.to_thread(
                        self.history.execute, URL_ACTION, {"url": url}
                    )
                except HistoryError as error:
                    raise ChatError(str(error)) from None
                prefetched.append((URL_ACTION, {"url": url}, resolved))
                if self.ui:
                    self.ui.event(
                        "history_link",
                        kind_name=resolved["kind"],
                        scope=resolved["scope"],
                        subset=resolved.get("subset"),
                    )
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
        self, arguments: dict[str, object], resume: bool = False,
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
            summary = await self.runner.run(arguments, write=write)
            self.retain_summary(summary)
            text: str = compact(
                {
                    key: summary[key]
                    for key in ("job_id", "total", "states", "classifications")
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
        if self.runner.last_summary:
            self.retain_summary(self.runner.last_summary)
        else:
            self.harness.reset()
            self.harness.messages[0]["content"] += BATCH_HINT
        if self.ui:
            self.ui.reset()
        else:
            print(
                "Fresh context. Review history is unchanged.", file=sys.stderr
            )
