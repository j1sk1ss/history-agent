"""Sanitize text, split reasoning tags and crop Rich output."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.segment import Segment


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
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        lines: list[list[Segment]] = console.render_lines(
            self.renderable, options.update(height=None), pad=False
        )
        start: int = -self.height
        for line in lines[start:]:
            yield from line
            yield Segment.line()
