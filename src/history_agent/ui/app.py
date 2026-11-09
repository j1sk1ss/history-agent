from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import DOMQuery
from textual.widgets import Button, Collapsible, Input, Static

from ..jobs import UnfinishedJob, delete_job, unfinished_jobs
from ..terminal import ThinkingText
from .art import WelcomeArt
from .bridge import BackendEvent, UIAdapter
from .config import ConfigurationScreen
from .formatting import pretty, tool_preview
from .jobs import UnfinishedJobs
from .metrics import SessionMetrics
from .widgets import CommandInput, DetailCard


class HistoryApp(App):
    TITLE: str = "History"
    CSS_PATH: Path = Path(__file__).with_name("style.tcss")
    ENABLE_COMMAND_PALETTE: bool = False
    BINDINGS: list[Binding] = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Stop", priority=True),
        Binding("escape", "cancel", "Stop", show=False, priority=True),
        Binding("ctrl+o", "configure", "Settings", priority=True),
        Binding("ctrl+e", "toggle_details", "Tool details", priority=True),
    ]

    def __init__(self, args: Namespace) -> None:
        super().__init__()
        self.args: Namespace = args
        self.inputs: asyncio.Queue[str] = asyncio.Queue()
        self.ready: asyncio.Event = asyncio.Event()
        self.adapter: UIAdapter = UIAdapter(self)
        self.metrics: SessionMetrics = SessionMetrics()
        self.cards: list[DetailCard] = []
        self.streams: dict[str, DetailCard] = {}
        self.tool_cards: dict[str, DetailCard] = {}
        self.text_parts: list[tuple[str, str]] = []
        self.splitter: ThinkingText = ThinkingText(self.append_stream)
        self.waiting: bool = False
        self.busy: bool = False
        self.backend_done: bool = False
        self.last_job_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "HISTORY  |  "
            + ("WRITE" if self.args.allow_write else "READ ONLY"),
            id="topbar",
        )
        with Vertical(id="body", classes="home"):
            with Vertical(id="welcome"):
                yield WelcomeArt()
                yield Static(
                    "Review code warnings with your model.", id="home-subtitle"
                )
            yield VerticalScroll(id="timeline")
            yield UnfinishedJobs()
            with Vertical(id="compose-area"):
                yield Static("", id="choices")
                yield CommandInput(self.args.batch_state_dir)
                with Horizontal(id="home-menu"):
                    yield Button("Review project", id="review-project")
                    yield Button("Help", id="help")
                    yield Button("Settings", id="settings")
                yield Static(
                    "enter send  ·  tab commands  ·  ctrl+o settings  "
                    "·  ctrl+q "
                    "quit",
                    id="input-hint",
                )
        yield Static("", id="metrics")

    async def on_mount(self) -> None:
        self.query_one("#choices").display = False
        self.query_one(CommandInput).disabled = True
        self.query_one("#metrics").display = False
        self.query_one("#timeline", VerticalScroll).anchor()
        self.set_interval(0.125, self.refresh_display)
        await self.refresh_jobs()
        self.ready.set()

    async def refresh_jobs(self) -> None:
        jobs: list[UnfinishedJob] = await asyncio.to_thread(
            unfinished_jobs, self.args.batch_state_dir
        )
        await self.query_one(UnfinishedJobs).show_jobs(jobs)
        self.query_one("#body").set_class(bool(jobs), "has-jobs")
        self.query_one(WelcomeArt).compact_below = 32 if jobs else 24

    def on_resize(self, event: events.Resize) -> None:
        for art in self.query(WelcomeArt):
            art.update_art(screen_height=event.size.height)
        for jobs in self.query(UnfinishedJobs):
            jobs.set_class(event.size.height < 28, "compact-jobs")

    async def add_card(
        self, title: str, text: str = "", *, kind: str = "notice",
        preview: str | None = None,
    ) -> DetailCard:
        self.query_one("#body").remove_class("home")
        self.query_one("#metrics").display = True
        card: DetailCard = DetailCard(title, text, kind=kind, preview=preview)
        await self.query_one("#timeline", VerticalScroll).mount(card)
        self.cards.append(card)
        card.refresh_content()
        return card

    def append_stream(self, kind: str, text: str) -> None:
        if not text:
            return
        self.text_parts.append((kind, text))

    async def flush_text(self) -> None:
        for kind, text in self.text_parts:
            await self.stream_text(kind, text)
        self.text_parts = []

    async def stream_text(self, kind: str, text: str) -> None:
        if not text:
            return
        if kind not in self.streams:
            self.streams[kind] = await self.add_card(
                "Reasoning" if kind == "reasoning" else "Assistant",
                kind="reasoning" if kind == "reasoning" else "answer",
            )
            self.streams[kind].active = True
        self.streams[kind].add_text(text)

    async def on_backend_event(self, event: BackendEvent) -> None:
        kind: str
        data: dict[str, object]
        kind, data = event.kind, event.data
        if kind not in (
            "begin",
            "finish",
            "notice",
            "input_ready",
            "welcome",
            "backend_done",
        ):
            self.metrics.event(kind, **data)
        if kind == "begin":
            self.busy = True
            self.last_job_id = None
            for card in self.streams.values():
                card.active = False
            self.streams = {}
            self.metrics.batch = None
            self.metrics.start()
            await self.add_card("You", data["text"], kind="user")
        elif kind == "input_ready":
            self.waiting, self.busy = True, False
            self.query_one(UnfinishedJobs).disabled = False
            self.query_one(CommandInput).disabled = False
            self.query_one(CommandInput).focus()
        elif kind == "backend_done":
            self.backend_done, self.busy = True, False
            self.waiting = True
            self.query_one(CommandInput).disabled = False
            self.query_one(CommandInput).focus()
        elif kind == "batch_started":
            self.last_job_id = data["job_id"]
        elif kind == "welcome":
            self.metrics.context = data["context"]
            self.query_one("#metrics").display = True
        elif kind in ("notice", "failure"):
            await self.add_card(
                "Error"
                if kind == "failure" or data.get("error")
                else "Notice",
                data.get("message", data.get("text", "")),
                kind="error" if kind == "failure" else "notice",
            )
        elif kind == "request_start":
            self.splitter.flush()
            await self.flush_text()
            for card in self.streams.values():
                card.active = False
            self.streams = {}
            self.splitter = ThinkingText(self.append_stream)
        elif kind in ("content", "reasoning"):
            if kind == "reasoning":
                await self.stream_text("reasoning", data["text"])
            else:
                self.splitter.feed(data["text"])
                await self.flush_text()
        elif kind == "request_end":
            self.splitter.flush()
            await self.flush_text()
            for card in self.streams.values():
                card.active = False
        elif kind == "tool_start":
            key: str = data.get("event_id", data["name"])
            card: DetailCard | None = await self.add_card(
                "RUNNING | " + str(data.get("action") or data["name"]),
                kind="tool",
                preview="Executing tool call",
            )
            card.active = True
            self.tool_cards[key] = card
        elif kind == "tool_end":
            card = self.tool_cards.get(data.get("event_id", data["name"]))
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
        elif kind in ("tool_result", "tool_prefetch"):
            card = self.tool_cards.pop(data.get("event_id", ""), None)
            if card is None:
                card = await self.add_card(
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
        elif kind == "batch_progress":
            await self.add_card(
                data["checker"],
                f"Warning {data['warning_id']} | "
                f"completed {data['done']}/{data['total']}",
            )
        elif kind == "batch_verdict":
            if self.metrics.batch:
                self.metrics.batch["done"] += 1
            await self.add_card(
                data["classification"]
                + (" | saved" if data["saved"] else " | proposed"),
                data["comment"],
                kind="verdict",
            )
        elif kind == "batch_patterns":
            await self.add_card(
                "Review patterns",
                pretty(data.get("patterns", [])),
                preview=f"{data['checker']} | criteria: {data['count']}",
            )
        elif kind == "batch_format_repair":
            await self.add_card(
                "Format validation", "\n".join(data.get("issues", []))
            )
        elif kind == "batch_summary":
            await self.add_card(
                "Batch summary", pretty(data["text"]), kind="answer"
            )
        elif kind == "history_link":
            await self.add_card(
                "History URL resolved", pretty(data), kind="tool"
            )
        elif kind == "finish":
            self.splitter.flush()
            await self.flush_text()
            self.busy = False
            if data.get("answer") and not any(
                card.text
                for card in self.streams.values()
                if card.kind == "answer"
            ):
                await self.add_card("Assistant", data["answer"], kind="answer")

    def refresh_display(self) -> None:
        if not self.is_mounted:
            return
        metrics: DOMQuery[Static] = self.query("#metrics")
        if not metrics:
            return
        for card in self.cards:
            card.refresh_content()
        m: SessionMetrics = self.metrics
        state: str = (
            "Working"
            if self.busy
            else "Complete"
            if self.backend_done
            else "Ready"
        )
        status: dict[str, object] = m.summary(
            state,
            context_limit=getattr(self.args, "max_context_bytes", 65536),
            detailed=getattr(self.args, "stats", False),
        )
        metrics.first().update(Text(status))

    def show_completions(self, matches: list[str]) -> None:
        widget: Static = self.query_one("#choices", Static)
        widget.display = bool(matches)
        widget.update(Text("  ".join(matches)))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit(event.value)

    def submit(self, value: str) -> None:
        text: str = value.strip()
        if not text or not self.waiting:
            return
        if text == "/config":
            self.action_configure()
            return
        if text in ("/quit", "/quite"):
            self.exit()
            return
        if self.backend_done:
            if text == "/reload":
                self.exit({"reload_job": self.last_job_id})
            else:
                self.adapter.error_message(
                    "Backend stopped. Use /reload to restart it."
                )
            return
        self.waiting = False
        self.query_one(UnfinishedJobs).disabled = True
        composer: CommandInput = self.query_one(CommandInput)
        composer.remember(text)
        composer.value = ""
        composer.disabled = True
        self.show_completions([])
        self.inputs.put_nowait(text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id: str = event.button.id or ""
        if button_id.startswith("delete-"):
            if not self.waiting:
                return
            jobs: UnfinishedJobs = self.query_one(UnfinishedJobs)
            jobs.disabled = True
            try:
                await asyncio.to_thread(
                    delete_job,
                    self.args.batch_state_dir,
                    button_id.removeprefix("delete-"),
                )
                await self.refresh_jobs()
            except (OSError, ValueError) as error:
                jobs.show_error(str(error))
            finally:
                jobs.disabled = not self.waiting
        elif button_id.startswith("resume-"):
            self.submit("/resume " + button_id.removeprefix("resume-"))
        elif button_id == "settings":
            self.action_configure()
        elif button_id in ("review-project", "help"):
            self.submit(
                "/batch" if event.button.id == "review-project" else "/help"
            )

    def action_configure(self) -> None:
        if (self.waiting or self.backend_done) and not isinstance(
            self.screen, ConfigurationScreen
        ):
            self.push_screen(
                ConfigurationScreen(self.args), self.settings_closed
            )

    def settings_closed(self, args: Namespace | None) -> None:
        if args is not None:
            self.exit({"settings": args})

    def check_action(
        self, action: str, parameters: tuple[object, ...],
    ) -> bool | None:
        if isinstance(self.screen, ConfigurationScreen) and action in (
            "quit",
            "cancel",
            "configure",
            "toggle_details",
        ):
            return False
        return super().check_action(action, parameters)

    def action_cancel(self) -> None:
        if self.adapter.work and not self.adapter.work.done():
            self.adapter.work.cancel()
        elif not self.backend_done:
            self.query_one(CommandInput).focus()

    def action_toggle_details(self) -> None:
        disclosures: list[Collapsible] = list(self.query(Collapsible))
        collapse: bool = any(not widget.collapsed for widget in disclosures)
        for widget in disclosures:
            widget.collapsed = collapse
