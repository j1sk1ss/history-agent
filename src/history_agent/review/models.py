"""Validated response models shared by model tools and verdict parsing."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from history_agent.review.classification import REVIEW_CRITERIA


class ResponseModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True
    )


class SourceEvidence(ResponseModel):
    file: str = Field(
        min_length=1,
        description="Exact source file address read via agent_source. Only "
        "comment uses short paths.",
    )
    line: int = Field(ge=1)
    snapshot_id: str = Field(
        min_length=1,
        description="Concrete snapshot of the source that was actually read; "
        "required here, omitted from comment.",
    )


class ReviewPattern(ResponseModel):
    name: str = Field(min_length=1, max_length=80)
    criterion: str = Field(min_length=1, max_length=500)
    checks: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        min_length=1, max_length=4
    )


class ReviewVerdict(ResponseModel):
    classification: Literal["TP", "FP", "WF", "UC", "UD"] = Field(
        description=REVIEW_CRITERIA
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
