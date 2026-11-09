"""Parse CLI and slash-command scopes without managing a conversation."""

import asyncio
import shlex
from argparse import Namespace

from history_agent.history.client import HistoryClient
from history_agent.inference.model import ChatError

COMMAND_USAGE: str = (
    "Use /batch [PROJECT BRANCH SNAPSHOT [CHECKER]], "
    "/diff PROJECT BRANCH CURRENT BASE [appeared|disappeared|both] [CHECKER], "
    "or /resume JOB_ID"
)


def initial_arguments(args: Namespace) -> tuple[dict[str, object], bool]:
    if args.resume:
        return {"job_id": args.resume}, True
    selected: list[str] = args.diff or args.batch
    try:
        project: int = int(selected[0])
    except ValueError:
        raise ChatError("Batch project ID must be an integer") from None
    values: dict[str, object] = {
        "project_id": project,
        "stream_id": selected[1],
        "snapshot_id": selected[2],
        "instructions": args.batch_instructions,
        "overwrite": args.overwrite_reviews,
    }
    if args.diff:
        values.update(
            base_snapshot_id=selected[3], subset=args.subset or "appeared"
        )
        if args.base_project is not None:
            values["base_project_id"] = args.base_project
        if args.base_branch is not None:
            values["base_stream_id"] = args.base_branch
    return values, False


async def command_arguments(
    text: str, args: Namespace, history: HistoryClient,
) -> tuple[dict[str, object], bool]:
    parts: list[str] = shlex.split(text)
    if parts[0] == "/resume" and len(parts) == 2:
        return {"job_id": parts[1]}, True
    if parts[0] == "/batch" and len(parts) == 1:
        defaults: dict[str, object] = await asyncio.to_thread(
            history.execute, "current", {}
        )
        return {
            **defaults["scope"],
            "instructions": args.batch_instructions,
            "overwrite": args.overwrite_reviews,
        }, False
    if parts[0] == "/batch" and len(parts) in (4, 5):
        return batch_scope(parts, args), False
    if parts[0] == "/diff" and len(parts) in (5, 6, 7):
        return difference_scope(parts, args), False
    raise ChatError(COMMAND_USAGE)


def batch_scope(parts: list[str], args: Namespace) -> dict[str, object]:
    values: dict[str, object] = {
        "project_id": int(parts[1]),
        "stream_id": parts[2],
        "snapshot_id": parts[3],
        "instructions": args.batch_instructions,
        "overwrite": args.overwrite_reviews,
    }
    if len(parts) == 5:
        values["checker_name"] = parts[4]
    return values


def difference_scope(parts: list[str], args: Namespace) -> dict[str, object]:
    values: dict[str, object] = batch_scope(parts[:4], args)
    values.update(
        base_snapshot_id=parts[4],
        subset=parts[5] if len(parts) > 5 else "appeared",
    )
    if len(parts) == 7:
        values["checker_name"] = parts[6]
    return values
