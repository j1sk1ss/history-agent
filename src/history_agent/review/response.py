"""Checkpoint, repair and validate a single investigation response."""

from __future__ import annotations

import copy
from collections.abc import Callable

from pydantic import ValidationError

from history_agent.inference.model import ChatError, compact
from history_agent.prompts import Prompts
from history_agent.review.classification import EVIDENCE_REQUIRED, STATUSES
from history_agent.review.client import ItemClient
from history_agent.review.comments import review_comment
from history_agent.review.journal import Journal
from history_agent.review.models import ReviewPattern, ReviewVerdict
from history_agent.review.storage.models import ReviewItem
from history_agent.review.verdict import (
    VerdictFormatError,
    extract_verdict,
    normalize_verdict,
)
from history_agent.runtime.agent import Harness


class VerdictResponse:
    def __init__(
        self,
        job: Journal,
        row: ReviewItem,
        baseline: dict[str, object],
        item_client: ItemClient,
        reviewer_factory: Callable[[ItemClient], Harness],
        on_event: Callable[..., None],
    ) -> None:
        self.job: Journal = job
        self.row: ReviewItem = row
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

    async def resolve(self, raw: str, original_raw: str) -> ReviewVerdict:
        self.raw = raw
        self.original_raw = original_raw
        self.retain()
        try:
            verdict: ReviewVerdict = self.parse()
        except VerdictFormatError as error:
            verdict = await self.repair([str(error)])
        self.verify_evidence(verdict)
        verdict.comment = review_comment(
            verdict.comment,
            [address.model_dump() for address in verdict.evidence],
            self.client.snapshots,
        )
        if not verdict.comment:
            raise ChatError(
                "Review explanation is empty after removing redundant metadata"
            )
        return verdict

    def retain(
        self,
        issues: list[str] | None = None,
        *,
        retry: bool = False,
    ) -> None:
        self.job.set(
            "pending_response",
            {
                "warning_id": self.row.warning_id,
                "baseline": self.baseline,
                "raw": self.raw,
                "original_raw": self.original_raw,
                "evidence": sorted(self.client.record),
                "validation_errors": issues or [],
                "retry_investigation": retry,
            },
        )

    def parse(self) -> ReviewVerdict:
        try:
            verdict: dict[str, object] = normalize_verdict(
                extract_verdict(self.raw),
                self.client.record,
                self.scope,
                self.row.warning_id,
                self.row.checker,
                STATUSES,
            )
            if "pattern" in verdict:
                try:
                    ReviewPattern.model_validate(verdict["pattern"])
                except ValidationError:
                    verdict.pop("pattern")
            self.draft = verdict
        except VerdictFormatError:
            raise
        except ChatError as error:
            self.retain([str(error)], retry=True)
            raise
        try:
            validated: ReviewVerdict = ReviewVerdict.model_validate(verdict)
        except ValidationError as error:
            issues: list[str] = [
                (".".join(map(str, issue["loc"])) or "verdict")
                + ": "
                + issue["msg"][:300]
                for issue in error.errors()[:5]
            ]
            raise VerdictFormatError("; ".join(issues)) from None
        return validated

    async def repair(self, issues: list[str]) -> ReviewVerdict:
        previous: dict[str, object] | None = copy.deepcopy(self.draft)
        self.retain(issues)
        self.on_event(
            "batch_format_repair",
            warning_id=self.row.warning_id,
            issues=issues,
        )
        formatter: Harness = self.reviewer_factory(self.client)
        formatter.tools, formatter.allowed = [], set()
        formatter.messages = [
            {"role": "system", "content": Prompts.get("review.format_repair")}
        ]
        self.raw = await formatter.ask(
            compact(
                {
                    "schema": ReviewVerdict.model_json_schema(),
                    "validation_errors": issues,
                    "draft": self.raw,
                }
            )
        )
        self.retain()
        try:
            verdict: ReviewVerdict = self.parse()
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
        self,
        previous: dict[str, object],
        verdict: ReviewVerdict,
    ) -> None:
        classification: object = previous.get("classification")
        if (
            isinstance(classification, str)
            and classification in STATUSES
            and verdict.classification != classification
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
            (a.file, a.line, a.snapshot_id) for a in verdict.evidence
        }
        if cited != repaired:
            self.reject(
                "Format repair changed the cited evidence",
                "Format repair changed the cited evidence; "
                "no review was saved",
            )

    def verify_evidence(self, verdict: ReviewVerdict) -> None:
        if (
            verdict.classification in EVIDENCE_REQUIRED
            and not verdict.evidence
        ):
            self.reject(
                "TP/FP/WF verdict has no source evidence",
                "TP/FP/WF verdict has no source evidence; no review was saved",
            )
        for address in verdict.evidence:
            if (
                address.file,
                address.line,
                address.snapshot_id,
            ) not in (self.client.record):
                self.reject(
                    "Verdict cited source that was not actually read: "
                    + compact(address.model_dump()),
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
