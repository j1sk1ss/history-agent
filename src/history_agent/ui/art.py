"""Terminal artwork without runtime fonts or image support."""

from pathlib import Path

from rich.text import Text
from textual import events
from textual.reactive import reactive
from textual.widgets import Static


class WelcomeArt(Static):
    """Overlap a white wordmark with a rising pink cursive signature."""

    compact_below = reactive(24)

    def __init__(self):
        artwork = (
            Path(__file__).with_name("logo.txt").read_text(encoding="utf-8")
        )
        self.full_art = Text.from_markup(artwork.rstrip("\n"))
        self.full_art.no_wrap = True
        self.compact_art = Text.assemble(
            ("SAMSUNG-DATA\n", "bold #f5f0f3"),
            ("        Vibe\n", "bold italic #f56bb7"),
            ("      ━━━━━━━╱", "#f56bb7"),
        )
        self.compact = False
        super().__init__(self.full_art, id="home-title")

    def on_resize(self, event: events.Resize) -> None:
        self.update_art()

    def watch_compact_below(self) -> None:
        if self.is_mounted:
            self.update_art()

    def update_art(self, *, screen_height: int | None = None) -> None:
        if screen_height is None:
            screen_height = self.app.size.height
        compact = self.size.width < 71 or screen_height < self.compact_below
        if compact != self.compact:
            self.compact = compact
            self.update(self.compact_art if compact else self.full_art)
