"""Persistence guarantees, including checkpoints predating SQLAlchemy."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError

from history_agent.review import patterns
from history_agent.review.jobs import UnfinishedJob, unfinished_jobs
from history_agent.review.journal import Journal
from history_agent.review.models import ReviewVerdict
from history_agent.review.storage.checkpoint import CheckpointStore
from history_agent.review.storage.models import ReviewItem
from history_agent.review.storage.patterns import (
    ranked_patterns,
    stored_patterns,
)

LEGACY_SCHEMA: Path = Path(__file__).parent / "fixtures/checkpoint_v1.sql"


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        directory: tempfile.TemporaryDirectory[str] = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(directory.cleanup)
        self.root: Path = Path(directory.name)

    def legacy_checkpoint(self) -> Path:
        path: Path = self.root / ("a" * 32 + ".sqlite3")
        with sqlite3.connect(path) as database:
            database.executescript(LEGACY_SCHEMA.read_text())
        database.close()
        return path

    def test_legacy_checkpoint_is_discovered_without_changes(self) -> None:
        path: Path = self.legacy_checkpoint()
        original: bytes = path.read_bytes()
        jobs: list[UnfinishedJob] = unfinished_jobs(self.root)
        self.assertEqual([job.job_id for job in jobs], [path.stem])
        self.assertEqual(jobs[0].scope["snapshot_id"], "old")
        self.assertEqual((jobs[0].done, jobs[0].total), (0, 1))
        self.assertEqual(path.read_bytes(), original)

    def test_legacy_data_survives_orm_resume(self) -> None:
        path: Path = self.legacy_checkpoint()
        journal: Journal = Journal(self.root, path.stem)
        self.addCleanup(journal.close)
        item: ReviewItem = journal.items()[0]
        self.assertEqual(item.warning, {"warning_id": "legacy-warning"})
        self.assertEqual(item.verdict["classification"], "UC")
        self.assertEqual(item.baseline["classification_id"], 0)
        self.assertEqual(
            journal.cached_checker("CHECKER"), {"documentation": "Old docs"}
        )
        self.assertEqual(
            patterns.recall(journal, "CHECKER")[0]["name"], "old-pattern"
        )
        journal.update(item.warning_id, "reviewed")
        journal.enable_writes()
        self.assertIs(journal.get("write"), True)
        resumed: ReviewItem = journal.items()[0]
        self.assertEqual(resumed.state, "proposed")
        self.assertEqual(resumed.verdict, item.verdict)
        self.assertEqual(resumed.baseline, item.baseline)

    def test_page_insert_rolls_back_on_duplicate_warning(self) -> None:
        journal: Journal = Journal(self.root)
        self.addCleanup(journal.close)
        with self.assertRaises(IntegrityError):
            journal.add_items(
                [
                    ReviewItem(
                        warning_id="duplicate",
                        checker="A",
                        ordinal=0,
                        warning={},
                    ),
                    ReviewItem(
                        warning_id="duplicate",
                        checker="B",
                        ordinal=1,
                        warning={},
                    ),
                ]
            )
        self.assertEqual(journal.items(), [])

    def test_read_only_store_refuses_writes(self) -> None:
        path: Path = self.legacy_checkpoint()
        original: bytes = path.read_bytes()
        store: CheckpointStore = CheckpointStore(path, read_only=True)
        self.addCleanup(store.close)
        with self.assertRaises(OperationalError):
            store.set("write", True)
        self.assertEqual(path.read_bytes(), original)

    def test_pattern_ranking_reuse_and_retention(self) -> None:
        journal: Journal = Journal(self.root)
        self.addCleanup(journal.close)
        for index in range(10):
            patterns.learn(
                journal,
                "CHECKER",
                ReviewVerdict.model_validate(
                    {
                        "classification": "FP",
                        "comment": "Guard holds",
                        "evidence": [],
                        "pattern": {
                            "name": str(index),
                            "criterion": "Guard holds",
                            "checks": ["Inspect the guard"],
                        },
                    }
                ),
            )
        hints: list[dict[str, str | list[str]]] = patterns.recall(
            journal, "CHECKER"
        )
        self.assertEqual([hint["name"] for hint in hints], ["9", "8", "7"])
        patterns.learn(
            journal,
            "CHECKER",
            ReviewVerdict.model_validate(
                {
                    "classification": "FP",
                    "comment": "Guard holds",
                    "evidence": [],
                    "pattern": {
                        key: value
                        for key, value in hints[2].items()
                        if key != "classification"
                    },
                }
            ),
        )
        self.assertEqual(patterns.recall(journal, "CHECKER")[0]["name"], "7")
        self.assertEqual(stored_patterns(journal, "OTHER_CHECKER"), [])
        with journal.sessions() as session:
            self.assertEqual(
                len(list(session.scalars(ranked_patterns("CHECKER")))), 8
            )
