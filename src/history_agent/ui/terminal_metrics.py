"""Render token and timing statistics for the legacy terminal UI."""

from __future__ import annotations

import math
import time

from rich.console import Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from history_agent.ui import terminal
from history_agent.ui.text import safe_text


def footer(ui: terminal.TerminalUI) -> Panel:
    now: float = time.monotonic()
    prompt: int | None
    output: int | None
    cached: int | None
    prompt, output, cached = ui.counts()
    input_label: str = (
        str(prompt)
        if prompt is not None
        else "≈" + str(math.ceil(ui.context / 4))
    )
    output_label: str = (
        str(output)
        if output is not None
        else "≈" + str(math.ceil(ui.output_chars / 4))
    )
    generation: float = (
        (ui.request_finished or now) - ui.first_token if ui.first_token else 0
    )
    rate: float = (
        (output if output is not None else math.ceil(ui.output_chars / 4))
        / generation
        if generation > 0.05
        else 0
    )
    ttft: str = (
        (f"{ui.first_token - ui.request_started:.2f}s")
        if ui.first_token
        else "…"
    )
    spinner: Spinner | Text = (
        Spinner("dots", Text(safe_text(ui.state), style="bold cyan"))
        if ui.live
        else Text(ui.state)
    )
    summary: Text = Text(
        (
            f"step {ui.step}  ·  in {input_label}  out {output_label}"
            f" tok  ·  cache {(cached if cached is not None else '?')}"
            f"  ·  {('≈' if output is None else '')}{rate:.1f}"
            f" tok/s  ·  TTFT {ttft}  ·  {now - ui.started:.1f}s"
        ),
        style="cyan",
    )
    totals: Text = session_totals(ui, prompt, output)
    if ui.batch:
        progress: Text = Text(
            safe_text(
                (
                    f"job {ui.batch['job_id']}  ·  {ui.batch['done']}/"
                    f"{ui.batch['total']}  {ui.batch['checker']}  ·  "
                    f"{ui.review_wall:.1f}s review"
                )
            ),
            style="yellow",
        )
        return Panel(
            Group(spinner, progress, summary, totals),
            border_style="bright_black",
        )
    return Panel(Group(spinner, summary, totals), border_style="bright_black")


def session_totals(
    ui: terminal.TerminalUI, prompt: int | None, output: int | None,
) -> Text:
    pending_in: int = (
        0
        if ui.accounted
        else prompt
        if prompt is not None
        else math.ceil(ui.context / 4)
    )
    pending_out: int = (
        0
        if ui.accounted
        else output
        if output is not None
        else math.ceil(ui.output_chars / 4)
    )
    estimated_in: bool = ui.session_estimated_in or (
        not ui.accounted and prompt is None
    )
    estimated_out: bool = ui.session_estimated_out or (
        not ui.accounted and output is None
    )
    totals: Text = Text(
        (
            f"session: in {('≈' if estimated_in else '')}"
            f"{ui.session_in + pending_in}  out "
            f"{('≈' if estimated_out else '')}"
            f"{ui.session_out + pending_out}"
            f"  ·  ≈ estimate until server usage arrives"
        ),
        style="dim",
    )
    return totals
