"""A small, explicit settings editor shown before connecting to services."""

from __future__ import annotations

from argparse import Namespace
from copy import copy
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Label,
    Select,
    Static,
)
from textual.widgets.select import NoSelection

from ..client import Settings
from ..config import (
    BOOL_FIELDS,
    LIMIT_FIELDS,
    LOCAL_PROVIDERS,
    PROVIDER_DEFAULTS,
    PROVIDER_KEYS,
    PROVIDERS,
    SAVED_FIELDS,
    save_defaults,
    validate_defaults,
)
from .settings_help import SETTINGS_HELP


class ConfigurationScreen(ModalScreen):
    TITLE: str = "History settings"
    CSS_PATH: Path = Path(__file__).with_name("config.tcss")
    BINDINGS: list[tuple[str, ...]] = [
        ("escape", "cancel", "Back"),
        ("ctrl+q", "cancel", "Back"),
        ("ctrl+c", "cancel", "Back"),
    ]

    def __init__(self, args: Namespace) -> None:
        super().__init__()
        self.args: Namespace = args

    def compose(self) -> ComposeResult:
        history: Settings = Settings.from_env()
        with VerticalScroll(id="settings"):
            yield Static("History", id="settings-title")
            yield Static(
                "Connect a model and a code history server.", classes="muted"
            )
            yield Label("Provider")
            yield Select(
                [(name, name) for name in PROVIDERS],
                value=self.args.provider,
                allow_blank=False,
                id="provider",
            )
            yield Label("Model URL")
            yield Input(self.args.model_url or "", id="model_url")
            yield Label("Model ID")
            yield Input(
                self.args.model or "",
                placeholder="Auto-detect for local models",
                id="model",
            )
            yield Label("History URL")
            yield Input(
                self.args.history_url or history.url,
                id="history_url",
            )
            yield Label("History username")
            yield Input(
                self.args.history_user
                if self.args.history_user is not None
                else history.user or "",
                id="history_user",
            )
            yield Label("History password")
            yield Input(
                self.args.history_password
                if self.args.history_password is not None
                else history.password or "",
                password=True,
                id="history_password",
            )
            yield Label("API key environment variable")
            yield Input(self.args.model_key_env or "", id="model_key_env")
            yield Checkbox(
                "Save each validated review immediately",
                value=self.args.allow_write,
                id="allow_write",
            )
            with Collapsible(title="Advanced", collapsed=True):
                for name in LIMIT_FIELDS:
                    yield Label(name.replace("_", " ").capitalize())
                    yield Input(str(getattr(self.args, name)), id=name)
                for name, label in (
                    ("no_thinking", "Disable thinking (Qwen / local models)"),
                    ("no_stream", "Disable streaming"),
                    ("no_stream_usage", "Disable streaming usage reporting"),
                    ("stats", "Show detailed statistics"),
                    ("no_patterns", "Disable reusable review checklists"),
                ):
                    yield Checkbox(
                        label, value=getattr(self.args, name), id=name
                    )
            yield Static("", id="settings-error", markup=False)
            with Horizontal(id="settings-actions"):
                yield Button("Save and start", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        for name, description in SETTINGS_HELP.items():
            self.query_one("#" + name).tooltip = description
        for label in self.query(Label):
            siblings: list[DOMNode] = list(label.parent.children)
            index: int = siblings.index(label) + 1
            if index < len(siblings):
                label.tooltip = SETTINGS_HELP.get(siblings[index].id)
        self.update_placeholders()
        self.query_one("#model_url", Input).focus()

    def update_placeholders(self) -> None:
        provider: str | NoSelection = self.query_one("#provider", Select).value
        self.query_one(
            "#model_url", Input
        ).placeholder = PROVIDER_DEFAULTS.get(
            provider, "http://127.0.0.1:8000/v1"
        )
        self.query_one(
            "#model_key_env", Input
        ).placeholder = PROVIDER_KEYS.get(
            provider, "HISTORY_AGENT_MODEL_API_KEY"
        )

    @on(Select.Changed, "#provider")
    def provider_changed(self) -> None:
        self.update_placeholders()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        args: Namespace = copy(self.args)
        try:
            args.provider = self.query_one("#provider", Select).value
            for name in ("model_url", "model", "history_url", "model_key_env"):
                setattr(
                    args,
                    name,
                    self.query_one("#" + name, Input).value.strip() or None,
                )
            args.history_user = self.query_one(
                "#history_user", Input
            ).value.strip()
            args.history_password = self.query_one(
                "#history_password", Input
            ).value
            if not args.history_user and not args.history_password:
                args.history_user = args.history_password = None
            elif not args.history_user or not args.history_password:
                raise ValueError(
                    "Enter both the history server username and password"
                )
            if not args.model_url and args.provider == "openai-compatible":
                args.model_url = "http://127.0.0.1:8000/v1"
            for name in LIMIT_FIELDS:
                convert: type[int] | type[float] = (
                    float if name == "timeout" else int
                )
                setattr(
                    args,
                    name,
                    convert(self.query_one("#" + name, Input).value),
                )
            for name in BOOL_FIELDS:
                setattr(args, name, self.query_one("#" + name, Checkbox).value)
            validate_defaults(
                {name: getattr(args, name) for name in SAVED_FIELDS}
            )
            if args.provider not in LOCAL_PROVIDERS and not args.model:
                raise ValueError("Enter a model ID for this provider")
            if (
                args.provider in (*LOCAL_PROVIDERS, "deepseek")
                and args.model_url
            ):
                from ..cli import model_base

                model_base(args.model_url)
            if args.overwrite_reviews and not args.allow_write:
                raise ValueError(
                    "Overwriting reviews requires allowing review writes"
                )
            save_defaults(args)
        except (ValueError, OSError) as error:
            self.query_one("#settings-error", Static).update(str(error))
            self.query_one("#settings-error").scroll_visible()
            return
        self.dismiss(args)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss()


class ConfigurationApp(App):
    """Standalone settings entry point for initial setup and --configure."""

    TITLE: str = "History settings"
    ENABLE_COMMAND_PALETTE: bool = False

    def __init__(self, args: Namespace) -> None:
        super().__init__()
        self.args: Namespace = args

    def on_mount(self) -> None:
        self.push_screen(ConfigurationScreen(self.args), self.exit)
