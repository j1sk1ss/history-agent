"""Present backend events without owning the application loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from history_agent.ui import app as view
from history_agent.ui.bridge import BackendEvent
from history_agent.ui.formatting import pretty, tool_preview
from history_agent.ui.jobs import UnfinishedJobs
from history_agent.ui.text import ThinkingText
from history_agent.ui.widgets import CommandInput, DetailCard


class BackendPresenter:
    def __init__(self, ui: view.HistoryApp) -> None:
        self.ui: view.HistoryApp = ui

    async def handle(self, event: BackendEvent) -> None:
        kind: str = event.kind
        data: dict[str, object] = event.data
        if kind not in (
            "begin",
            "finish",
            "notice",
            "input_ready",
            "welcome",
            "backend_done",
        ):
            self.ui.metrics.event(kind, **data)
        handlers: dict[
            str, Callable[[str, dict[str, object]], Awaitable[None]]
        ] = {
            "begin": self.handle_begin,
            "input_ready": self.handle_input_ready,
            "backend_done": self.handle_backend_done,
            "batch_started": self.handle_batch_started,
            "welcome": self.handle_welcome,
            "notice": self.handle_notice,
            "failure": self.handle_notice,
            "request_start": self.handle_request_start,
            "content": self.handle_content,
            "reasoning": self.handle_content,
            "request_end": self.handle_request_end,
            "tool_start": self.handle_tool_start,
            "tool_end": self.handle_tool_end,
            "tool_result": self.handle_tool_result,
            "tool_prefetch": self.handle_tool_result,
            "batch_progress": self.handle_batch_progress,
            "batch_verdict": self.handle_batch_verdict,
            "batch_patterns": self.handle_batch_patterns,
            "batch_format_repair": self.handle_batch_format_repair,
            "batch_summary": self.handle_batch_summary,
            "history_link": self.handle_history_link,
            "finish": self.handle_finish,
        }
        handler: Callable[[str, dict[str, object]], Awaitable[None]] | None = (
            handlers.get(kind)
        )
        if handler is not None:
            await handler(kind, data)

    async def handle_begin(self, kind: str, data: dict[str, object]) -> None:
        self.ui.busy = True
        self.ui.last_job_id = None
        for card in self.ui.streams.values():
            card.active = False
        self.ui.streams = {}
        self.ui.metrics.batch = None
        self.ui.metrics.start()
        await self.ui.add_card("You", data["text"], kind="user")

    async def handle_input_ready(
        self, kind: str, data: dict[str, object]
    ) -> None:
        self.ui.waiting, self.ui.busy = (True, False)
        self.ui.query_one(UnfinishedJobs).disabled = False
        self.ui.query_one(CommandInput).disabled = False
        self.ui.query_one(CommandInput).focus()

    async def handle_backend_done(
        self, kind: str, data: dict[str, object]
    ) -> None:
        self.ui.backend_done, self.ui.busy = (True, False)
        self.ui.waiting = True
        self.ui.query_one(CommandInput).disabled = False
        self.ui.query_one(CommandInput).focus()

    async def handle_batch_started(
        self, kind: str, data: dict[str, object]
    ) -> None:
        self.ui.last_job_id = data["job_id"]

    async def handle_welcome(self, kind: str, data: dict[str, object]) -> None:
        self.ui.metrics.context = data["context"]
        self.ui.query_one("#metrics").display = True

    async def handle_notice(self, kind: str, data: dict[str, object]) -> None:
        await self.ui.add_card(
            "Error" if kind == "failure" or data.get("error") else "Notice",
            data.get("message", data.get("text", "")),
            kind="error" if kind == "failure" else "notice",
        )

    async def handle_request_start(
        self, kind: str, data: dict[str, object]
    ) -> None:
        self.ui.splitter.flush()
        await self.ui.flush_text()
        for card in self.ui.streams.values():
            card.active = False
        self.ui.streams = {}
        self.ui.splitter = ThinkingText(self.ui.append_stream)

    async def handle_content(self, kind: str, data: dict[str, object]) -> None:
        if kind == "reasoning":
            await self.ui.stream_text("reasoning", data["text"])
        else:
            self.ui.splitter.feed(data["text"])
            await self.ui.flush_text()

    async def handle_request_end(
        self, kind: str, data: dict[str, object]
    ) -> None:
        self.ui.splitter.flush()
        await self.ui.flush_text()
        for card in self.ui.streams.values():
            card.active = False

    async def handle_tool_start(
        self, kind: str, data: dict[str, object]
    ) -> None:
        key: str = data.get("event_id", data["name"])
        card: DetailCard | None = await self.ui.add_card(
            "RUNNING | " + str(data.get("action") or data["name"]),
            kind="tool",
            preview="Executing tool call",
        )
        card.active = True
        self.ui.tool_cards[key] = card

    async def handle_tool_end(
        self, kind: str, data: dict[str, object]
    ) -> None:
        card = self.ui.tool_cards.get(data.get("event_id", data["name"]))
        if card:
            card.heading = (
                "ERROR | " if data.get("error") else "DONE | "
            ) + card.heading.removeprefix("RUNNING | ")
            if data.get("error"):
                card.preview_override, card.active = (
                    "Tool returned an error.",
                    False,
                )
            card.dirty = True

    async def handle_tool_result(
        self, kind: str, data: dict[str, object]
    ) -> None:
        card = self.ui.tool_cards.pop(data.get("event_id", ""), None)
        if card is None:
            card = await self.ui.add_card(
                "TOOL | "
                + str(data.get("action") or data.get("name", "Tool")),
                kind="tool",
            )
        response: str = data["response"]
        try:
            result: object = json.loads(response)
        except ValueError:
            result = response
        card.text = pretty(
            {"arguments": data.get("arguments", {}), "result": result}
        )
        card.preview_override, card.dirty, card.active = (
            tool_preview(response),
            True,
            False,
        )

    async def handle_batch_progress(
        self, kind: str, data: dict[str, object]
    ) -> None:
        await self.ui.add_card(
            data["checker"],
            (
                "Warning "
                f"{data['warning_id']}"
                " | completed "
                f"{data['done']}"
                "/"
                f"{data['total']}"
            ),
        )

    async def handle_batch_verdict(
        self, kind: str, data: dict[str, object]
    ) -> None:
        if self.ui.metrics.batch:
            self.ui.metrics.batch["done"] += 1
        await self.ui.add_card(
            data["classification"]
            + (" | saved" if data["saved"] else " | proposed"),
            data["comment"],
            kind="verdict",
        )

    async def handle_batch_patterns(
        self, kind: str, data: dict[str, object]
    ) -> None:
        await self.ui.add_card(
            "Review patterns",
            pretty(data.get("patterns", [])),
            preview=f"{data['checker']} | criteria: {data['count']}",
        )

    async def handle_batch_format_repair(
        self, kind: str, data: dict[str, object]
    ) -> None:
        await self.ui.add_card(
            "Format validation", "\n".join(data.get("issues", []))
        )

    async def handle_batch_summary(
        self, kind: str, data: dict[str, object]
    ) -> None:
        await self.ui.add_card(
            "Batch summary", pretty(data["text"]), kind="answer"
        )

    async def handle_history_link(
        self, kind: str, data: dict[str, object]
    ) -> None:
        await self.ui.add_card(
            "History URL resolved", pretty(data), kind="tool"
        )

    async def handle_finish(self, kind: str, data: dict[str, object]) -> None:
        self.ui.splitter.flush()
        await self.ui.flush_text()
        self.ui.busy = False
        if data.get("answer") and (
            not any(
                (
                    card.text
                    for card in self.ui.streams.values()
                    if card.kind == "answer"
                )
            )
        ):
            await self.ui.add_card("Assistant", data["answer"], kind="answer")
