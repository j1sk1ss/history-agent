"""Read saved job progress without modifying or migrating checkpoints."""

import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

FINISHED_ITEM_STATES = ("saved", "reviewed", "skipped", "conflict")


class JobRunningError(ValueError):
    """An operation cannot acquire exclusive ownership of a job."""


@contextmanager
def job_lock(path: Path):
    """Use the same process lock for running and deleting a checkpoint."""
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.write(b"0")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise JobRunningError(
                "Batch job is already running: " + path.stem
            ) from None
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def delete_job(root: Path, job_id: str) -> None:
    """Delete only local job files, refusing checkpoints in active use."""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid batch job ID")
    path = Path(root) / (job_id + ".sqlite3")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Batch job does not exist: " + job_id)
    with job_lock(path):
        path.unlink()
        for suffix in ("-wal", "-shm", "-journal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
    # Keep the lock inode: other processes may already be waiting on it.


@dataclass(frozen=True)
class UnfinishedJob:
    job_id: str
    scope: dict
    model: str
    write: bool
    done: int
    total: int
    queue_complete: bool
    modified: float


def unfinished_jobs(root: Path) -> list[UnfinishedJob]:
    """Find resumable checkpoints, most recently updated first."""
    result = []
    for path in Path(root).glob("*.sqlite3"):
        if not re.fullmatch(r"[a-f0-9]{32}", path.stem):
            continue
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as db:
                metadata = {
                    key: json.loads(value)
                    for key, value in db.execute(
                        "SELECT key,value FROM metadata WHERE key IN "
                        "('format','scope','model','write','queue_complete')"
                    )
                }
                scope = metadata["scope"]
                if metadata.get("format") != 1 or not isinstance(scope, dict):
                    continue
                if not all(
                    key in scope
                    for key in ("project_id", "stream_id", "snapshot_id")
                ):
                    continue
                states = dict(
                    db.execute(
                        "SELECT state,COUNT(*) FROM items GROUP BY state"
                    )
                )
            total = sum(states.values())
            done = sum(states.get(state, 0) for state in FINISHED_ITEM_STATES)
            queue_complete = metadata.get("queue_complete") is True
            if queue_complete and done == total:
                continue
            model = metadata.get("model", {})
            if not isinstance(model, dict):
                continue
            result.append(
                UnfinishedJob(
                    job_id=path.stem,
                    scope=scope,
                    model=str(model.get("model", "")),
                    write=metadata.get("write") is True,
                    done=done,
                    total=total,
                    queue_complete=queue_complete,
                    modified=path.stat().st_mtime,
                )
            )
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            # A damaged or partially initialized job must not block startup.
            continue
    return sorted(
        result, key=lambda job: (job.modified, job.job_id), reverse=True
    )
