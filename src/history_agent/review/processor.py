"""Advance one queued warning through investigation and verified saving."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from sqlite3 import Row

from history_agent.history.client import HistoryClient
from history_agent.inference.model import ChatError
from history_agent.review import patterns
from history_agent.review.classification import STATUSES
from history_agent.review.comments import review_comment
from history_agent.review.jobs import FINISHED_ITEM_STATES
from history_agent.review.journal import Journal


class ItemProcessor:
    def __init__(
        self,
        history: HistoryClient,
        investigate: Callable[
            [Journal, Row, dict[str, object]], Awaitable[dict[str, object]]
        ],
        on_event: Callable[..., None],
        *,
        reuse_patterns: bool,
    ) -> None:
        self.history: HistoryClient = history
        self.investigate: Callable[
            [Journal, Row, dict[str, object]], Awaitable[dict[str, object]]
        ] = investigate
        self.on_event: Callable[..., None] = on_event
        self.reuse_patterns: bool = reuse_patterns

    async def current(
        self,
        scope: dict[str, str | int],
        warning_id: str,
    ) -> dict[str, object]:
        result: dict[str, object] = await asyncio.to_thread(
            self.history.execute,
            "get_warning",
            {**scope, "snapshot_id": "HEAD", "warning_id": warning_id},
        )
        warning: dict[str, object] = result.get("warning", {})
        if warning.get("warning_id") != warning_id:
            raise ChatError("Current warning identity mismatch")
        attributes: object = warning.get("attributes")
        if (
            not isinstance(attributes, dict)
            or "classification_id" not in attributes
        ):
            raise ChatError("Current warning has no usable review attributes")
        return copy.deepcopy(attributes)

    async def process(
        self, job: Journal, row: Row, ids: dict[str, int]
    ) -> None:
        self.progress(job, row)
        current: dict[str, object] = await self.current(
            job.get("scope"), row["warning_id"]
        )
        if row["state"] in ("saving", "uncertain"):
            self.verify_previous_write(job, row, current, ids)
            return
        proposal: (
            tuple[dict[str, object], dict[str, object]] | None
        ) = await self.propose(job, row, current, ids)
        if proposal is None:
            return
        verdict: dict[str, object]
        baseline: dict[str, object]
        verdict, baseline = proposal
        if job.get("write") and not await self.save(
            job, row, verdict, baseline, ids
        ):
            return
        self.finish(job, row, verdict, saved=bool(job.get("write")))

    def progress(self, job: Journal, row: Row) -> None:
        summary: dict[str, object] = job.summary()
        completed: int = sum(
            summary["states"].get(state, 0) for state in FINISHED_ITEM_STATES
        )
        self.on_event(
            "batch_progress",
            job_id=job.id,
            done=completed,
            total=summary["total"],
            checker=row["checker"],
            warning_id=row["warning_id"],
        )

    def verify_previous_write(
        self,
        job: Journal,
        row: Row,
        current: dict[str, object],
        ids: dict[str, int],
    ) -> None:
        if matches(current, json.loads(row["verdict"]), ids):
            job.update(row["warning_id"], "saved")
            return
        raise ChatError(
            "A previous write has an uncertain outcome; inspect review "
            "history. It will not be retried."
        )

    async def propose(
        self,
        job: Journal,
        row: Row,
        current: dict[str, object],
        ids: dict[str, int],
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        if row["state"] == "proposed":
            verdict: dict[str, object] = json.loads(row["verdict"])
            baseline: dict[str, object] = json.loads(row["baseline"])
            verdict["comment"] = review_comment(
                verdict["comment"], verdict["evidence"]
            )
        else:
            reviewed: bool = current["classification_id"] != ids[
                STATUSES["UD"]
            ] or bool(current.get("comment"))
            if not job.get("overwrite") and reviewed:
                job.update(row["warning_id"], "skipped")
                return None
            baseline = current
            job.update(row["warning_id"], "analyzing", baseline=baseline)
            verdict = await self.investigate(job, row, baseline)
        job.update(
            row["warning_id"], "proposed", verdict=verdict, baseline=baseline
        )
        if row["state"] != "proposed":
            job.set("pending_response", None)
        return verdict, baseline

    async def save(
        self,
        job: Journal,
        row: Row,
        verdict: dict[str, object],
        baseline: dict[str, object],
        ids: dict[str, int],
    ) -> bool:
        scope: dict[str, str | int] = job.get("scope")
        warning_id: str = row["warning_id"]
        if await self.current(scope, warning_id) != baseline:
            job.update(warning_id, "conflict")
            return False
        job.update(warning_id, "saving")
        try:
            await asyncio.to_thread(
                self.history.execute,
                "agent_set_review",
                {
                    "project_id": scope["project_id"],
                    "stream_id": scope["stream_id"],
                    "warning_id": warning_id,
                    "classification": verdict["classification"],
                    "comment": verdict["comment"],
                },
                write=True,
            )
            if not matches(
                await self.current(scope, warning_id), verdict, ids
            ):
                raise ChatError("Saved review could not be verified")
        except BaseException:
            job.update(warning_id, "uncertain")
            raise
        return True

    def finish(
        self,
        job: Journal,
        row: Row,
        verdict: dict[str, object],
        *,
        saved: bool,
    ) -> None:
        job.update(row["warning_id"], "saved" if saved else "reviewed")
        if self.reuse_patterns:
            patterns.learn(job.db, row["checker"], verdict)
        self.on_event(
            "batch_verdict",
            warning_id=row["warning_id"],
            classification=verdict["classification"],
            comment=verdict["comment"],
            saved=saved,
        )


def matches(
    current: dict[str, object],
    verdict: dict[str, object],
    ids: dict[str, int],
) -> bool:
    return (
        current.get("classification_id")
        == ids[STATUSES[verdict["classification"]]]
        and current.get("comment") == verdict["comment"]
    )
