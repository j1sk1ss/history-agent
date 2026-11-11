"""Live connection indicators for the settings form."""

import asyncio
from copy import copy

from textual import on
from textual.message_pump import MessagePump
from textual.widgets import Button, Input, Select, Static
from textual.widgets.select import NoSelection

from history_agent.configuration.defaults import (
    PROVIDER_DEFAULTS,
    PROVIDER_KEYS,
)
from history_agent.ui import config


class ConnectionChecks(MessagePump):
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
        self.schedule_connection_check()

    @on(Input.Changed)
    def connection_changed(self, event: Input.Changed) -> None:
        if event.input.id in (
            "model_url",
            "model",
            "model_key_env",
            "system_one_url",
            "system_one_model",
        ):
            self.schedule_connection_check()

    def schedule_connection_check(self) -> None:
        if not self.is_mounted:
            return
        self.connection_revision += 1
        if self.connection_timer:
            self.connection_timer.stop()
        for name, label in (("model", "Model"), ("system-one", "System 1")):
            self.query_one("#" + name + "-status", Static).update(
                f"[dim]■ {label}: checking…[/]"
            )
        self.connection_timer = self.set_timer(
            0.4, self.start_connection_check
        )

    @on(Button.Pressed, "#check-connections")
    def start_connection_check(self) -> None:
        if self.connection_timer:
            self.connection_timer.stop()
        self.run_worker(
            self.check_connections(),
            group="connections",
            exclusive=True,
            exit_on_error=False,
        )

    async def check_connections(self) -> None:
        revision = self.connection_revision
        args = copy(self.args)
        args.provider = self.query_one("#provider", Select).value
        for name in (
            "model_url",
            "model",
            "model_key_env",
            "system_one_url",
            "system_one_model",
        ):
            setattr(
                args,
                name,
                self.query_one("#" + name, Input).value.strip() or None,
            )
        results = await asyncio.gather(
            asyncio.to_thread(config.probe_model_connection, args),
            asyncio.to_thread(config.probe_system_one_connection, args),
        )
        if not self.is_mounted or revision != self.connection_revision:
            return
        for (name, label), (connected, detail) in zip(
            (("model", "Model"), ("system-one", "System 1")),
            results,
        ):
            color = "green" if connected else "red"
            state = "connected" if connected else "unavailable"
            if name == "system-one" and not args.system_one_model:
                state = "disabled"
            widget = self.query_one("#" + name + "-status", Static)
            widget.update(f"[{color}]■[/] {label}: {state}")
            widget.tooltip = detail
