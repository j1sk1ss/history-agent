"""Local terminal presentation; none of this state is sent to the model."""

from __future__ import annotations

import math
import threading
import time
from _thread import RLock
from collections import deque
from collections.abc import Callable

from rich.console import (
    Console,
    ConsoleOptions,
    Group,
    RenderableType,
    RenderResult,
)
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.spinner import Spinner
from rich.text import Text


def safe_text(value: object) -> str:
    # Model/source text must not inject terminal control sequences or Rich
    # markup.
    return "".join(
        c
        for c in str(value)
        if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160)
    )


class ThinkingText:
    """Split <think> tags even when delimiters cross network chunks."""

    def __init__(self, emit: Callable[[str, str], None]) -> None:
        self.emit: Callable[[str, str], None]
        self.pending: str
        self.thinking: bool
        self.emit, self.pending, self.thinking = emit, "", False

    def feed(self, text: str) -> None:
        self.pending += text
        while self.pending:
            marker: str = "</think>" if self.thinking else "<think>"
            position: int = self.pending.find(marker)
            if position >= 0:
                self.emit(
                    "reasoning" if self.thinking else "content",
                    self.pending[:position],
                )
                marker_end: int = position + len(marker)
                self.pending = self.pending[marker_end:]
                self.thinking = not self.thinking
                continue
            keep: int = 0
            for length in range(1, min(len(marker), len(self.pending) + 1)):
                if self.pending.endswith(marker[:length]):
                    keep = length
            ready: str = self.pending[:-keep] if keep else self.pending
            self.pending = self.pending[-keep:] if keep else ""
            self.emit("reasoning" if self.thinking else "content", ready)
            break

    def flush(self) -> None:
        if self.pending:
            self.emit(
                "reasoning" if self.thinking else "content", self.pending
            )
            self.pending = ""


