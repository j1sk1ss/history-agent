"""
Small checker-local hypotheses, never previous warnings' evidence or
verdicts.
"""

from __future__ import annotations

import json
import re
from sqlite3 import Connection, Cursor

from history_agent.inference.model import compact
from history_agent.review.classification import PATTERN_CLASSES

PATTERN_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "criterion", "checks"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "criterion": {"type": "string", "minLength": 1, "maxLength": 500},
        "checks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {"type": "string", "minLength": 1, "maxLength": 160},
        },
    },
}


def initialize(db: Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS checker_patterns(
        checker TEXT NOT NULL, name TEXT NOT NULL,
        classification TEXT NOT NULL,
        value TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(checker,name,classification))""")


def recall(
    db: Connection,
    checker: str,
) -> list[dict[str, str | list[str]]]:
    result: list[dict[str, str | list[str]]] = []
    rows: Cursor = db.execute(
        "SELECT classification,value FROM checker_patterns WHERE checker=? "
        "ORDER BY uses DESC,rowid DESC LIMIT 6",
        (checker,),
    )
    for classification, value in rows:
        hint: dict[str, str | list[str]] = {
            "classification": classification,
            **json.loads(value),
        }
        if len(compact(result + [hint])) > 2400:
            break
        result.append(hint)
        if len(result) == 3:
            break
    return result


def learn(
    db: Connection,
    checker: str,
    verdict: dict[str, object],
) -> None:
    pattern: dict[str, str | list[str]] | None = verdict.get("pattern")
    if not pattern or verdict["classification"] not in PATTERN_CLASSES:
        return
    text: str = compact(pattern)
    if re.search(r"(?:/[A-Za-z0-9_.-]+){3,}|\b[a-fA-F0-9]{40}\b", text):
        return
    with db:
        db.execute(
            """INSERT INTO checker_patterns(checker,name,classification,value)
            VALUES(?,?,?,?) ON CONFLICT(checker,name,classification)
            DO UPDATE SET
            value=excluded.value,uses=checker_patterns.uses+1""",
            (
                checker,
                pattern["name"],
                verdict["classification"],
                compact(pattern),
            ),
        )
        db.execute(
            """DELETE FROM checker_patterns WHERE checker=? AND rowid NOT IN
            (SELECT rowid FROM checker_patterns WHERE checker=?
             ORDER BY uses DESC,rowid DESC LIMIT 8)""",
            (checker, checker),
        )
