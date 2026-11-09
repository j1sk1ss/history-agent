"""Transactional access to job metadata, warning queues and checker caches."""

from pathlib import Path

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from history_agent.review.storage.database import checkpoint_engine
from history_agent.review.storage.models import (
    Base,
    CheckerCache,
    MetadataEntry,
    ReviewItem,
)


class CheckpointStore:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.engine: Engine = checkpoint_engine(path, read_only=read_only)
        self.sessions: sessionmaker[Session] = sessionmaker(
            self.engine, expire_on_commit=False
        )
        if not read_only:
            try:
                Base.metadata.create_all(self.engine)
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        self.engine.dispose()

    def get(self, key: str, default: object = None) -> object:
        with self.sessions() as session:
            entry: MetadataEntry | None = session.get(MetadataEntry, key)
            return entry.value if entry is not None else default

    def metadata(self, keys: tuple[str, ...]) -> dict[str, object]:
        with self.sessions() as session:
            return {
                entry.key: entry.value
                for entry in session.scalars(
                    select(MetadataEntry).where(MetadataEntry.key.in_(keys))
                )
            }

    def set(self, key: str, value: object) -> None:
        with self.sessions.begin() as session:
            session.merge(MetadataEntry(key=key, value=value))

    def enable_writes(self) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(ReviewItem)
                .where(ReviewItem.state == "reviewed")
                .values(state="proposed")
            )
            session.merge(MetadataEntry(key="write", value=True))

    def clear_items(self) -> None:
        with self.sessions.begin() as session:
            session.execute(delete(ReviewItem))

    def add_items(self, items: list[ReviewItem]) -> None:
        with self.sessions.begin() as session:
            session.add_all(items)

    def items(
        self, *, excluded_states: tuple[str, ...] = (),
    ) -> list[ReviewItem]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(ReviewItem)
                    .where(ReviewItem.state.not_in(excluded_states))
                    .order_by(
                        ReviewItem.checker.collate("NOCASE"),
                        ReviewItem.ordinal,
                    )
                )
            )

    def update(
        self, warning_id: str, state: str, *,
        verdict: dict[str, object] | None = None,
        baseline: dict[str, object] | None = None,
    ) -> None:
        with self.sessions.begin() as session:
            item: ReviewItem | None = session.get(ReviewItem, warning_id)
            if item is None:
                return
            item.state = state
            if verdict is not None:
                item.verdict = verdict
            if baseline is not None:
                item.baseline = baseline

    def state_counts(self) -> dict[str, int]:
        with self.sessions() as session:
            return dict(
                session.execute(
                    select(ReviewItem.state, func.count()).group_by(
                        ReviewItem.state
                    )
                ).all()
            )

    def completed_verdicts(self) -> list[dict[str, object]]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(ReviewItem.verdict)
                    .where(ReviewItem.state.in_(("saved", "reviewed")))
                    .where(ReviewItem.verdict.is_not(None))
                )
            )

    def cached_checker(self, checker: str) -> dict[str, object] | None:
        with self.sessions() as session:
            cached: CheckerCache | None = session.get(CheckerCache, checker)
            return cached.value if cached is not None else None

    def cache_checker(self, checker: str, value: dict[str, object]) -> None:
        with self.sessions.begin() as session:
            session.merge(CheckerCache(checker=checker, value=value))
