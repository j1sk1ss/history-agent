"""Shared form spacing and section headings for settings dialogs."""

from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, Static


class SettingsField(Vertical):
    def __init__(self, label: str, control: Widget):
        super().__init__(Label(label), control, classes="settings-field")


class SettingsGroup(Vertical):
    def __init__(self, title: str, *, id: str | None = None):
        super().__init__(
            Static(title, classes="section-title"),
            id=id,
            classes="settings-group",
        )
