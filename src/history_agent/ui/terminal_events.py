"""Present backend events without owning the application loop."""

from __future__ import annotations

import time
from collections.abc import Callable

from history_agent.ui import terminal as view
from history_agent.ui.text import ThinkingText, safe_text


class TerminalPresenter:
    def __init__(self, ui: view.TerminalUI) -> None:
        self.ui: view.TerminalUI = ui

    def handle(self, kind: str, data: dict[str, object]) -> None:
        handlers: dict[str, Callable[[str, dict[str, object]], None]] = {
            "batch_started": self.handle_batch_started,
            "batch_queue": self.handle_batch_queue,
            "batch_progress": self.handle_batch_progress,
            "batch_finished": self.handle_batch_finished,
            "batch_summary": self.handle_batch_summary,
            "batch_format_repair": self.handle_batch_format_repair,
            "history_link": self.handle_history_link,
            "request_start": self.handle_request_start,
            "content": self.handle_content,
            "reasoning": self.handle_content,
            "tool_delta": self.handle_content,
            "usage": self.handle_usage,
            "request_end": self.handle_request_end,
            "tool_start": self.handle_tool_start,
            "tool_end": self.handle_tool_end,
            "failure": self.handle_failure,
            "notice": self.handle_notice,
        }
        handler: Callable[[str, dict[str, object]], None] | None = (
            handlers.get(kind)
        )
        if handler is not None:
            handler(kind, data)

    def handle_notice(self, kind: str, data: dict[str, object]) -> None:
        self.ui.append("tool", str(data["message"]) + "\n")

    def handle_batch_started(self, kind: str, data: dict[str, object]) -> None:
        self.ui.batch = {
            "job_id": data["job_id"],
            "done": 0,
            "total": 0,
            "checker": "",
        }
        self.ui.records = []
        self.ui.state = "Building review queue"

    def handle_batch_queue(self, kind: str, data: dict[str, object]) -> None:
        self.ui.batch.update(total=data["total"], checker=data["checker"])

    def handle_batch_progress(
        self, kind: str, data: dict[str, object],
    ) -> None:
        self.ui.batch = dict(data)
        self.ui.records = []
        self.ui.splitter = ThinkingText(self.ui.append)
        self.ui.state = "Loading warning / restoring checker context"

    def handle_batch_finished(
        self, kind: str, data: dict[str, object],
    ) -> None:
        self.ui.records, self.ui.batch = ([], None)
        self.ui.state = "Batch complete"

    def handle_batch_summary(self, kind: str, data: dict[str, object]) -> None:
        self.ui.append("content", data["text"])

    def handle_batch_format_repair(
        self, kind: str, data: dict[str, object],
    ) -> None:
        self.ui.append(
            "tool", "Formatting verdict (one attempt, no tools or writes)\n"
        )
        for issue in data.get("issues", []):
            self.ui.append("tool", "  " + issue + "\n")
        self.ui.state = "Formatting verdict"

    def handle_history_link(self, kind: str, data: dict[str, object]) -> None:
        scope: dict[str, str | int] = data["scope"]
        subset: str = " · " + data["subset"] if data.get("subset") else ""
        self.ui.append(
            "tool",
            (
                f"Resolved history link: project {scope['project_id']} · "
                f"{scope['stream_id']} · {scope['snapshot_id']}{subset}\n"
            ),
        )

    def handle_request_start(self, kind: str, data: dict[str, object]) -> None:
        now: float = time.monotonic()
        self.ui.splitter.flush()
        self.ui.splitter = ThinkingText(self.ui.append)
        self.ui.step, self.ui.context = (data["step"], data["context_bytes"])
        self.ui.request_started, self.ui.first_token = (now, None)
        self.ui.request_finished = None
        self.ui.output_chars, self.ui.usage, self.ui.accounted = (0, {}, False)
        self.ui.usage_chars = 0
        self.ui.state = "Prefill / waiting for first token"

    def handle_content(self, kind: str, data: dict[str, object]) -> None:
        now: float = time.monotonic()
        if data["text"]:
            self.ui.first_token = self.ui.first_token or now
        self.ui.output_chars += len(data["text"])
        if kind == "content":
            self.ui.splitter.feed(data["text"])
            self.ui.state = (
                "Generating answer"
                if not self.ui.splitter.thinking
                else "Thinking"
            )
        elif kind == "reasoning":
            self.ui.append("reasoning", data["text"])
            self.ui.state = "Thinking"
        else:
            self.ui.state = "Receiving tool call"

    def handle_usage(self, kind: str, data: dict[str, object]) -> None:
        if isinstance(data.get("usage"), dict):
            self.ui.usage.update(data["usage"])
            self.ui.usage_chars = self.ui.output_chars

    def handle_request_end(self, kind: str, data: dict[str, object]) -> None:
        now: float = time.monotonic()
        self.ui.splitter.flush()
        self.ui.usage.update(data.get("usage") or {})
        self.ui.usage_chars = self.ui.output_chars
        self.ui.account_usage()
        self.ui.request_finished = now
        self.ui.state = "Response received"

    def handle_tool_start(self, kind: str, data: dict[str, object]) -> None:
        label: str = safe_text(
            data["name"] + " · " + str(data.get("action") or "list actions")
        )
        self.ui.events.append(label)
        self.ui.append("tool", label + "\n")
        self.ui.state = "Running " + safe_text(data["name"])

    def handle_tool_end(self, kind: str, data: dict[str, object]) -> None:
        label = safe_text(data["name"])
        status: str = "error" if data.get("error") else "done"
        self.ui.events.append(
            f"{status} · {label} · {data['seconds']:.2f}s · {data['bytes']} B"
        )
        self.ui.append(
            "tool",
            f"  {status} · {data['seconds']:.2f}s · {data['bytes']} B\n",
        )

    def handle_failure(self, kind: str, data: dict[str, object]) -> None:
        now: float = time.monotonic()
        self.ui.splitter.flush()
        self.ui.error = safe_text(data["message"])
        self.ui.request_finished = now
        self.ui.account_usage()
        self.ui.state = "Stopped — partial output"
