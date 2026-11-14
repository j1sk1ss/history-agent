"""A small, explicit settings editor shown before connecting to services."""

from __future__ import annotations

from argparse import Namespace
from copy import copy, deepcopy
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
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
from history_agent.prompts import Prompts
from history_agent.ui.config_capabilities import CapabilityEditor
from history_agent.ui.config_connections import ConnectionChecks
from history_agent.ui.config_models import ModelPicker
from history_agent.ui.config_sections import SettingsSections
from history_agent.ui.settings_help import SETTINGS_HELP


class ConfigurationScreen(
    SettingsSections,
    ModelPicker,
    ConnectionChecks,
    ModalScreen,
):
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
        with Vertical(id="settings-dialog", classes="settings-dialog"):
            yield Static("Settings", id="settings-title")
            yield Static(
                "Connect a model and a code history server.", classes="muted"
            )
            with VerticalScroll(id="settings", classes="settings-body"):
                yield from self.model_fields()
                yield from self.history_fields()
                yield from self.review_fields()
                yield from self.optional_fields()
            yield Static("", id="settings-error", markup=False)
            with Horizontal(id="settings-actions", classes="settings-actions"):
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
        self.query_one("#model_profile", Select).focus()
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
                "system_one_url",
                "system_one_model",
                "prompts_file",
                "history_redis_url",
            ):
                setattr(
                    args,
                    name,
                    self.query_one("#" + name, Input).value.strip() or None,
                )
            args.model_api_key = self.query_one(
                "#model_api_key", Input
            ).value.strip()
            args.model_key_env = None
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
                    float if name in ("timeout", "history_timeout") else int
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
            if args.provider in COMPATIBLE_PROVIDERS and args.model_url:
                from history_agent.interfaces.cli import model_base

                model_base(args.model_url)
            if args.overwrite_reviews:
                raise ValueError(
                    "Review overwrites are disabled; only UD can be saved"
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
