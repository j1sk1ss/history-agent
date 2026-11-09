"""Coordinate resumable jobs; each warning gets an isolated investigation."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import Client

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.history.comparison import scopes
from history_agent.inference.model import ChatError, compact
from history_agent.review.classification import STATUSES
from history_agent.review.client import ItemClient
from history_agent.review.investigation import Investigation
from history_agent.review.jobs import FINISHED_ITEM_STATES
from history_agent.review.journal import Journal
from history_agent.review.processor import ItemProcessor
from history_agent.review.queue import ReviewQueue
from history_agent.review.schemas import BATCH_SCHEMA
from history_agent.runtime.agent import Harness


class BatchRunner:
    def __init__(
        self,
        history: HistoryClient,
        client: Client,
        reviewer_factory: Callable[[ItemClient], Harness],
        root: str | Path,
        model_identity: dict[str, str],
        *,
        allow_write: bool = False,
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
        self,
        action: str,
        args: dict[str, object],
    ) -> dict[str, object]:
        return await asyncio.to_thread(self.history.execute, action, args)

    async def run(
        self,
        args: dict[str, object],
        *,
        write: bool = False,
    ) -> dict[str, object]:
        self.last_job_id = None
        self.validate_request(args, write)
        job: Journal = Journal(self.root, args.get("job_id"))
        self.last_job_id = job.id
        try:
            with job.lock():
                if not args.get("job_id"):
                    self.initialize_job(job, args, write)
                self.validate_checkpoint(job, write)
                await self.process_queue(job)
                self.last_summary = job.summary()
                self.on_event("batch_finished", summary=self.last_summary)
                return self.last_summary
        except (ChatError, HistoryError, OSError, sqlite3.Error) as error:
            raise ChatError(
                f"Batch stopped; job_id={job.id}. {error}"
            ) from None
        finally:
            self.last_summary = job.summary()
            job.db.close()

    def validate_request(self, args: dict[str, object], write: bool) -> None:
        if not Draft202012Validator(BATCH_SCHEMA).is_valid(args):
            raise ChatError(
                "Invalid review_batch arguments; discover its schema first"
            )
        if write and not self.allow_write:
            raise ChatError("Batch writes require --allow-write")
        if args.get("overwrite") and not self.allow_overwrite:
            raise ChatError(
                "Replacing existing batch reviews requires --overwrite-reviews"
            )

    def initialize_job(
        self,
        job: Journal,
        args: dict[str, object],
        write: bool,
    ) -> None:
        scope: dict[str, str | int] = {
            key: args[key]
            for key in ("project_id", "stream_id", "snapshot_id")
        }
        if args.get("base_snapshot_id"):
            current: dict[str, str | int]
            base: dict[str, str | int]
            current, base = scopes(args)
            subset: str = args.get("subset", "appeared")
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
            "instructions": args.get("instructions", ""),
            "overwrite": args.get("overwrite", False),
            "checker_name": args.get("checker_name"),
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
            with job.db:
                job.db.execute(
                    "UPDATE items SET state='proposed' WHERE state='reviewed'"
                )
                job.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                    ("write", compact(True)),
                )
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
        placeholders: str = ",".join("?" for _ in FINISHED_ITEM_STATES)
        rows: list[sqlite3.Row] = job.db.execute(
            f"SELECT * FROM items WHERE state NOT IN ({placeholders}) "
            "ORDER BY checker COLLATE NOCASE,ordinal",
            FINISHED_ITEM_STATES,
        ).fetchall()
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
        self,
        job: Journal,
        row: sqlite3.Row,
        baseline: dict[str, object],
    ) -> dict[str, object]:
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
