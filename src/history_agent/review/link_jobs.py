"""Reject resumes of checkpoints outside the active history selection."""

from pathlib import Path

from pydantic import ValidationError

from history_agent.review.requests import ResumeRequest
from history_agent.review.storage.checkpoint import CheckpointStore


def check_resume(root, args, links, reject):
    try:
        request = ResumeRequest.model_validate(args)
    except ValidationError:
        reject("Resume requires only the existing job_id")
    path = Path(root) / (request.job_id + ".sqlite3")
    if not path.is_file():
        reject("Resume checkpoint does not exist")
    checkpoint = CheckpointStore(path, read_only=True)
    try:
        metadata = checkpoint.metadata(("scope", "comparison", "warning_id"))
    finally:
        checkpoint.close()
    for link in links:
        if metadata.get("comparison"):
            expected = {
                key: link.get(key) for key in ("current", "base", "subset")
            }
            if (
                link["kind"] == "comparison"
                and metadata["comparison"] == expected
            ):
                return
        elif metadata.get("scope") == link["scope"]:
            if link["kind"] == "comparison" and not metadata.get("warning_id"):
                continue
            if (
                link["kind"] == "warning"
                and metadata.get(
                    "warning_id",
                )
                != link["selected_warning_id"]
            ):
                continue
            return
    reject("Checkpoint belongs to a different history selection")
