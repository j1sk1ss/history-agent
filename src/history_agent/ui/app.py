from __future__ import annotations

import asyncio
from argparse import Namespace
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import DOMQuery
from textual.widgets import Button, Collapsible, Input, Static

from history_agent.review.jobs import (
    UnfinishedJob,
    delete_job,
    unfinished_jobs,
)
from history_agent.ui.art import WelcomeArt
from history_agent.ui.bridge import BackendEvent, UIAdapter
from history_agent.ui.config import ConfigurationScreen
from history_agent.ui.events import BackendPresenter
from history_agent.ui.jobs import UnfinishedJobs
from history_agent.ui.metrics import SessionMetrics
from history_agent.ui.text import ThinkingText
from history_agent.ui.widgets import CommandInput, DetailCard


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
        self,
        title: str,
        text: str = "",
        *,
        kind: str = "notice",
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
        await BackendPresenter(self).handle(event)

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
        self,
        action: str,
        parameters: tuple[object, ...],
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
