"""Store bounded checker-local hypotheses independently of investigation."""

from sqlalchemy import Select, select

from history_agent.review.storage.checkpoint import CheckpointStore
from history_agent.review.storage.models import CheckerPattern


def ranked_patterns(checker: str) -> Select[tuple[CheckerPattern]]:
    return (
        select(CheckerPattern)
        .where(CheckerPattern.checker == checker)
        .order_by(CheckerPattern.uses.desc(), CheckerPattern.row_id.desc())
    )


def stored_patterns(
    store: CheckpointStore, checker: str,
) -> list[CheckerPattern]:
    with store.sessions() as session:
        return list(session.scalars(ranked_patterns(checker).limit(6)))


def save_pattern(
    store: CheckpointStore, checker: str, classification: str,
    name: str, value: dict[str, str | list[str]],
) -> None:
    with store.sessions.begin() as session:
        key: tuple[str, str, str] = (checker, name, classification)
        record: CheckerPattern | None = session.get(CheckerPattern, key)
        if record is None:
            session.add(
                CheckerPattern(
                    checker=checker,
                    name=name,
                    classification=classification,
                    value=value,
                )
            )
        else:
            record.value = value
            record.uses += 1
        session.flush()
        for stale in session.scalars(ranked_patterns(checker).offset(8)):
            session.delete(stale)
