"""Checkpoint, repair and validate a single investigation response."""

from __future__ import annotations

import copy
from collections.abc import Callable
from sqlite3 import Row

from jsonschema import Draft202012Validator

from history_agent.inference.model import ChatError, compact
from history_agent.review.classification import EVIDENCE_REQUIRED, STATUSES
from history_agent.review.client import ItemClient
from history_agent.review.comments import review_comment
from history_agent.review.journal import Journal
from history_agent.review.patterns import PATTERN_SCHEMA
from history_agent.review.prompts import FORMAT_REPAIR
from history_agent.review.schemas import RESULT_SCHEMA
from history_agent.review.verdict import (
    VerdictFormatError,
    extract_verdict,
    normalize_verdict,
    schema_issues,
)
from history_agent.runtime.agent import Harness


class VerdictResponse:
    def __init__(
        self, job: Journal, row: Row, baseline: dict[str, object],
        item_client: ItemClient,
        reviewer_factory: Callable[[ItemClient], Harness],
        on_event: Callable[..., None],
    ) -> None:
        self.job: Journal = job
        self.row: Row = row
        self.baseline: dict[str, object] = baseline
        self.client: ItemClient = item_client
        self.reviewer_factory: Callable[[ItemClient], Harness] = (
            reviewer_factory
        )
        self.on_event: Callable[..., None] = on_event
        self.scope: dict[str, str | int] = job.get("scope")
        self.raw: str = ""
        self.original_raw: str = ""
        self.draft: dict[str, object] | None = None

    async def resolve(self, raw: str, original_raw: str) -> dict[str, object]:
        self.raw = raw
        self.original_raw = original_raw
        self.retain()
        try:
            verdict: dict[str, object] = self.parse()
        except VerdictFormatError as error:
            verdict = await self.repair([str(error)])
        self.verify_evidence(verdict)
        verdict["comment"] = review_comment(
            verdict["comment"], verdict["evidence"], self.client.snapshots
        )
        if not verdict["comment"]:
            raise ChatError(
                "Review explanation is empty after removing redundant metadata"
            )
        return verdict

    def retain(
        self, issues: list[str] | None = None, *, retry: bool = False,
    ) -> None:
        self.job.set(
            "pending_response",
            {
                "warning_id": self.row["warning_id"],
                "baseline": self.baseline,
                "raw": self.raw,
                "original_raw": self.original_raw,
                "evidence": sorted(self.client.record),
                "validation_errors": issues or [],
                "retry_investigation": retry,
            },
        )

    def parse(self) -> dict[str, object]:
        try:
            verdict: dict[str, object] = normalize_verdict(
                extract_verdict(self.raw),
                self.client.record,
                self.scope,
                self.row["warning_id"],
                self.row["checker"],
                STATUSES,
            )
            if "pattern" in verdict and not Draft202012Validator(
                PATTERN_SCHEMA
            ).is_valid(verdict["pattern"]):
                verdict.pop("pattern")
            self.draft = verdict
        except VerdictFormatError:
            raise
        except ChatError as error:
            self.retain([str(error)], retry=True)
            raise
        issues: list[str] = schema_issues(
            verdict, Draft202012Validator(RESULT_SCHEMA)
        )
        if issues:
            raise VerdictFormatError("; ".join(issues))
        return verdict

    async def repair(self, issues: list[str]) -> dict[str, object]:
        previous: dict[str, object] | None = copy.deepcopy(self.draft)
        self.retain(issues)
        self.on_event(
            "batch_format_repair",
            warning_id=self.row["warning_id"],
            issues=issues,
        )
        formatter: Harness = self.reviewer_factory(self.client)
        formatter.tools, formatter.allowed = [], set()
        formatter.messages = [{"role": "system", "content": FORMAT_REPAIR}]
        self.raw = await formatter.ask(
            compact(
                {
                    "schema": RESULT_SCHEMA,
                    "validation_errors": issues,
                    "draft": self.raw,
                }
            )
        )
        self.retain()
        try:
            verdict: dict[str, object] = self.parse()
        except VerdictFormatError as error:
            self.retain([str(error)])
            raise ChatError(
                "Batch verdict still fails validation: "
                + str(error)
                + ". Response and evidence are checkpointed; resume retries "
                "formatting only. No review was saved"
            ) from None
        if previous is not None:
            self.verify_repair(previous, verdict)
        return verdict

    def reject(self, issue: str, message: str) -> None:
        self.retain([issue], retry=True)
        raise ChatError(message)

    def verify_repair(
        self, previous: dict[str, object], verdict: dict[str, object],
    ) -> None:
        classification: object = previous.get("classification")
        if (
            isinstance(classification, str)
            and classification in STATUSES
            and verdict["classification"] != classification
        ):
            self.reject(
                "Format repair changed the classification",
                "Format repair changed the classification; "
                "no review was saved",
            )
        addresses: object = previous.get("evidence")
        if not isinstance(addresses, list) or not all(
            valid_address(address) for address in addresses
        ):
            return
        cited: set[tuple[str, int, str]] = {
            (a["file"], a["line"], a["snapshot_id"]) for a in addresses
        }
        repaired: set[tuple[str, int, str]] = {
            (a["file"], a["line"], a["snapshot_id"])
            for a in verdict["evidence"]
        }
        if cited != repaired:
            self.reject(
                "Format repair changed the cited evidence",
                "Format repair changed the cited evidence; "
                "no review was saved",
            )

    def verify_evidence(self, verdict: dict[str, object]) -> None:
        if (
            verdict["classification"] in EVIDENCE_REQUIRED
            and not verdict["evidence"]
        ):
            self.reject(
                "TP/FP/WF verdict has no source evidence",
                "TP/FP/WF verdict has no source evidence; no review was saved",
            )
        for address in verdict["evidence"]:
            if (
                address["file"],
                address["line"],
                address["snapshot_id"],
            ) not in (self.client.record):
                self.reject(
                    "Verdict cited source that was not actually read: "
                    + compact(address),
                    "Verdict cited source that was not actually read; "
                    "no review was saved",
                )


def valid_address(address: object) -> bool:
    return (
        isinstance(address, dict)
        and isinstance(address.get("file"), str)
        and isinstance(address.get("line"), int)
        and not isinstance(address["line"], bool)
        and isinstance(address.get("snapshot_id"), str)
    )
