"""
Small checker-local hypotheses, never previous warnings' evidence or
verdicts.
"""

from __future__ import annotations

import re

from history_agent.inference.model import compact
from history_agent.review.classification import PATTERN_CLASSES
from history_agent.review.models import ReviewPattern, ReviewVerdict
from history_agent.review.storage.checkpoint import CheckpointStore
from history_agent.review.storage.models import CheckerPattern
from history_agent.review.storage.patterns import save_pattern, stored_patterns


def recall(
    store: CheckpointStore, checker: str,
) -> list[dict[str, str | list[str]]]:
    result: list[dict[str, str | list[str]]] = []
    rows: list[CheckerPattern] = stored_patterns(store, checker)
    for row in rows:
        hint: dict[str, str | list[str]] = {
            "classification": row.classification,
            **row.value,
        }
        if len(compact(result + [hint])) > 2400:
            break
        result.append(hint)
        if len(result) == 3:
            break
    return result


def learn(
    store: CheckpointStore, checker: str, verdict: ReviewVerdict,
) -> None:
    pattern: ReviewPattern | None = verdict.pattern
    if not pattern or verdict.classification not in PATTERN_CLASSES:
        return
    text: str = pattern.model_dump_json()
    if re.search(r"(?:/[A-Za-z0-9_.-]+){3,}|\b[a-fA-F0-9]{40}\b", text):
        return
    save_pattern(
        store, checker, verdict.classification,
        pattern.name, pattern.model_dump(),
    )
