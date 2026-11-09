"""New reviews and checkpoint resumes are distinct validated commands."""

from pydantic import Field, RootModel

from history_agent.contracts import StrictModel
from history_agent.history.requests import ComparisonOptions, SnapshotId


class ResumeRequest(StrictModel):
    job_id: str = Field(pattern="^[a-f0-9]{32}$")


class BatchSelection(ComparisonOptions):
    project_id: int | None = Field(default=None, ge=0)
    stream_id: str | None = Field(default=None, min_length=1)
    snapshot_id: SnapshotId | None = None
    checker_name: str | None = Field(default=None, min_length=1)
    instructions: str = Field(default="", max_length=8000)
    overwrite: bool = False


class BatchRequest(BatchSelection):
    project_id: int = Field(ge=0)
    stream_id: str = Field(min_length=1)
    snapshot_id: SnapshotId


class DifferenceRequest(BatchRequest):
    base_snapshot_id: SnapshotId


class BatchCommand(RootModel[BatchSelection | ResumeRequest]):
    """Public command permits resolving omitted scope from current history."""


class ResolvedBatchCommand(RootModel[BatchRequest | ResumeRequest]):
    """Runner command requires a concrete scope before creating a job."""


class DifferenceCommand(RootModel[DifferenceRequest | ResumeRequest]):
    """Difference reviews require explicit current and base snapshots."""
