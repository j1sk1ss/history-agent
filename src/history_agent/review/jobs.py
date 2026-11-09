"""Read saved job progress without modifying or migrating checkpoints."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from history_agent.review.storage.checkpoint import CheckpointStore

FINISHED_ITEM_STATES: tuple[str, ...] = (
    "saved",
    "reviewed",
    "skipped",
    "conflict",
)


class JobRunningError(ValueError):
    """An operation cannot acquire exclusive ownership of a job."""


@contextmanager
def job_lock(path: Path) -> Iterator[None]:
    """Use the same process lock for running and deleting a checkpoint."""
    fd: int = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
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
    path: Path = Path(root) / (job_id + ".sqlite3")
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
    scope: dict[str, str | int]
    model: str
    write: bool
    done: int
    total: int
    queue_complete: bool
    modified: float


def unfinished_jobs(root: Path) -> list[UnfinishedJob]:
    """Find resumable checkpoints, most recently updated first."""
    result: list[UnfinishedJob] = []
    for path in Path(root).glob("*.sqlite3"):
        if not re.fullmatch(r"[a-f0-9]{32}", path.stem):
            continue
        try:
            store: CheckpointStore = CheckpointStore(path, read_only=True)
            try:
                metadata: dict[str, object] = store.metadata(
                    (
                        "format",
                        "scope",
                        "model",
                        "write",
                        "queue_complete",
                    )
                )
                scope: dict[str, str | int] = metadata["scope"]
                if metadata.get("format") != 1 or not isinstance(scope, dict):
                    continue
                if not all(
                    key in scope
                    for key in ("project_id", "stream_id", "snapshot_id")
                ):
                    continue
                states: dict[str, int] = store.state_counts()
            finally:
                store.close()
            total: int = sum(states.values())
            done: int = sum(
                states.get(state, 0) for state in FINISHED_ITEM_STATES
            )
            queue_complete: bool = metadata.get("queue_complete") is True
            if queue_complete and done == total:
                continue
            model: dict[str, str] = metadata.get("model", {})
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
        except (OSError, SQLAlchemyError, ValueError, TypeError, KeyError):
            # A damaged or partially initialized job must not block startup.
            continue
    return sorted(
        result, key=lambda job: (job.modified, job.job_id), reverse=True
    )
