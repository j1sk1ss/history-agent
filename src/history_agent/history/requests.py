"""Validated arguments for actions implemented by the history adapter."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from history_agent.contracts import StrictModel

SnapshotId: TypeAlias = Annotated[
    str,
    Field(
        min_length=1, pattern=re.compile(r"^(?!HEAD$)"),
        description="Concrete snapshot ID; HEAD is invalid",
    ),
]

SnapshotReference: TypeAlias = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Snapshot ID or HEAD for the latest in the specified branch"
        ),
    ),
]


class CurrentRequest(StrictModel):
    project_id: int | None = Field(default=None, ge=0)
    project_name: str | None = Field(default=None, min_length=1)
    stream_id: str | None = Field(default=None, min_length=1)


class ResolveUrlRequest(StrictModel):
    url: str = Field(min_length=1, max_length=16384)


class CheckerDocumentationRequest(StrictModel):
    checker_name: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=3000, ge=1, le=8000)


class ComparisonOptions(StrictModel):
    base_project_id: int | None = Field(default=None, ge=0)
    base_stream_id: str | None = Field(default=None, min_length=1)
    base_snapshot_id: SnapshotReference | None = None
    subset: Literal["appeared", "disappeared", "both"] = "appeared"

    @model_validator(mode="after")
    def comparison_has_base_snapshot(self) -> Self:
        if self.base_snapshot_id is None and (
            self.base_project_id is not None
            or self.base_stream_id is not None
            or "subset" in self.model_fields_set
        ):
            raise ValueError("Comparison arguments require base_snapshot_id")
        return self


class CompareWarningsRequest(ComparisonOptions):
    project_id: int = Field(ge=0)
    stream_id: str = Field(min_length=1)
    snapshot_id: SnapshotId
    base_snapshot_id: SnapshotId
    checker_name: str | None = Field(default=None, min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
