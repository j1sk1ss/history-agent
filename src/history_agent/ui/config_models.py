"""Select models and edit provider connections using form fields."""

from copy import deepcopy
from pathlib import Path

from textual import on
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.message_pump import MessagePump
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from history_agent.configuration.credentials import provider_api_key
from history_agent.configuration.defaults import LOCAL_PROVIDERS, PROVIDERS
from history_agent.configuration.models import ModelCatalog
from history_agent.ui.config_fields import SettingsField, SettingsGroup


class ModelPicker(MessagePump):
    def model_picker(self):
        yield SettingsField(
            "Saved model",
            Select(
                [
                    ("Manual connection", ""),
                    *ModelCatalog.choices(self.providers),
                ],
                value=self.args.model_profile or "",
                allow_blank=False,
                id="model_profile",
            ),
        )
        with Grid(classes="model-buttons settings-row"):
            yield Button("Add", id="add-model")
            yield Button("Edit", id="edit-model")
            yield Button("Remove", id="remove-model")

    @on(Select.Changed, "#model_profile")
    def profile_changed(self, event):
        if not event.value or not isinstance(event.value, str):
            return
        values = ModelCatalog.select(self.providers, event.value)
        values["model_api_key"] = provider_api_key(
            values["provider"], values["model_api_key"],
            values.pop("model_key_env"),
        )
        self.query_one("#provider", Select).value = values.pop("provider")
        if self.query_one("#provider", Select).value not in LOCAL_PROVIDERS:
            self.query_one("#no_thinking", Checkbox).value = False
        for field, value in values.items():
            self.query_one("#" + field, Input).value = str(value or "")
        self.schedule_connection_check()

    @on(Button.Pressed, "#add-model, #edit-model")
    def edit_model(self, event):
        reference = self.query_one("#model_profile", Select).value
        if event.button.id == "add-model":
            reference = ""
        self.app.push_screen(
            ModelEditor(self.providers, reference), self.model_edited
        )

    def model_edited(self, result):
        if result is None:
            return
        self.providers, reference = result
        picker = self.query_one("#model_profile", Select)
        picker.set_options(
            [
                ("Manual connection", ""),
                *ModelCatalog.choices(self.providers),
            ]
        )
        picker.value = reference
        # Updating the same entry must also refresh its connection fields.
        self.profile_changed(Select.Changed(picker, reference))

    @on(Button.Pressed, "#remove-model")
    def remove_model(self):
        reference = self.query_one("#model_profile", Select).value
        if not reference or not isinstance(reference, str):
            return
        provider, _, model = reference.partition("/")
        self.providers[provider]["models"].pop(model)
        if not self.providers[provider]["models"]:
            self.providers.pop(provider)
        self.model_edited((self.providers, ""))

    def save_profile(self, args):
        args.providers = deepcopy(self.providers)
        args.model_profile = (
            self.query_one("#model_profile", Select).value or None
        )
        if not args.model_profile:
            return
        if not args.model:
            raise ValueError("Enter a model ID for the saved connection")
        provider, _, model = args.model_profile.partition("/")
        connection = args.providers[provider]
        connection.update(
            transport=args.provider,
            base_url=args.model_url,
            api_key=args.model_api_key,
        )
        connection.pop("api_key_env", None)
        options = connection["models"][model]
        for field in (
            "max_output_tokens",
            "max_context_bytes",
            "max_tool_result_bytes",
        ):
            options[field] = getattr(args, field)
        # Model ID edits rename the saved entry.
        if args.model != model:
            if args.model in connection["models"]:
                raise ValueError(
                    "A model with this ID already exists in the connection"
                )
            connection["models"][args.model] = connection["models"].pop(model)
            args.model_profile = provider + "/" + args.model
        ModelCatalog.validate(args.providers)


class ModelEditor(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS_PATH = Path(__file__).with_name("config.tcss")

    def __init__(self, providers, reference):
        super().__init__()
        self.providers = deepcopy(providers)
        self.reference = reference

    def compose(self):
        provider, _, model = (self.reference or "").partition("/")
        values = (
            ModelCatalog.select(self.providers, self.reference)
            if self.reference
            else {}
        )
        key = provider_api_key(
            values["provider"], values.get("model_api_key"),
            values.get("model_key_env"),
        ) if self.reference else ""
        with Vertical(classes="settings-dialog"):
            yield Static("Model connection", classes="dialog-title")
            with VerticalScroll(classes="settings-body"):
                with SettingsGroup("Connection"):
                    yield SettingsField(
                        "Connection name",
                        Input(
                            provider,
                            id="connection_name",
                        ),
                    )
                    yield SettingsField(
                        "API protocol",
                        Select(
                            [(name, name) for name in PROVIDERS],
                            value=values.get("provider", "openai-compatible"),
                            allow_blank=False,
                            id="connection_transport",
                        ),
                    )
                    yield SettingsField(
                        "API base URL",
                        Input(
                            values.get("model_url") or "",
                            id="connection_url",
                        ),
                    )
                    yield SettingsField(
                        "API key",
                        Input(
                            key or "",
                            password=True,
                            id="connection_api_key",
                            placeholder="Paste API key; empty if not required",
                        ),
                    )
                with SettingsGroup("Model"):
                    yield SettingsField(
                        "Model ID", Input(model, id="model_id")
                    )
            yield Static("", id="model-editor-error", markup=False)
            with Horizontal(classes="settings-actions"):
                yield Button("Apply", id="apply-model", variant="primary")
                yield Button("Cancel", id="cancel-model")

    @on(Button.Pressed, "#apply-model")
    def apply(self):
        try:
            name = self.query_one("#connection_name", Input).value.strip()
            model = self.query_one("#model_id", Input).value.strip()
            previous = self.providers.get(name, {}).get("models", {})
            values = {
                "transport": self.query_one(
                    "#connection_transport", Select
                ).value,
                "base_url": self.query_one(
                    "#connection_url", Input
                ).value.strip(),
                "api_key": self.query_one(
                    "#connection_api_key", Input
                ).value.strip(),
                "models": {**previous, model: previous.get(model, {})},
            }
            updated = {**deepcopy(self.providers), name: values}
            if self.reference and self.reference != name + "/" + model:
                old_provider, _, old_model = self.reference.partition("/")
                updated[old_provider]["models"].pop(old_model, None)
                if not updated[old_provider]["models"]:
                    updated.pop(old_provider)
            ModelCatalog.validate(updated)
        except ValueError as error:
            self.query_one("#model-editor-error", Static).update(str(error))
            return
        self.dismiss((updated, name + "/" + model))

    @on(Button.Pressed, "#cancel-model")
    def action_cancel(self):
        self.dismiss()
