from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Collapsible, Input, Static

from history_agent.runtime.commands import COMMANDS
from history_agent.ui.text import safe_text


class DetailCard(Vertical):
    def __init__(
        self, title: str, text: str = "", *, kind: str = "notice",
        preview: str | None = None,
    ) -> None:
        super().__init__(classes="card " + kind)
        self.heading: str
        self.text: str
        self.kind: str
        self.heading, self.text, self.kind = (
            safe_text(title),
            safe_text(text),
            kind,
        )
        self.preview_override: str | None = (
            safe_text(preview) if preview is not None else None
        )
        self.dirty: bool = True
        self.active: bool = False

    def compose(self) -> ComposeResult:
        yield Static(Text(self.heading), classes="card-title")
        if self.kind == "tool":
            yield Static("", classes="preview")
            yield Collapsible(
                Static("", classes="full-text"),
                title="Tool output",
                collapsed=True,
            )
        else:
            yield Static("", classes="full-text")

    def add_text(self, text: str) -> None:
        self.text += safe_text(text)
        self.dirty = True

    def refresh_content(self) -> None:
        if not self.is_mounted or not self.dirty:
            return
        self.query_one(".card-title", Static).update(Text(self.heading))
        full: Static = self.query_one(".full-text", Static)
        if self.kind == "tool":
            disclosure: Collapsible = self.query_one(Collapsible)
            lines: list[str] = (
                (
                    self.preview_override
                    if self.preview_override is not None
                    else self.text
                )
                .strip()
                .splitlines()
            )
            summary: str = "\n".join(lines[:4])[:420]
            if len(self.text) > len(summary):
                summary += " ..."
            self.query_one(".preview", Static).update(
                Text(summary, style="grey62")
            )
            disclosure.title = "Tool output"
            self.query_one(".preview", Static).display = disclosure.collapsed
            if not disclosure.collapsed:
                full.update(Text(self.text))
        elif self.kind == "answer":
            full.update(Markdown(self.text))
        else:
            full.update(
                Text(
                    self.text,
                    style="grey70" if self.kind == "reasoning" else None,
                )
            )
        self.dirty = False

    @on(Collapsible.Toggled)
    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        self.dirty = True
        self.refresh_content()


class CommandInput(Input):
    BINDINGS: list[Binding] = [
        Binding("tab", "complete", show=False, priority=True),
        Binding("up", "previous_input", show=False, priority=True),
        Binding("down", "next_input", show=False, priority=True),
    ]

    def __init__(self, state_dir: str | Path) -> None:
        super().__init__(
            placeholder="Ask about code or paste a history link…",
            id="composer",
        )
        self.state_dir: Path = Path(state_dir)
        self.history: list[str]
        self.history_index: int
        self.draft: str
        self.history, self.history_index, self.draft = [], 0, ""
        self.completions: list[str]
        self.complete_index: int
        self.completed_value: str | None
        self.completions, self.complete_index, self.completed_value = (
            [],
            0,
            None,
        )

    def remember(self, text: str) -> None:
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            self.history = self.history[-100:]
        self.history_index = len(self.history)

    def action_previous_input(self) -> None:
        if self.history and self.history_index > 0:
            if self.history_index == len(self.history):
                self.draft = self.value
            self.history_index -= 1
            self.value = self.history[self.history_index]
            self.cursor_position: int = len(self.value)

    def action_next_input(self) -> None:
        if self.history_index < len(self.history):
            self.history_index += 1
            self.value = (
                self.history[self.history_index]
                if self.history_index < len(self.history)
                else self.draft
            )
            self.cursor_position = len(self.value)

    def action_complete(self) -> None:
        if not self.value.startswith("/"):
            self.app.action_focus_next()
            return
        if self.completed_value == self.value and self.completions:
            self.complete_index = (self.complete_index + 1) % len(
                self.completions
            )
            self.value = self.completions[self.complete_index]
        else:
            before: str = self.value[: self.cursor_position]
            head: str
            sep: str
            prefix: str
            head, sep, prefix = before.rpartition(" ")
            if not sep:
                head, prefix = "", before
                options: list[str] = list(COMMANDS)
            elif head.strip() == "/resume":
                options = sorted(
                    path.stem
                    for path in self.state_dir.glob("*.sqlite3")
                    if len(path.stem) == 32
                )
            elif head.split()[:1] == ["/diff"] and len(head.split()) == 5:
                options = ["appeared", "disappeared", "both"]
            else:
                return
            matches: list[str] = [
                option for option in options if option.startswith(prefix)
            ]
            if not matches:
                return
            base: str = head + " " if sep else ""
            self.completions = [base + match for match in matches]
            self.complete_index = 0
            self.value = self.completions[0]
            self.app.show_completions(matches if len(matches) > 1 else [])
        self.completed_value = self.value
        self.cursor_position = len(self.value)
