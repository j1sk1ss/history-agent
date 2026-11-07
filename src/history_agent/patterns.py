"""
Small checker-local hypotheses, never previous warnings' evidence or
verdicts.
"""

import json
import re

from .classification import PATTERN_CLASSES
from .model import compact

PATTERN_SCHEMA = {
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


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS checker_patterns(
        checker TEXT NOT NULL, name TEXT NOT NULL,
        classification TEXT NOT NULL,
        value TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(checker,name,classification))""")


def recall(job, checker):
    result = []
    rows = job.db.execute(
        "SELECT classification,value FROM checker_patterns WHERE checker=? "
        "ORDER BY uses DESC,rowid DESC LIMIT 6",
        (checker,),
    )
    for classification, value in rows:
        hint = {"classification": classification, **json.loads(value)}
        if len(compact(result + [hint])) > 2400:
            break
        result.append(hint)
        if len(result) == 3:
            break
    return result


def learn(job, checker, verdict):
    pattern = verdict.get("pattern")
    if not pattern or verdict["classification"] not in PATTERN_CLASSES:
        return
    text = compact(pattern)
    if re.search(r"(?:/[A-Za-z0-9_.-]+){3,}|\b[a-fA-F0-9]{40}\b", text):
        return
    with job.db:
        job.db.execute(
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
        job.db.execute(
            """DELETE FROM checker_patterns WHERE checker=? AND rowid NOT IN
            (SELECT rowid FROM checker_patterns WHERE checker=?
             ORDER BY uses DESC,rowid DESC LIMIT 8)""",
            (checker, checker),
        )
