"""Edit host tool policy without exposing arbitrary commands to the model."""

import json
from pathlib import Path

from pydantic import ValidationError
from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static, TextArea

from history_agent.configuration.capabilities import CapabilitySettings
from history_agent.configuration.credentials import resolve_api_key
from history_agent.ui.config_fields import SettingsField, SettingsGroup


class CapabilityEditor(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS_PATH = Path(__file__).with_name("config.tcss")

    def __init__(self, values):
        super().__init__()
        self.settings = CapabilitySettings.model_validate(values)

    def compose(self):
        with Vertical(classes="settings-dialog"):
            yield Static("Agent tools", classes="dialog-title")
            with VerticalScroll(classes="settings-body"):
                with SettingsGroup("Questions"):
                    yield Checkbox(
                        "Allow questions to the user",
                        self.settings.questions,
                        id="questions",
                    )
                with SettingsGroup("Library documentation"):
                    yield Checkbox(
                        "Enable library documentation",
                        self.settings.documentation.enabled,
                        id="documentation",
                    )
                    yield SettingsField(
                        "Search API key",
                        Input(
                            resolve_api_key(
                                self.settings.documentation.api_key,
                                self.settings.documentation.api_key_env,
                            )
                            or "",
                            password=True,
                            id="search-api-key",
                            placeholder="Paste Brave Search API key",
                        ),
                    )
                    yield SettingsField(
                        "Library → approved hosts (JSON)",
                        TextArea(
                            json.dumps(
                                self.settings.documentation.libraries, indent=2
                            ),
                            id="libraries",
                        ),
                    )
                with SettingsGroup("Language servers"):
                    yield Checkbox(
                        "Enable local language servers",
                        self.settings.lsp.enabled,
                        id="enable-lsp",
                    )
                    yield SettingsField(
                        "Installed server commands (JSON)",
                        TextArea(
                            json.dumps(self.settings.lsp.commands, indent=2),
                            id="lsp-commands",
                        ),
                    )
                    yield SettingsField(
                        "Timeout (seconds)",
                        Input(
                            str(self.settings.lsp.timeout),
                            id="lsp-timeout",
                        ),
                    )
            yield Static("", id="policy-error", markup=False)
            with Horizontal(classes="settings-actions"):
                yield Button("Apply", id="apply-policy", variant="primary")
                yield Button("Cancel", id="cancel-policy")

    @on(Button.Pressed, "#apply-policy")
    def apply(self):
        try:
            settings = CapabilitySettings.model_validate(
                {
                    "questions": self.query_one("#questions", Checkbox).value,
                    "documentation": {
                        "enabled": self.query_one(
                            "#documentation", Checkbox
                        ).value,
                        "api_key": self.query_one(
                            "#search-api-key", Input
                        ).value.strip(),
                        "libraries": json.loads(
                            self.query_one("#libraries", TextArea).text
                        ),
                    },
                    "lsp": {
                        "enabled": self.query_one(
                            "#enable-lsp", Checkbox
                        ).value,
                        "commands": json.loads(
                            self.query_one("#lsp-commands", TextArea).text
                        ),
                        "timeout": float(
                            self.query_one("#lsp-timeout", Input).value
                        ),
                    },
                }
            )
        except ValidationError as error:
            issue = error.errors(include_input=False)[0]
            self.query_one("#policy-error", Static).update(
                ".".join(map(str, issue["loc"])) + ": " + issue["msg"]
            )
            return
        except ValueError as error:
            self.query_one("#policy-error", Static).update(str(error))
            return
        self.dismiss(settings.for_storage())

    @on(Button.Pressed, "#cancel-policy")
    def action_cancel(self):
        self.dismiss()
