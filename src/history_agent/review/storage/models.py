"""Mappings for the existing version 1 checkpoint tables."""

from sqlalchemy import JSON, Float, Integer, Text, column
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    mapped_column,
)


class Base(DeclarativeBase):
    pass


class MetadataEntry(Base):
    __tablename__: str = "metadata"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSON, nullable=False)


class ReviewItem(Base):
    __tablename__: str = "items"

    warning_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checker: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    warning: Mapped[dict[str, object]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending"
    )
    verdict: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    baseline: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )


class CheckerCache(Base):
    __tablename__: str = "checker_cache"

    checker: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON)


class ReviewAttempt(Base):
    __tablename__: str = "review_attempts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    warning_id: Mapped[str] = mapped_column(Text, index=True)
    started_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running")
    route: Mapped[str] = mapped_column(Text, default="agent")
    wall_seconds: Mapped[float] = mapped_column(Float, default=0)
    waiting_seconds: Mapped[float] = mapped_column(Float, default=0)
    agent_seconds: Mapped[float] = mapped_column(Float, default=0)


class CheckerPattern(Base):
    __tablename__: str = "checker_patterns"

    checker: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    classification: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, str | list[str]]] = mapped_column(JSON)
    uses: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # SQLite already provides rowid; do not add a column to old checkpoints.
    row_id: Mapped[int] = column_property(column("rowid", Integer))
