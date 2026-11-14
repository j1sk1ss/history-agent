"""Settings grouped by the service or behavior they control."""

from textual.containers import Horizontal
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Select,
    Static,
)

from history_agent.configuration.credentials import model_api_key
from history_agent.configuration.defaults import PROVIDERS
from history_agent.history.settings import Settings
from history_agent.ui.config_fields import SettingsField, SettingsGroup


class SettingsSections:
    def model_fields(self):
        with SettingsGroup("Model", id="model-settings"):
            yield from self.model_picker()
            yield SettingsField(
                "API protocol / provider",
                Select(
                    [(name, name) for name in PROVIDERS],
                    value=self.args.provider,
                    allow_blank=False,
                    id="provider",
                ),
            )
            yield SettingsField(
                "Model URL",
                Input(
                    self.args.model_url or "",
                    id="model_url",
                ),
            )
            yield SettingsField(
                "Model ID",
                Input(
                    self.args.model or "",
                    id="model",
                    placeholder="Auto-detect for local models",
                ),
            )
            yield SettingsField(
                "API key",
                Input(
                    model_api_key(self.args) or "",
                    password=True,
                    placeholder="Paste API key; leave empty if not required",
                    id="model_api_key",
                ),
            )
            with Horizontal(id="connection-status", classes="settings-row"):
                yield Static("[dim]■ Model: checking…[/]", id="model-status")
                yield Button("Check", id="check-connections")
            yield Static("", id="model-connection-error", markup=False)

    def history_fields(self):
        history: Settings = Settings.from_env()
        with SettingsGroup("History server", id="history-settings"):
            yield SettingsField(
                "History URL",
                Input(
                    self.args.history_url or history.url,
                    id="history_url",
                ),
            )
            yield SettingsField(
                "Username",
                Input(
                    self.args.history_user
                    if self.args.history_user is not None
                    else history.user or "",
                    id="history_user",
                ),
            )
            yield SettingsField(
                "Password",
                Input(
                    self.args.history_password
                    if self.args.history_password is not None
                    else history.password or "",
                    password=True,
                    id="history_password",
                ),
            )

    def review_fields(self):
        with SettingsGroup("Review", id="review-settings"):
            yield Checkbox(
                "Save each validated review immediately",
                value=self.args.allow_write,
                id="allow_write",
            )
            yield Checkbox(
                "Disable reusable review checklists",
                value=self.args.no_patterns,
                id="no_patterns",
            )

    def optional_fields(self):
        with Collapsible(
            title="System 1 adviser",
            id="system-one-settings",
            collapsed=not bool(self.args.system_one_model),
        ):
            yield SettingsField(
                "Endpoint URL",
                Input(
                    self.args.system_one_url or "",
                    id="system_one_url",
                ),
            )
            yield SettingsField(
                "Model ID (empty to disable)",
                Input(
                    self.args.system_one_model or "",
                    id="system_one_model",
                ),
            )
            yield Static(
                "[dim]■ System 1: checking…[/]", id="system-one-status"
            )
        with Collapsible(
            title="Agent instructions and tools", id="agent-settings"
        ):
            yield SettingsField(
                "Prompt overrides JSON file",
                Input(
                    self.args.prompts_file or "",
                    id="prompts_file",
                ),
            )
            yield Button("Configure agent tools", id="tool-settings")
        with Collapsible(title="Limits and timeouts", id="limits-settings"):
            for name, label in (
                ("max_steps", "Model requests per investigation"),
                ("max_output_tokens", "Output tokens per request"),
                ("max_context_bytes", "Context budget (JSON bytes)"),
                ("max_tool_result_bytes", "Tool result budget (bytes)"),
                ("timeout", "Model response timeout (seconds)"),
            ):
                yield SettingsField(
                    label,
                    Input(
                        str(getattr(self.args, name)),
                        id=name,
                    ),
                )
        with Collapsible(
            title="Model response and display", id="output-settings"
        ):
            for name, label in (
                ("no_thinking", "Disable thinking (Qwen / local models)"),
                ("no_stream", "Disable streaming"),
                ("no_stream_usage", "Disable streaming usage reporting"),
                ("stats", "Show detailed statistics"),
            ):
                yield Checkbox(label, value=getattr(self.args, name), id=name)
