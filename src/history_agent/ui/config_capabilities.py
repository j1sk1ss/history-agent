"""Edit host tool policy without exposing arbitrary commands to the model."""

import json

from textual import on
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static, TextArea

from history_agent.configuration.capabilities import CapabilitySettings


class CapabilityEditor(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    CapabilityEditor { align: center middle; background: #101010; }
    CapabilityEditor VerticalScroll { width: 80; max-width: 100%;
        height: auto; max-height: 100%; padding: 1 3; }
    CapabilityEditor TextArea { height: 9; }
    CapabilityEditor Horizontal { height: auto; margin-top: 1; }
    CapabilityEditor #policy-error { height: auto; color: red; }
    """

    def __init__(self, values):
        super().__init__()
        self.settings = CapabilitySettings.model_validate(values)

    def compose(self):
        with VerticalScroll():
            yield Static("Agent tools")
            yield Checkbox(
                "Allow questions to the user",
                self.settings.questions,
                id="questions",
            )
            yield Checkbox(
                "Enable library documentation",
                self.settings.documentation.enabled,
                id="documentation",
            )
            yield Label("Search API key environment variable")
            yield Input(
                self.settings.documentation.api_key_env, id="search-key-env"
            )
            yield Label("Library → approved documentation hosts (JSON)")
            yield TextArea(
                json.dumps(self.settings.documentation.libraries, indent=2),
                id="libraries",
            )
            yield Checkbox(
                "Enable local language servers",
                self.settings.lsp.enabled,
                id="enable-lsp",
            )
            yield Label("Language → installed server command argv (JSON)")
            yield TextArea(
                json.dumps(self.settings.lsp.commands, indent=2),
                id="lsp-commands",
            )
            yield Label("Language server timeout in seconds")
            yield Input(str(self.settings.lsp.timeout), id="lsp-timeout")
            yield Static("", id="policy-error", markup=False)
            with Horizontal():
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
                        "api_key_env": self.query_one(
                            "#search-key-env", Input
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
        except ValueError as error:
            self.query_one("#policy-error", Static).update(str(error))
            return
        self.dismiss(settings.model_dump())

    @on(Button.Pressed, "#cancel-policy")
    def action_cancel(self):
        self.dismiss()
