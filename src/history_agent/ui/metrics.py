from __future__ import annotations

import math
import time


class SessionMetrics:
    def __init__(self) -> None:
        self.session_in: int = 0
        self.session_out: int = 0
        self.session_cached: int = 0
        self.session_estimated_in: bool = False
        self.session_estimated_out: bool = False
        self.batch: dict[str, object] | None = None
        self.start()

    def start(self) -> None:
        self.started: float = time.monotonic()
        self.request_started: float = self.started
        self.request_finished: float | None = None
        self.first_token: float | None = None
        self.context: int = 0
        self.output_chars: int = 0
        self.usage_chars: int = 0
        self.usage: dict[str, object] = {}
        self.accounted: bool = True
        self.error: str | None = None

    def event(self, kind: str, **data: object) -> None:
        now: float = time.monotonic()
        if kind == "batch_started":
            self.batch = {
                "job_id": data["job_id"],
                "done": 0,
                "total": 0,
                "checker": "",
            }
        elif kind == "batch_queue" and self.batch is not None:
            self.batch.update(total=data["total"], checker=data["checker"])
        elif kind == "batch_progress":
            self.batch = dict(data)
        elif kind == "batch_finished":
            self.batch = None
        elif kind == "request_start":
            self.context = data["context_bytes"]
            self.request_started = now
            self.request_finished = None
            self.first_token = None
            self.output_chars = 0
            self.usage_chars = 0
            self.usage = {}
            self.accounted = False
        elif kind in ("content", "reasoning", "tool_delta"):
            chunk: str = data.get("text", "")
            if chunk:
                self.first_token = self.first_token or now
                self.output_chars += len(chunk)
        elif kind == "usage" and isinstance(data.get("usage"), dict):
            self.usage.update(data["usage"])
            self.usage_chars = self.output_chars
        elif kind == "request_end":
            self.usage.update(data.get("usage") or {})
            self.usage_chars = self.output_chars
            self.request_finished = now
            self.account_usage()
        elif kind == "failure":
            self.error = data.get("message", "")
            self.request_finished = now
            self.account_usage()

    def counts(self) -> tuple[int | None, int | None, int | None]:
        def valid(value: object) -> int | None:
            return (
                value
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                else None
            )

        prompt: int | None = valid(self.usage.get("prompt_tokens"))
        output: int | None = valid(self.usage.get("completion_tokens"))
        if self.output_chars > self.usage_chars:
            output = None
        details: dict[str, object] = (
            self.usage.get("prompt_tokens_details") or {}
        )
        cached: int | None = (
            valid(details.get("cached_tokens"))
            if isinstance(details, dict)
            else None
        )
        return prompt, output, cached

    def account_usage(self) -> None:
        if self.accounted:
            return
        prompt: int | None
        output: int | None
        cached: int | None
        prompt, output, cached = self.counts()
        self.session_in += (
            prompt if prompt is not None else math.ceil(self.context / 4)
        )
        self.session_out += (
            output if output is not None else math.ceil(self.output_chars / 4)
        )
        self.session_cached += cached or 0
        self.session_estimated_in |= prompt is None
        self.session_estimated_out |= output is None
        self.accounted = True

    def summary(
        self, state: str, *, context_limit: int | None = None,
        detailed: bool = False,
    ) -> str:
        prompt: int | None
        output: int | None
        cached: int | None
        prompt, output, cached = self.counts()
        received: int = (
            prompt if prompt is not None else math.ceil(self.context / 4)
        )
        emitted: int = (
            output if output is not None else math.ceil(self.output_chars / 4)
        )
        generation: float = (
            (self.request_finished or time.monotonic()) - self.first_token
            if self.first_token
            else 0
        )
        speed: float = (emitted / generation) if generation > 0.05 else 0
        input_count: str = (
            str(received) if prompt is not None else f"~{received}"
        )
        output_count: str = (
            str(emitted) if output is not None else f"~{emitted}"
        )
        speed_label: str = (
            f"{speed:.1f}" if output is not None else f"~{speed:.1f}"
        )
        context: str = f"{self.context / 1024:.1f}"
        if context_limit is not None:
            context += f"/{context_limit / 1024:.1f}"
        label: str = f"{state} | CTX {context} KiB | {speed_label} tok/s"
        if self.batch:
            label += (
                f" | {self.batch.get('done', 0)}/{self.batch.get('total', 0)}"
            )
        label += (
            f"\nIN {input_count}  OUT {output_count}"
            f"  CACHE {cached if cached is not None else '-'}"
        )
        if detailed:
            session_in: str = (
                f"{'~' if self.session_estimated_in else ''}{self.session_in}"
            )
            session_out: str = (
                f"{'~' if self.session_estimated_out else ''}"
                f"{self.session_out}"
            )
            label += (
                f"\nSESSION IN {session_in}  OUT {session_out}"
                f"  CACHE {self.session_cached}"
            )
        return label
