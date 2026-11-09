"""Coordinate resumable jobs; each warning gets an isolated investigation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from mcp import Client
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.history.comparison import scopes
from history_agent.inference.model import ChatError
from history_agent.review.classification import STATUSES
from history_agent.review.client import ItemClient
from history_agent.review.investigation import Investigation
from history_agent.review.jobs import FINISHED_ITEM_STATES
from history_agent.review.journal import Journal
from history_agent.review.models import ReviewVerdict
from history_agent.review.processor import ItemProcessor
from history_agent.review.queue import ReviewQueue
from history_agent.review.requests import (
    BatchRequest,
    ResolvedBatchCommand,
    ResumeRequest,
)
from history_agent.review.storage.models import ReviewItem
from history_agent.runtime.agent import Harness


class BatchRunner:
    def __init__(
        self, history: HistoryClient, client: Client,
        reviewer_factory: Callable[[ItemClient], Harness], root: str | Path,
        model_identity: dict[str, str], *, allow_write: bool = False,
        allow_overwrite: bool = False,
        on_event: Callable[..., None] | None = None,
        reuse_patterns: bool = True,
    ) -> None:
        self.history: HistoryClient = history
        self.client: Client = client
        self.reviewer_factory: Callable[[ItemClient], Harness] = (
            reviewer_factory
        )
        self.root: Path = Path(root)
        self.model_identity: dict[str, str] = model_identity
        self.allow_write: bool = allow_write
        self.allow_overwrite: bool = allow_overwrite
        self.on_event: Callable[..., None] = on_event or (
            lambda *args, **kwargs: None
        )
        self.reuse_patterns: bool = reuse_patterns
        self.last_summary: dict[str, object] | None = None
        self.last_job_id: str | None = None

    async def read(
        self, action: str, args: dict[str, object],
    ) -> dict[str, object]:
        return await asyncio.to_thread(self.history.execute, action, args)

    async def run(
        self, args: dict[str, object], *, write: bool = False,
    ) -> dict[str, object]:
        self.last_job_id = None
        request: BatchRequest | ResumeRequest = self.validate_request(
            args, write
        )
        job_id: str | None = (
            request.job_id if isinstance(request, ResumeRequest) else None
        )
        job: Journal = Journal(self.root, job_id)
        self.last_job_id = job.id
        try:
            with job.lock():
                if isinstance(request, BatchRequest):
                    self.initialize_job(job, request, write)
                self.validate_checkpoint(job, write)
                await self.process_queue(job)
                self.last_summary = job.summary()
                self.on_event("batch_finished", summary=self.last_summary)
                return self.last_summary
        except (ChatError, HistoryError, OSError, SQLAlchemyError) as error:
            raise ChatError(
                f"Batch stopped; job_id={job.id}. {error}"
            ) from None
        finally:
            self.last_summary = job.summary()
            job.close()

    def validate_request(
        self, args: dict[str, object], write: bool,
    ) -> BatchRequest | ResumeRequest:
        try:
            request: BatchRequest | ResumeRequest = (
                ResolvedBatchCommand.model_validate(args).root
            )
        except ValidationError:
            raise ChatError(
                "Invalid review_batch arguments; discover its schema first"
            ) from None
        if write and not self.allow_write:
            raise ChatError("Batch writes require --allow-write")
        if (
            isinstance(request, BatchRequest)
            and request.overwrite
            and not self.allow_overwrite
        ):
            raise ChatError(
                "Replacing existing batch reviews requires --overwrite-reviews"
            )
        return request

    def initialize_job(
        self, job: Journal, request: BatchRequest, write: bool,
    ) -> None:
        scope: dict[str, str | int] = {
            "project_id": request.project_id,
            "stream_id": request.stream_id,
            "snapshot_id": request.snapshot_id,
        }
        if request.base_snapshot_id is not None:
            current: dict[str, str | int]
            base: dict[str, str | int]
            current, base = scopes(request.model_dump(exclude_none=True))
            subset: str = request.subset
            job.set(
                "comparison",
                {
                    "current": current,
                    "base": base,
                    "subset": subset,
                },
            )
            scope = base if subset == "disappeared" else current
        values: dict[str, object] = {
            "scope": scope,
            "format": 1,
            "endpoint": self.history.endpoint,
            "model": self.model_identity,
            "write": write,
            "instructions": request.instructions,
            "overwrite": request.overwrite,
            "checker_name": request.checker_name,
        }
        for key, value in values.items():
            job.set(key, value)

    def validate_checkpoint(self, job: Journal, write: bool) -> None:
        if job.get("format") != 1:
            raise ChatError("Unsupported batch checkpoint format")
        if (
            job.get("endpoint") != self.history.endpoint
            or job.get("model") != self.model_identity
        ):
            raise ChatError(
                "Checkpoint belongs to a different history endpoint/model"
            )
        if job.get("write") != write:
            if not write:
                raise ChatError(
                    "This job saves reviews; enable live saving to resume it"
                )
            job.enable_writes()
        if job.get("overwrite") and not self.allow_overwrite:
            raise ChatError("This checkpoint requires --overwrite-reviews")

    async def process_queue(self, job: Journal) -> None:
        self.on_event("batch_started", job_id=job.id)
        if not job.get("queue_complete"):
            await self.enumerate(job)
        statuses: dict[str, object] = await self.read(
            "get_value_filter", {**job.get("scope"), "field": "status"}
        )
        ids: dict[str, int] = {
            value["name"]: value["id"]
            for value in statuses.get("classification", [])
        }
        if not set(STATUSES.values()) <= set(ids):
            raise ChatError("History classification catalog is incomplete")
        rows: list[ReviewItem] = job.items(
            excluded_states=FINISHED_ITEM_STATES
        )
        processor: ItemProcessor = ItemProcessor(
            self.history,
            self.investigate,
            self.on_event,
            reuse_patterns=self.reuse_patterns,
        )
        for row in rows:
            await processor.process(job, row, ids)

    async def enumerate(self, job: Journal) -> None:
        await ReviewQueue(self.read, self.on_event).collect(job)

    async def investigate(
        self, job: Journal, row: ReviewItem, baseline: dict[str, object],
    ) -> ReviewVerdict:
        investigation: Investigation = Investigation(
            self.history,
            self.client,
            job,
            row,
            baseline,
            self.reviewer_factory,
            self.on_event,
            reuse_patterns=self.reuse_patterns,
        )
        return await investigation.run()
