"""Local terminal presentation; none of this state is sent to the model."""

from __future__ import annotations

import math
import threading
import time
from _thread import RLock
from collections import deque

from rich.console import (
    Console,
    Group,
)
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from history_agent.ui.terminal_events import TerminalPresenter
from history_agent.ui.terminal_metrics import footer
from history_agent.ui.text import Tail, ThinkingText, safe_text


class TerminalUI:
    def __init__(
        self, model: str, *, allow_write: bool = False,
        show_thoughts: bool = True, console: Console | None = None,
    ) -> None:
        self.console: Console = console or Console()
        self.model: str
        self.allow_write: bool
        self.show_thoughts: bool
        self.model, self.allow_write, self.show_thoughts = (
            model,
            allow_write,
            show_thoughts,
        )
        self.lock: RLock = threading.RLock()
        self.live: Live | None = None
        self.records: list[list[str]] = []
        self.events: deque[str] = deque(maxlen=5)
        self.session_in: int
        self.session_out: int
        self.session_cached: int
        self.session_in = self.session_out = self.session_cached = 0
        self.session_estimated_in: bool
        self.session_estimated_out: bool
        self.session_estimated_in = self.session_estimated_out = False
        self.state: str
        self.error: str | None
        self.state, self.error = "Ready", None
        self.started: float
        self.request_started: float
        self.started = self.request_started = time.monotonic()
        self.step: int
        self.context: int
        self.step, self.context = 0, 0
        self.first_token: float | None
        self.output_chars: int
        self.usage: dict[str, object]
        self.first_token, self.output_chars, self.usage = None, 0, {}
        self.usage_chars: int = 0
        self.request_finished: float | None = None
        self.splitter: ThinkingText = ThinkingText(self.append)
        self.accounted: bool = False
        self.batch: dict[str, object] | None = None
        self.review_wall: float = 0.0

    def welcome(self, context: int) -> None:
        self.console.print(
            "HISTORY | " + ("WRITE" if self.allow_write else "READ ONLY")
        )

    def prompt(self) -> str:
        return self.console.input("history> ")

    def begin(self, prompt: str) -> None:
        with self.lock:
            self.records, self.events, self.error = [], deque(maxlen=5), None
            self.started = time.monotonic()
            self.state = "Starting request"
            self.batch = None
            self.review_wall = 0.0
            self.step, self.context, self.output_chars = 0, 0, 0
            self.first_token, self.request_finished, self.usage = (
                None,
                None,
                {},
            )
            self.accounted = True
            self.splitter = ThinkingText(self.append)
        self.console.print(
            Panel(
                Text(safe_text(prompt)),
                title="You",
                title_align="left",
                border_style="cyan",
            )
        )
        self.live = Live(
            console=self.console,
            get_renderable=self.render,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="crop",
        )
        self.live.start(refresh=True)

    def append(self, kind: str, text: str) -> None:
        text = safe_text(text)
        if not text:
            return
        if self.records and self.records[-1][0] == kind:
            self.records[-1][1] += text
        else:
            self.records.append([kind, text])

    def event(self, kind: str, **data: object) -> None:
        with self.lock:
            TerminalPresenter(self).handle(kind, data)

    def counts(self) -> tuple[int | None, int | None, int | None]:
        def count(value: object) -> int | None:
            return (
                value
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                else None
            )

        prompt: int | None = count(self.usage.get("prompt_tokens"))
        output: int | None = count(self.usage.get("completion_tokens"))
        if self.output_chars > self.usage_chars:
            output = None
        details: dict[str, object] = (
            self.usage.get("prompt_tokens_details") or {}
        )
        cached: int | None = (
            count(details.get("cached_tokens"))
            if isinstance(details, dict)
            else None
        )
        return prompt, output, cached

    def account_usage(self) -> None:
        if self.accounted:
            return
        prompt: int | None
        output: int | None
        cached: int | None
        prompt, output, cached = self.counts()
        self.session_in += (
            prompt if prompt is not None else math.ceil(self.context / 4)
        )
        self.session_out += (
            output if output is not None else math.ceil(self.output_chars / 4)
        )
        self.session_cached += cached or 0
        self.session_estimated_in |= prompt is None
        self.session_estimated_out |= output is None
        self.accounted = True

    def footer(self) -> Panel:
        return footer(self)

    def body(self, final: bool = False) -> Group | Text:
        records: list[list[str]] = self.records if final else self.records[-8:]
        panels: list[Panel | Text] = []
        for kind, text in records:
            if kind == "reasoning" and not self.show_thoughts:
                continue
            shown: str = text if final else text[-4000:]
            if kind == "reasoning":
                panels.append(
                    Panel(
                        Text(shown, style="grey62"),
                        title="Thinking",
                        title_align="left",
                        border_style="bright_black",
                    )
                )
            elif kind == "tool":
                panels.append(Text(shown.rstrip(), style="dim cyan"))
            else:
                panels.append(
                    Panel(
                        Markdown(shown) if final else Text(shown),
                        title=safe_text(self.model),
                        title_align="left",
                        border_style="blue",
                    )
                )
        if self.error:
            panels.append(
                Panel(
                    Text(self.error, style="red"),
                    title="Stopped",
                    border_style="red",
                )
            )
        return (
            Group(*panels)
            if panels
            else Text("Waiting for model output…", style="dim")
        )

    def render(self) -> Group:
        with self.lock:
            return Group(
                Tail(
                    self.body(),
                    max(
                        1, self.console.size.height - (9 if self.batch else 7)
                    ),
                ),
                self.footer(),
            )

    def finish(self, answer: str | None = None) -> None:
        with self.lock:
            self.splitter.flush()
            if answer and not any(
                kind == "content" for kind, _ in self.records
            ):
                self.append("content", answer)
            if not self.error:
                self.state = "Done"
        if self.live:
            self.live.stop()
            self.live = None
        with self.lock:
            self.console.print(self.body(final=True))
            self.console.print(self.footer())

    def reset(self) -> None:
        # /reset clears model context; usage totals describe the whole terminal
        # session.
        self.console.print(
            "Fresh model context; session token totals retained.", style="dim"
        )

    def error_message(self, text: str) -> None:
        self.console.print(Text(safe_text(text), style="red"))
