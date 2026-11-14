"""Live connection indicators for the settings form."""

import asyncio
import math
from copy import copy

from textual import on
from textual.message_pump import MessagePump
from textual.widgets import Button, Checkbox, Input, Select, Static
from textual.widgets.select import NoSelection

from history_agent.configuration.defaults import (
    PROVIDER_DEFAULTS,
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

    @on(Select.Changed, "#provider")
    def provider_changed(self) -> None:
        self.update_placeholders()
        self.schedule_connection_check()

    @on(Input.Changed)
    def connection_changed(self, event: Input.Changed) -> None:
        if event.input.id in (
            "model_url",
            "model",
            "model_api_key",
            "system_one_url",
            "system_one_model",
            "timeout",
            "max_output_tokens",
        ):
            self.schedule_connection_check()

    @on(Checkbox.Changed)
    def connection_option_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id in (
            "no_thinking",
            "no_stream",
            "no_stream_usage",
        ):
            self.schedule_connection_check()

    def schedule_connection_check(self) -> None:
        if not self.is_mounted:
            return
        self.connection_revision += 1
        if self.connection_timer:
            self.connection_timer.stop()
        self.query_one("#model-connection-error", Static).update("")
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
            "system_one_url",
            "system_one_model",
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
        try:
            args.timeout = float(self.query_one("#timeout", Input).value)
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ValueError()
            args.max_output_tokens = int(
                self.query_one("#max_output_tokens", Input).value
            )
            if args.max_output_tokens <= 0:
                raise ValueError()
        except ValueError:
            self.query_one("#model-status", Static).update(
                "[red]■[/] Model: invalid request settings"
            )
            self.query_one("#model-connection-error", Static).update(
                "Timeout and output tokens must be positive numbers."
            )
            return
        for name in ("no_thinking", "no_stream", "no_stream_usage"):
            setattr(args, name, self.query_one("#" + name, Checkbox).value)
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
            if name == "model":
                self.query_one("#model-connection-error", Static).update(
                    "" if connected else detail
                )
