import os
import tempfile
import unittest
from pathlib import Path

from history_agent.batch import Journal
from history_agent.jobs import (
    JobRunningError,
    delete_job,
    job_lock,
    unfinished_jobs,
)
from history_agent.model import ChatError


def checkpoint(root, states, *, queue_complete=True, **metadata):
    job = Journal(root)
    try:
        values = {
            "format": 1,
            "scope": {
                "project_id": 0,
                "stream_id": "master",
                "snapshot_id": "snapshot",
            },
            "model": {"base": "http://synthetic/v1", "model": "qwen"},
            "write": False,
            "queue_complete": queue_complete,
            **metadata,
        }
        for key, value in values.items():
            job.set(key, value)
        with job.db:
            for index, state in enumerate(states):
                job.db.execute(
                    "INSERT INTO items "
                    "(warning_id,checker,ordinal,warning,state) "
                    "VALUES(?,?,?,?,?)",
                    (str(index), "CHECKER", index, "{}", state),
                )
        return job.path
    finally:
        job.db.close()


class UnfinishedJobsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_lists_unfinished_states_with_original_scope_and_progress(self):
        path = checkpoint(
            self.root,
            [
                "saved",
                "reviewed",
                "skipped",
                "conflict",
                "pending",
                "analyzing",
                "proposed",
                "saving",
                "uncertain",
            ],
            write=True,
        )
        jobs = unfinished_jobs(self.root)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.job_id, path.stem)
        self.assertEqual((job.done, job.total), (4, 9))
        self.assertEqual(job.scope["stream_id"], "master")
        self.assertEqual(job.model, "qwen")
        self.assertTrue(job.write)

    def test_hides_finished_and_empty_completed_jobs(self):
        checkpoint(self.root, ["saved", "reviewed", "skipped", "conflict"])
        checkpoint(self.root, [])
        self.assertEqual(unfinished_jobs(self.root), [])

    def test_interrupted_queue_collection_remains_resumable(self):
        empty = checkpoint(self.root, [], queue_complete=False)
        partial = checkpoint(self.root, ["reviewed"], queue_complete=False)
        jobs = unfinished_jobs(self.root)
        self.assertEqual(
            {job.job_id for job in jobs}, {empty.stem, partial.stem}
        )
        self.assertTrue(all(not job.queue_complete for job in jobs))

    def test_discovery_is_read_only_and_sorts_by_last_update(self):
        old = checkpoint(self.root, ["pending"])
        recent = checkpoint(self.root, ["analyzing"])
        os.utime(old, (100, 100))
        os.utime(recent, (200, 200))
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        jobs = unfinished_jobs(self.root)
        self.assertEqual([job.job_id for job in jobs], [recent.stem, old.stem])
        after = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(old.stat().st_mtime, 100)
        self.assertEqual(recent.stat().st_mtime, 200)

    def test_bad_checkpoints_do_not_block_valid_jobs(self):
        valid = checkpoint(self.root, ["pending"])
        checkpoint(self.root, ["pending"], model=[])
        checkpoint(self.root, ["pending"], scope=None)
        checkpoint(self.root, ["pending"], format=999)
        journal = Journal(self.root)
        journal.db.close()
        (self.root / ("f" * 32 + ".sqlite3")).write_text("damaged database")
        (self.root / "not-a-job.sqlite3").write_text("not a job")
        self.assertEqual(
            [job.job_id for job in unfinished_jobs(self.root)], [valid.stem]
        )

    def test_missing_directory_is_not_created(self):
        missing = self.root / "missing"
        self.assertEqual(unfinished_jobs(missing), [])
        self.assertFalse(missing.exists())

    def test_delete_cleans_only_selected_checkpoint_and_sqlite_sidecars(self):
        path = checkpoint(self.root, ["pending"])
        other = checkpoint(self.root, ["analyzing"])
        companions = [
            path.with_name(path.name + suffix)
            for suffix in ("-wal", "-shm", "-journal")
        ]
        for companion in companions:
            companion.write_bytes(b"leftover SQLite file")
        delete_job(self.root, path.stem)
        self.assertFalse(path.exists())
        self.assertTrue(other.exists())
        self.assertTrue(
            all(not companion.exists() for companion in companions)
        )
        self.assertEqual(
            [job.job_id for job in unfinished_jobs(self.root)], [other.stem]
        )

    def test_running_job_cannot_be_deleted(self):
        path = checkpoint(self.root, ["analyzing"])
        before = path.read_bytes()
        with job_lock(path):
            with self.assertRaisesRegex(JobRunningError, "already running"):
                delete_job(self.root, path.stem)
        self.assertEqual(path.read_bytes(), before)

    def test_delete_rejects_invalid_ids_and_symlinks(self):
        path = checkpoint(self.root, ["pending"])
        for job_id in ("../escape", "", "a" * 31, "A" * 32):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                delete_job(self.root, job_id)
        link = self.root / ("a" * 32 + ".sqlite3")
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            delete_job(self.root, link.stem)
        self.assertTrue(path.exists())

    @unittest.skipIf(os.name == "nt", "Windows cannot unlink an open database")
    def test_previously_opened_job_cannot_run_after_deletion(self):
        path = checkpoint(self.root, ["pending"])
        job = Journal(self.root, path.stem)
        try:
            delete_job(self.root, path.stem)
            with self.assertRaisesRegex(ChatError, "does not exist"):
                with job.lock():
                    self.fail("Deleted checkpoint was allowed to run")
        finally:
            job.db.close()
