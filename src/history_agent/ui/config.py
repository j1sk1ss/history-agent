"""A small, explicit settings editor shown before connecting to services."""

from __future__ import annotations

from argparse import Namespace
from copy import copy, deepcopy
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Label,
    Select,
    Static,
)

from history_agent.configuration.defaults import (
    BOOL_FIELDS,
    COMPATIBLE_PROVIDERS,
    LIMIT_FIELDS,
    LOCAL_PROVIDERS,
    PROVIDERS,
    SAVED_FIELDS,
    save_defaults,
    validate_defaults,
)
from history_agent.configuration.probes import (
    probe_model_connection as probe_model_connection,
)
from history_agent.configuration.probes import (
    probe_system_one_connection as probe_system_one_connection,
)
from history_agent.history.client import Settings
from history_agent.prompts import Prompts
from history_agent.ui.config_capabilities import CapabilityEditor
from history_agent.ui.config_connections import ConnectionChecks
from history_agent.ui.config_models import ModelPicker
from history_agent.ui.settings_help import SETTINGS_HELP


class ConfigurationScreen(ModelPicker, ConnectionChecks, ModalScreen):
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
        self.providers = deepcopy(args.providers)
        self.capabilities = deepcopy(args.capabilities)
        self.connection_revision: int = 0
        self.connection_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        history: Settings = Settings.from_env()
        with VerticalScroll(id="settings"):
            yield Static("History", id="settings-title")
            yield Static(
                "Connect a model and a code history server.", classes="muted"
            )
            with Horizontal(id="connection-status"):
                yield Static("[dim]■ Model: checking…[/]", id="model-status")
                yield Static(
                    "[dim]■ System 1: checking…[/]", id="system-one-status"
                )
                yield Button("Check", id="check-connections")
            yield from self.model_picker()
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
            yield from self.advanced_fields()
            yield Static("", id="settings-error", markup=False)
            with Horizontal(id="settings-actions"):
                yield Button("Save and start", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def advanced_fields(self):
        with Collapsible(title="Advanced", collapsed=True):
            yield Label("Prompt overrides JSON file")
            yield Input(self.args.prompts_file or "", id="prompts_file")
            yield Button("Agent tool settings", id="tool-settings")
            yield Label("System 1 URL")
            yield Input(self.args.system_one_url or "", id="system_one_url")
            yield Label("System 1 model (empty to disable)")
            yield Input(
                self.args.system_one_model or "", id="system_one_model"
            )
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
                yield Checkbox(label, value=getattr(self.args, name), id=name)

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
        self.schedule_connection_check()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        args: Namespace = copy(self.args)
        try:
            args.provider = self.query_one("#provider", Select).value
            for name in (
                "model_url",
                "model",
                "history_url",
                "model_key_env",
                "system_one_url",
                "system_one_model",
                "prompts_file",
            ):
                setattr(
                    args,
                    name,
                    self.query_one("#" + name, Input).value.strip() or None,
                )
            if args.system_one_model and not args.system_one_url:
                raise ValueError("Enter a System 1 URL")
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
            args.capabilities = deepcopy(self.capabilities)
            self.save_profile(args)
            if args.prompts_file:
                Prompts.validate(args.prompts_file)
            validate_defaults(
                {name: getattr(args, name) for name in SAVED_FIELDS}
            )
            if args.provider not in LOCAL_PROVIDERS and not args.model:
                raise ValueError("Enter a model ID for this provider")
            if (
                args.provider in COMPATIBLE_PROVIDERS
                and args.model_url
            ):
                from history_agent.interfaces.cli import model_base

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

    @on(Button.Pressed, "#tool-settings")
    def edit_capabilities(self):
        self.app.push_screen(
            CapabilityEditor(self.capabilities), self.policy_edited
        )

    def policy_edited(self, result):
        if result is not None:
            self.capabilities = result

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
