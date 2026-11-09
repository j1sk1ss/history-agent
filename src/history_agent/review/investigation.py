"""Prepare and run an isolated investigation for one warning."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from mcp import Client

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.inference.model import ChatError, compact
from history_agent.review import patterns, prompts
from history_agent.review.client import ItemClient
from history_agent.review.journal import Journal
from history_agent.review.models import ReviewVerdict
from history_agent.review.response import VerdictResponse
from history_agent.review.storage.models import ReviewItem
from history_agent.runtime.agent import Harness
from history_agent.runtime.messages import tool_exchange


class Investigation:
    def __init__(
        self, history: HistoryClient, client: Client, job: Journal,
        row: ReviewItem, baseline: dict[str, object],
        reviewer_factory: Callable[[ItemClient], Harness],
        on_event: Callable[..., None], *, reuse_patterns: bool,
    ) -> None:
        self.history: HistoryClient = history
        self.job: Journal = job
        self.row: ReviewItem = row
        self.baseline: dict[str, object] = baseline
        self.reviewer_factory: Callable[[ItemClient], Harness] = (
            reviewer_factory
        )
        self.on_event: Callable[..., None] = on_event
        self.reuse_patterns: bool = reuse_patterns
        self.scope: dict[str, str | int] = job.get("scope")
        self.warning: dict[str, object] = row.warning
        self.client: ItemClient = ItemClient(
            client, {**self.scope, "checker_name": row.checker}, set()
        )

    async def read(
        self, action: str, arguments: dict[str, object],
    ) -> dict[str, object]:
        return await asyncio.to_thread(self.history.execute, action, arguments)

    async def run(self) -> ReviewVerdict:
        checker: dict[str, object] = await self.checker_documentation()
        self.allow_comparison_snapshots()
        reviewer: Harness = self.reviewer_factory(self.client)
        reviewer.reset()
        reviewer.set_result_schema(ReviewVerdict.model_json_schema())
        reviewer.messages[0]["content"] += " " + prompts.BATCH_INSTRUCTION
        reviewer.messages.append(
            {
                "role": "user",
                "content": prompts.PREFETCH_NOTICE,
            }
        )
        await self.prefetch(reviewer, checker)
        pending: dict[str, object] | None = self.pending_response()
        prompt: str = self.build_prompt(pending)
        raw: str = await self.obtain_response(reviewer, prompt, pending)
        original_raw: str = (
            pending.get("original_raw", pending["raw"])
            if pending and not pending.get("retry_investigation")
            else raw
        )
        response: VerdictResponse = VerdictResponse(
            self.job,
            self.row,
            self.baseline,
            self.client,
            self.reviewer_factory,
            self.on_event,
        )
        return await response.resolve(raw, original_raw)

    async def checker_documentation(self) -> dict[str, object]:
        cached: dict[str, object] | None = self.job.cached_checker(
            self.row.checker
        )
        if cached is not None:
            return cached
        arguments: dict[str, str] = {"checker_name": self.row.checker}
        try:
            result: dict[str, object] = await self.read(
                "agent_checker_info", arguments
            )
        except HistoryError as error:
            if not str(error).startswith("Unknown checker:"):
                raise
            result = {**arguments, "catalog_error": str(error)}
            try:
                result["documentation"] = await self.read(
                    "agent_checker_documentation", arguments
                )
            except HistoryError as doc_error:
                result["documentation"] = {
                    "status": "unavailable",
                    "error": str(doc_error),
                }
        self.job.cache_checker(self.row.checker, result)
        return result

    def allow_comparison_snapshots(self) -> None:
        comparison: dict[str, object] | None = self.job.get("comparison")
        if not comparison:
            return
        for side in ("current", "base"):
            other: dict[str, str | int] = comparison[side]
            if all(
                other[key] == self.scope[key]
                for key in ("project_id", "stream_id")
            ):
                self.client.snapshots.add(other["snapshot_id"])

    async def prefetch(
        self, reviewer: Harness, checker: dict[str, object],
    ) -> None:
        detail_args: dict[str, object] = {
            **self.scope,
            "warning_id": self.row.warning_id,
        }
        detail: dict[str, object] = await self.read("get_warning", detail_args)
        if detail.get("warning", {}).get("warning_id") != self.row.warning_id:
            raise ChatError("Snapshot warning identity mismatch")
        packets: list[tuple[str, dict[str, object], dict[str, object]]] = [
            (
                "agent_checker_info",
                {"checker_name": self.row.checker},
                checker,
            ),
            (
                "get_warning",
                detail_args,
                {
                    **detail,
                    "host_current_review": self.baseline,
                },
            ),
        ]
        review_args: dict[str, object] = {
            key: detail_args[key]
            for key in ("project_id", "stream_id", "warning_id")
        }
        packets.append(
            (
                "get_all_reviews",
                review_args,
                await self.read("get_all_reviews", review_args),
            )
        )
        trace: dict[str, object] = await self.read(
            "get_warning_trace_and_role", detail_args
        )
        packets.append(("get_warning_trace_and_role", detail_args, trace))
        self.client.observe("get_warning_trace_and_role", detail_args, trace)
        source: (
            tuple[str, dict[str, object], dict[str, object]] | None
        ) = await self.prefetch_source()
        if source:
            packets.append(source)
        for index, (action, arguments, data) in enumerate(packets):
            self.attach_packet(reviewer, index, action, arguments, data)

    async def prefetch_source(
        self,
    ) -> tuple[str, dict[str, object], dict[str, object]] | None:
        location: dict[str, object] = self.warning.get("location", {})
        if not location.get("file_path") or not isinstance(
            location.get("line"), int
        ):
            return None
        arguments: dict[str, object] = {
            "project_id": self.scope["project_id"],
            "snapshot_id": location.get("snapshot_override")
            or self.scope["snapshot_id"],
            "file": location["file_path"],
            "line": location["line"],
            "before": 5,
            "after": 5,
        }
        self.client.snapshots.add(arguments["snapshot_id"])
        try:
            data: dict[str, object] = await self.read(
                "agent_source", arguments
            )
        except HistoryError as error:
            if error.auth_error or str(error).startswith(
                "History request failed"
            ):
                raise
            data = {"error": str(error)}
        return "agent_source", arguments, data

    def attach_packet(
        self, reviewer: Harness, index: int, action: str,
        arguments: dict[str, object], data: dict[str, object],
    ) -> None:
        text: str = compact(data)
        if len(text.encode()) > reviewer.max_tool_result_bytes:
            text = compact({"error": prompts.PREFETCH_TOO_LARGE})
        else:
            self.client.observe(action, arguments, data)
        reviewer.messages.extend(
            tool_exchange("batch-read-" + str(index), action, arguments, text)
        )
        self.on_event(
            "tool_prefetch",
            action=action,
            arguments=arguments,
            response=text,
        )

    def pending_response(self) -> dict[str, object] | None:
        pending: object = self.job.get("pending_response")
        if (
            isinstance(pending, dict)
            and pending.get("warning_id") == self.row.warning_id
            and pending.get("baseline") == self.baseline
        ):
            return pending
        return None

    def build_prompt(self, pending: dict[str, object] | None) -> str:
        prompt: str = prompts.INVESTIGATION.format(
            scope=compact(self.scope),
            instructions=self.job.get("instructions", ""),
        )
        comparison: dict[str, object] | None = self.job.get("comparison")
        if comparison:
            prompt += prompts.COMPARISON.format(comparison=compact(comparison))
        hints: list[dict[str, str | list[str]]] = (
            patterns.recall(self.job, self.row.checker)
            if self.reuse_patterns
            else []
        )
        if hints:
            prompt += prompts.PATTERNS.format(patterns=compact(hints))
            self.on_event(
                "batch_patterns",
                count=len(hints),
                checker=self.row.checker,
                patterns=hints,
            )
        if pending and pending.get("retry_investigation"):
            prompt += prompts.RETRY.format(
                issues=compact(pending.get("validation_errors", []))
            )
        return prompt

    async def obtain_response(
        self, reviewer: Harness, prompt: str,
        pending: dict[str, object] | None,
    ) -> str:
        if not pending or pending.get("retry_investigation"):
            return await reviewer.ask(prompt)
        self.client.record.update(
            tuple(address) for address in pending["evidence"]
        )
        reviewer.messages.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": pending["raw"]},
            ]
        )
        return pending["raw"]
