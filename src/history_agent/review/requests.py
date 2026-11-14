"""New reviews and checkpoint resumes are distinct validated commands."""

from typing import Literal

from pydantic import Field, RootModel, model_validator

from history_agent.contracts import StrictModel
from history_agent.history.requests import (
    ComparisonOptions,
    SnapshotId,
    SnapshotReference,
)


class ResumeRequest(StrictModel):
    job_id: str = Field(pattern="^[a-f0-9]{32}$")


class BatchSelection(ComparisonOptions):
    project_id: int | None = Field(default=None, ge=0)
    stream_id: str | None = Field(default=None, min_length=1)
    snapshot_id: SnapshotReference | None = None
    checker_name: str | None = Field(default=None, min_length=1)
    warning_id: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=8000)
    overwrite: Literal[False] = False

    @model_validator(mode="after")
    def concrete_single_scope(self):
        if self.warning_id and self.base_snapshot_id is not None:
            raise ValueError(
                "Single-warning review requires its concrete snapshot scope"
            )
        return self


class BatchRequest(BatchSelection):
    project_id: int = Field(ge=0)
    stream_id: str = Field(min_length=1)
    snapshot_id: SnapshotId
    base_snapshot_id: SnapshotId | None = None


class DifferenceSelection(BatchSelection):
    project_id: int = Field(ge=0)
    stream_id: str = Field(min_length=1)
    snapshot_id: SnapshotReference
    base_snapshot_id: SnapshotReference


class BatchCommand(RootModel[BatchSelection | ResumeRequest]):
    """Public command permits resolving omitted scope from current history."""


class ResolvedBatchCommand(RootModel[BatchRequest | ResumeRequest]):
    """Runner command requires a concrete scope before creating a job."""


class DifferenceCommand(RootModel[DifferenceSelection | ResumeRequest]):
    """Difference reviews accept concrete snapshots or branch HEADs."""
