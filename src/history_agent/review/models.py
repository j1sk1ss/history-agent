"""Validated response models shared by model tools and verdict parsing."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from history_agent.contracts import StrictModel
from history_agent.prompts import Prompts


class SourceEvidence(StrictModel):
    file: str = Field(
        min_length=1,
        description="Exact source file address read via agent_source. Only "
        "comment uses short paths.",
    )
    line: int = Field(ge=1)
    snapshot_id: str = Field(
        min_length=1,
        description="Snapshot name returned with the source actually read; "
        "the host resolves its ID. Required here, omitted from comment.",
    )


class ReviewPattern(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    criterion: str = Field(min_length=1, max_length=500)
    checks: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        min_length=1, max_length=4
    )


class ReviewVerdict(StrictModel):
    classification: Literal["TP", "FP", "WF", "UC", "UD"] = Field(
        json_schema_extra=lambda schema: schema.update(
            description=Prompts.get("review.criteria")
        )
    )
    comment: str = Field(min_length=1, max_length=8000)
    evidence: list[SourceEvidence] = Field(max_length=20)
    pattern: ReviewPattern | None = None

    @field_validator("comment")
    @classmethod
    def explanation_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation must not be blank")
        return value