class Tail:
    def __init__(self, renderable: RenderableType, height: int) -> None:
        self.renderable: RenderableType
        self.height: int
        self.renderable, self.height = renderable, height

    def __rich_console__(
        self, console: Console, options: ConsoleOptions,
    ) -> RenderResult:
        lines: list[list[Segment]] = console.render_lines(
            self.renderable, options.update(height=None), pad=False
        )
        start: int = -self.height
        for line in lines[start:]:
            yield from line
            yield Segment.line()


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
            now: float = time.monotonic()
            if kind == "batch_started":
                self.batch = {
                    "job_id": data["job_id"],
                    "done": 0,
                    "total": 0,
                    "checker": "",
                }
                self.records = []
                self.state = "Building review queue"
            elif kind == "batch_queue":
                self.batch.update(total=data["total"], checker=data["checker"])
            elif kind == "batch_progress":
                self.batch = dict(data)
                self.records = []
                self.splitter = ThinkingText(self.append)
                self.state = "Loading warning / restoring checker context"
            elif kind == "batch_finished":
                self.records, self.batch = [], None
                self.state = "Batch complete"
            elif kind == "batch_summary":
                self.append("content", data["text"])
            elif kind == "batch_format_repair":
                self.append(
                    "tool",
                    "Formatting verdict (one attempt, no tools or writes)\n",
                )
                for issue in data.get("issues", []):
                    self.append("tool", "  " + issue + "\n")
                self.state = "Formatting verdict"
            elif kind == "history_link":
                scope: dict[str, str | int] = data["scope"]
                subset: str = (
                    (" · " + data["subset"]) if data.get("subset") else ""
                )
                self.append(
                    "tool",
                    f"Resolved history link: project {scope['project_id']} · "
                    f"{scope['stream_id']} · {scope['snapshot_id']}{subset}\n",
                )
            elif kind == "request_start":
                self.splitter.flush()
                self.splitter = ThinkingText(self.append)
                self.step, self.context = data["step"], data["context_bytes"]
                self.request_started, self.first_token = now, None
                self.request_finished = None
                self.output_chars, self.usage, self.accounted = 0, {}, False
                self.usage_chars = 0
                self.state = "Prefill / waiting for first token"
            elif kind in ("content", "reasoning", "tool_delta"):
                if data["text"]:
                    self.first_token = self.first_token or now
                self.output_chars += len(data["text"])
                if kind == "content":
                    self.splitter.feed(data["text"])
                    self.state = (
                        "Generating answer"
                        if not self.splitter.thinking
                        else "Thinking"
                    )
                elif kind == "reasoning":
                    self.append("reasoning", data["text"])
                    self.state = "Thinking"
                else:
                    self.state = "Receiving tool call"
            elif kind == "usage":
                if isinstance(data.get("usage"), dict):
                    self.usage.update(data["usage"])
                    self.usage_chars = self.output_chars
            elif kind == "request_end":
                self.splitter.flush()
                self.usage.update(data.get("usage") or {})
                self.usage_chars = self.output_chars
                self.account_usage()
                self.request_finished = now
                self.state = "Response received"
            elif kind == "tool_start":
                label: str = safe_text(
                    data["name"]
                    + " · "
                    + str(data.get("action") or "list actions")
                )
                self.events.append(label)
                self.append("tool", label + "\n")
                self.state = "Running " + safe_text(data["name"])
            elif kind == "tool_end":
                label = safe_text(data["name"])
                status: str = "error" if data.get("error") else "done"
                self.events.append(
                    f"{status} · {label} · {data['seconds']:.2f}s · "
                    f"{data['bytes']} B"
                )
                self.append(
                    "tool",
                    f"  {status} · {data['seconds']:.2f}s · "
                    f"{data['bytes']} B\n",
                )
            elif kind == "failure":
                self.splitter.flush()
                self.error = safe_text(data["message"])
                self.request_finished = now
                self.account_usage()
                self.state = "Stopped — partial output"

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
        now: float = time.monotonic()
        prompt: int | None
        output: int | None
        cached: int | None
        prompt, output, cached = self.counts()
        input_label: str = (
            str(prompt)
            if prompt is not None
            else "≈" + str(math.ceil(self.context / 4))
        )
        output_label: str = (
            str(output)
            if output is not None
            else "≈" + str(math.ceil(self.output_chars / 4))
        )
        generation: float = (
            (self.request_finished or now) - self.first_token
            if self.first_token
            else 0
        )
        rate: float = (
            (
                output
                if output is not None
                else math.ceil(self.output_chars / 4)
            )
            / generation
            if generation > 0.05
            else 0
        )
        ttft: str = (
            f"{self.first_token - self.request_started:.2f}s"
            if self.first_token
            else "…"
        )
        spinner: Spinner | Text = (
            Spinner("dots", Text(safe_text(self.state), style="bold cyan"))
            if self.live
            else Text(self.state)
        )
        summary: Text = Text(
            f"step {self.step}  ·  in {input_label}  "
            f"out {output_label} tok  ·  "
            f"cache {cached if cached is not None else '?'}  ·  "
            f"{'≈' if output is None else ''}{rate:.1f} tok/s  ·  "
            f"TTFT {ttft}  ·  {now - self.started:.1f}s",
            style="cyan",
        )
        pending_in: int = (
            0
            if self.accounted
            else prompt
            if prompt is not None
            else math.ceil(self.context / 4)
        )
        pending_out: int = (
            0
            if self.accounted
            else output
            if output is not None
            else math.ceil(self.output_chars / 4)
        )
        estimated_in: bool = self.session_estimated_in or (
            not self.accounted and prompt is None
        )
        estimated_out: bool = self.session_estimated_out or (
            not self.accounted and output is None
        )
        totals: Text = Text(
            f"session: in {'≈' if estimated_in else ''}"
            f"{self.session_in + pending_in}  "
            f"out {'≈' if estimated_out else ''}"
            f"{self.session_out + pending_out}  ·  "
            "≈ estimate until server usage arrives",
            style="dim",
        )
        if self.batch:
            progress: Text = Text(
                safe_text(
                    f"job {self.batch['job_id']}  ·  "
                    f"{self.batch['done']}/{self.batch['total']}  "
                    f"{self.batch['checker']}"
                ),
                style="yellow",
            )
            return Panel(
                Group(spinner, progress, summary, totals),
                border_style="bright_black",
            )
        return Panel(
            Group(spinner, summary, totals), border_style="bright_black"
        )

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
