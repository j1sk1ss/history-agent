"""
Durable review jobs. Model conversations contain one checker and one
warning.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import Client
from mcp.types import CallToolResult, TextContent

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.history.comparison import (
    COMPARE_ACTION,
    COMPARE_SCHEMA,
    scopes,
)
from history_agent.history.links import find_history_urls
from history_agent.inference.model import ChatError, compact
from history_agent.review import patterns
from history_agent.review.classification import (
    EVIDENCE_REQUIRED,
    REVIEW_CRITERIA,
    STATUSES,
)
from history_agent.review.comments import review_comment
from history_agent.review.jobs import (
    FINISHED_ITEM_STATES,
    JobRunningError,
    job_lock,
)
from history_agent.review.verdict import (
    VerdictFormatError,
    extract_verdict,
    normalize_verdict,
    schema_issues,
)
from history_agent.runtime import agent

BATCH_ACTION: str = "review_batch"
BATCH_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "project_id": {"type": "integer", "minimum": 0},
        "stream_id": {"type": "string", "minLength": 1},
        "snapshot_id": {
            "type": "string",
            "minLength": 1,
            "not": {"const": "HEAD"},
        },
        "checker_name": {"type": "string", "minLength": 1},
        "instructions": {"type": "string", "maxLength": 8000},
        "overwrite": {"type": "boolean", "default": False},
        "job_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
    },
    "oneOf": [
        {
            "required": ["project_id", "stream_id", "snapshot_id"],
            "not": {"required": ["job_id"]},
        },
        {"required": ["job_id"], "maxProperties": 1},
    ],
}
BATCH_PUBLIC_SCHEMA: dict[str, object] = copy.deepcopy(BATCH_SCHEMA)
BATCH_PUBLIC_SCHEMA["oneOf"] = [
    {"not": {"required": ["job_id"]}},
    {"required": ["job_id"], "maxProperties": 1},
]
BATCH_META: dict[str, object] = {
    "name": BATCH_ACTION,
    "read_only": False,
    "input_schema": BATCH_PUBLIC_SCHEMA,
    "description": "Review all warnings with fresh per-warning model context, "
    "grouped by checker. "
    "Missing project/branch/snapshot defaults to history_current "
    "(alphabetical project/branch, "
    "first snapshot); URL scopes must be passed explicitly. "
    "The session save setting controls immediate server writes, "
    "including when called through history_read. "
    "Resume with only job_id. "
    "Existing reviews are preserved by default.",
}
DIFF_ACTION: str = "review_diff"
for key in ("base_project_id", "base_stream_id", "base_snapshot_id", "subset"):
    BATCH_SCHEMA["properties"][key] = copy.deepcopy(
        COMPARE_SCHEMA["properties"][key]
    )
BATCH_SCHEMA["dependentRequired"] = {
    key: ["base_snapshot_id"]
    for key in ("base_project_id", "base_stream_id", "subset")
}
DIFF_SCHEMA: dict[str, object] = copy.deepcopy(BATCH_SCHEMA)
DIFF_SCHEMA["oneOf"][0]["required"].append("base_snapshot_id")
DIFF_META: dict[str, object] = {
    "name": DIFF_ACTION,
    "read_only": False,
    "input_schema": DIFF_SCHEMA,
    "description": "Review only a snapshot difference in isolated resumable "
    "contexts. "
    "Requires current and base snapshots; default appeared (UI First). "
    "disappeared (UI Second) uses baseline source and baseline review branch. "
    "both uses current source. Disappearance is not proof of FP/fix. "
    "The session save setting controls immediate server writes, "
    "including when called through history_read. "
    "Resume with only job_id.",
}
RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classification", "comment", "evidence"],
    "properties": {
        "classification": {
            "type": "string",
            "enum": list(STATUSES),
            "description": REVIEW_CRITERIA,
        },
        "comment": {"type": "string", "minLength": 1, "maxLength": 8000},
        "evidence": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "line", "snapshot_id"],
                "properties": {
                    "file": {"type": "string", "minLength": 1},
                    "line": {"type": "integer", "minimum": 1},
                    "snapshot_id": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}
RESULT_SCHEMA["properties"]["pattern"] = patterns.PATTERN_SCHEMA
RESULT_SCHEMA["properties"]["evidence"]["items"]["properties"]["file"][
    "description"
] = (
    "Exact source file address read via agent_source. Only "
    "comment uses short paths."
)
RESULT_SCHEMA["properties"]["evidence"]["items"]["properties"]["snapshot_id"][
    "description"
] = (
    "Concrete snapshot of the source that was actually read; "
    "required here, omitted from comment."
)
BATCH_INSTRUCTION: str = (
    "This is an isolated batch item. Investigate only its "
    "assigned warning; no previous warning context exists. "
    "The host has fetched the current checker guide, warning, "
    "trace, review and source below; reuse those reads. "
    "Request further source/navigation through read tools as "
    "needed. Do not save reviews: the host handles saving. "
    "Finish by calling submit_verdict alone with classification "
    "(TP/FP/WF/UC/UD), comment (concrete reasoning), and evidence"
    " "
    "(list of {file,line,snapshot_id} addresses actually read "
    "with agent_source). "
    "TP, FP and WF require actual source evidence. " + REVIEW_CRITERIA + " "
    "Use UC if checker meaning or evidence remains insufficient. "
    "No long user-facing report. "
    "The final tool schema specifies exact field names and types."
    " It returns a proposal, not a saved review. "
    "If using a textual final answer, return exactly one JSON "
    "object with those same fields. "
    "Do not append explanations about host saving or existing "
    "reviews outside the JSON comment. "
    "Keep the saved explanation short. Cite project-relative "
    "file:line, never absolute paths or snapshot IDs in comment; "
    "evidence must still contain the full addresses for host validation. "
    "For a recurring TP/FP/WF pattern you may add optional "
    "pattern={name,criterion,checks}: a general reusable "
    "condition "
    "and short checklist, without case-specific paths, IDs, "
    "previous code or verdict claims. Reuse the same name "
    "for the same criterion. Checker-local pattern hints are "
    "hypotheses: verify every checklist condition against "
    "this warning's actual code/trace, and investigate normally "
    "if anything differs. Skip redundant tool reads when "
    "the prefetched guide, trace and source already prove every "
    "required condition."
)


def reply(data: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=compact(data))], is_error=False
    )


def review_attributes(warning: dict[str, object]) -> dict[str, object]:
    attributes: dict[str, object] = warning.get("attributes")
    if (
        not isinstance(attributes, dict)
        or "classification_id" not in attributes
    ):
        raise ChatError("Current warning has no usable review attributes")
    return copy.deepcopy(attributes)


class Journal:
    def __init__(self, root: str | Path, job_id: str | None = None) -> None:
        self.root: Path = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.id: str = job_id or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.id):
            raise ChatError("Invalid batch job ID")
        self.path: Path = self.root / (self.id + ".sqlite3")
        if job_id and not self.path.is_file():
            raise ChatError("Batch job does not exist: " + self.id)
        if not job_id:
            fd: int = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
            )
            os.close(fd)
        self.db: sqlite3.Connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items(
                warning_id TEXT PRIMARY KEY, checker TEXT NOT NULL,
                ordinal INTEGER NOT NULL, warning TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                verdict TEXT, baseline TEXT);
            CREATE TABLE IF NOT EXISTS checker_cache(
                checker TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        with self.db:
            patterns.initialize(self.db)

    def get(self, key: str, default: object = None) -> object:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: object) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                (key, compact(value)),
            )

    def update(
        self, warning_id: str, state: str, *,
        verdict: dict[str, object] | None = None,
        baseline: dict[str, object] | None = None,
    ) -> None:
        with self.db:
            self.db.execute(
                "UPDATE items SET state=?, verdict=COALESCE(?,verdict), "
                "baseline=COALESCE(?,baseline) WHERE warning_id=?",
                (
                    state,
                    compact(verdict) if verdict is not None else None,
                    compact(baseline) if baseline is not None else None,
                    warning_id,
                ),
            )

    def summary(self) -> dict[str, object]:
        states: dict[str, int] = dict(
            self.db.execute("SELECT state,COUNT(*) FROM items GROUP BY state")
        )
        classes: Counter[str] = Counter()
        for row in self.db.execute(
            "SELECT verdict FROM items WHERE state IN ('saved','reviewed')"
        ):
            classes[json.loads(row[0])["classification"]] += 1
        return {
            "job_id": self.id,
            "scope": self.get("scope"),
            "comparison": self.get("comparison"),
            "write": self.get("write"),
            "total": sum(states.values()),
            "states": states,
            "classifications": dict(classes),
            "checkpoint": str(self.path),
        }

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            with job_lock(self.path):
                if not self.path.is_file():
                    raise ChatError("Batch job does not exist: " + self.id)
                yield
        except JobRunningError as error:
            raise ChatError(str(error)) from None


class ItemClient:
    """
    Scope batch reads and record actual source evidence without retaining
    old conversations.
    """

    def __init__(
        self, client: Client, scope: dict[str, str | int],
        record: set[tuple[str, int, str]],
    ) -> None:
        self.client: Client
        self.scope: dict[str, str | int]
        self.record: set[tuple[str, int, str]]
        self.client, self.scope, self.record = client, scope, record
        self.snapshots: set[str] = {scope["snapshot_id"]}

    def observe(
        self, action: str | None, args: dict[str, object],
        result: dict[str, object],
    ) -> None:
        def overrides(value: object) -> None:
            if isinstance(value, dict):
                for key, entry in value.items():
                    if (
                        key == "snapshot_override"
                        and isinstance(entry, str)
                        and entry
                    ):
                        self.snapshots.add(entry)
                    else:
                        overrides(entry)
            elif isinstance(value, list):
                for entry in value:
                    overrides(entry)

        overrides(result)
        if action == "agent_source" and isinstance(result, dict):
            for line in result.get("lines", []):
                if isinstance(line, dict) and isinstance(
                    line.get("number"), int
                ):
                    self.record.add(
                        (args["file"], line["number"], args["snapshot_id"])
                    )

    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> CallToolResult:
        if name not in ("history_discover", "history_read"):
            raise ChatError(
                "Batch model cannot write reviews or start another job"
            )
        args: dict[str, object] = arguments.get("arguments", {})
        if not isinstance(args, dict):
            raise ChatError("Batch read arguments must be an object")
        if name == "history_read":
            action: str | None = arguments.get("action")
            assigned_checker: str | None = self.scope.get("checker_name")
            if assigned_checker and action in (
                "agent_checker_info",
                "agent_checker_documentation",
            ):
                requested: object = args.get("checker_name")
                if (
                    not isinstance(requested, str)
                    or requested.casefold() != assigned_checker.casefold()
                ):
                    raise ChatError(
                        "Batch checker documentation belongs to a "
                        "different item"
                    )
            scoped: set[str] = {
                "get_warning",
                "get_warning_trace",
                "get_warning_trace_and_role",
                "get_warnings",
                "get_file_tree",
                "get_tokens",
                "get_ref_tokens",
                "agent_source",
                "agent_resolve_symbol",
                "agent_find_warnings",
            }
            if action in scoped and "snapshot_id" not in args:
                args = {**args, "snapshot_id": self.scope["snapshot_id"]}
                arguments = {**arguments, "arguments": args}
            if (
                args.get("project_id", self.scope["project_id"])
                != self.scope["project_id"]
            ):
                raise ChatError("Batch read left the assigned project")
            if (
                args.get("stream_id", self.scope["stream_id"])
                != self.scope["stream_id"]
            ):
                raise ChatError("Batch read left the assigned branch")
            snapshot: str | None = args.get("snapshot_id")
            if (
                snapshot
                and snapshot not in self.snapshots
                and not (
                    snapshot == "HEAD"
                    and arguments.get("action") == "get_warning"
                )
            ):
                raise ChatError(
                    "Batch read used a snapshot outside the warning/trace"
                )
        result: CallToolResult = await self.client.call_tool(name, arguments)
        if name == "history_read" and not result.is_error:
            try:
                data: dict[str, object] = json.loads(
                    "".join(
                        part.text
                        for part in result.content
                        if part.type == "text"
                    )
                )
                self.observe(arguments.get("action"), args, data)
            except (ValueError, KeyError):
                pass
        return result


class BatchRunner:
    def __init__(
        self, history: HistoryClient, client: Client,
        reviewer_factory: Callable[[ItemClient], agent.Harness],
        root: str | Path, model_identity: dict[str, str], *,
        allow_write: bool = False, allow_overwrite: bool = False,
        on_event: Callable[..., None] | None = None,
        reuse_patterns: bool = True,
    ) -> None:
        self.history: HistoryClient
        self.client: Client
        self.reviewer_factory: Callable[[ItemClient], agent.Harness]
        self.history, self.client, self.reviewer_factory = (
            history,
            client,
            reviewer_factory,
        )
        self.root: Path
        self.model_identity: dict[str, str]
        self.root, self.model_identity = Path(root), model_identity
        self.allow_write: bool
        self.allow_overwrite: bool
        self.allow_write, self.allow_overwrite = allow_write, allow_overwrite
        self.on_event: Callable[..., None] = on_event or (
            lambda *args, **kwargs: None
        )
        self.last_summary: dict[str, object] | None = None
        self.last_job_id: str | None = None
        self.reuse_patterns: bool = reuse_patterns

    async def read(
        self, action: str, args: dict[str, object],
    ) -> dict[str, object]:
        import asyncio

        return await asyncio.to_thread(self.history.execute, action, args)

    async def run(
        self, args: dict[str, object], *, write: bool = False,
    ) -> dict[str, object]:
        self.last_job_id = None
        if not Draft202012Validator(BATCH_SCHEMA).is_valid(args):
            raise ChatError(
                "Invalid review_batch arguments; discover its schema first"
            )
        if write and not self.allow_write:
            raise ChatError("Batch writes require --allow-write")
        if args.get("overwrite") and not self.allow_overwrite:
            raise ChatError(
                "Replacing existing batch reviews requires --overwrite-reviews"
            )
        job: Journal = Journal(self.root, args.get("job_id"))
        self.last_job_id = job.id
        try:
            with job.lock():
                if not args.get("job_id"):
                    scope: dict[str, str | int] = {
                        key: args[key]
                        for key in ("project_id", "stream_id", "snapshot_id")
                    }
                    if args.get("base_snapshot_id"):
                        current: dict[str, str | int]
                        base: dict[str, str | int]
                        current, base = scopes(args)
                        subset: str = args.get("subset", "appeared")
                        job.set(
                            "comparison",
                            {
                                "current": current,
                                "base": base,
                                "subset": subset,
                            },
                        )
                        scope = base if subset == "disappeared" else current
                    job.set("scope", scope)
                    job.set("format", 1)
                    job.set("endpoint", self.history.endpoint)
                    job.set("model", self.model_identity)
                    job.set("write", write)
                    job.set("instructions", args.get("instructions", ""))
                    job.set("overwrite", args.get("overwrite", False))
                    job.set("checker_name", args.get("checker_name"))
                if job.get("format") != 1:
                    raise ChatError("Unsupported batch checkpoint format")
                if (
                    job.get("endpoint") != self.history.endpoint
                    or job.get("model") != self.model_identity
                ):
                    raise ChatError(
                        "Checkpoint belongs to a different history "
                        "endpoint/model"
                    )
                if job.get("write") != write:
                    if not write:
                        raise ChatError(
                            "This job saves reviews; enable live saving "
                            "to resume it"
                        )
                    with job.db:
                        job.db.execute(
                            "UPDATE items SET state='proposed' "
                            "WHERE state='reviewed'"
                        )
                        job.db.execute(
                            "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                            ("write", compact(True)),
                        )
                if job.get("overwrite") and not self.allow_overwrite:
                    raise ChatError(
                        "This checkpoint requires --overwrite-reviews"
                    )
                self.on_event("batch_started", job_id=job.id)
                if not job.get("queue_complete"):
                    await self.enumerate(job)
                statuses: dict[str, object] = await self.read(
                    "get_value_filter", {**job.get("scope"), "field": "status"}
                )
                ids: dict[str, int] = {
                    value["name"]: value["id"]
                    for value in statuses.get("classification", [])
                }
                if not set(STATUSES.values()) <= set(ids):
                    raise ChatError(
                        "History classification catalog is incomplete"
                    )
                placeholders: str = ",".join("?" for _ in FINISHED_ITEM_STATES)
                rows: list[sqlite3.Row] = job.db.execute(
                    "SELECT * FROM items WHERE state NOT IN "
                    f"({placeholders}) "
                    "ORDER BY checker COLLATE NOCASE,ordinal",
                    FINISHED_ITEM_STATES,
                ).fetchall()
                for row in rows:
                    await self.process(job, row, ids)
                self.last_summary = job.summary()
                self.on_event("batch_finished", summary=self.last_summary)
                return self.last_summary
        except (ChatError, HistoryError, OSError, sqlite3.Error) as error:
            self.last_summary = job.summary()
            raise ChatError(
                f"Batch stopped; job_id={job.id}. {error}"
            ) from None
        finally:
            self.last_summary = job.summary()
            job.db.close()

    async def enumerate(self, job: Journal) -> None:
        if job.get("comparison"):
            await self.enumerate_diff(job)
            return
        scope: dict[str, str | int] = job.get("scope")
        if job.get("checker_name"):
            checkers: list[str] = [job.get("checker_name")]
        else:
            catalog: dict[str, object] = await self.read(
                "get_value_filter", {**scope, "field": "warn_types"}
            )
            checkers = sorted(
                {
                    checker["checker_name"]
                    for group in catalog["checker_groups"]
                    for checker in group["checkers"]
                },
                key=str.casefold,
            )
        with job.db:
            job.db.execute("DELETE FROM items")
        ordinal: int = 0
        for checker in checkers:
            offset: int = 0
            while True:
                page: dict[str, object] = await self.read(
                    "agent_find_warnings",
                    {
                        **scope,
                        "checker_name": checker,
                        "offset": offset,
                        "limit": 100,
                    },
                )
                warnings: list[dict[str, object]] = page.get("warnings")
                if not isinstance(warnings, list):
                    raise ChatError("Invalid warning page")
                with job.db:
                    for warning in warnings:
                        if (
                            warning.get("checker_name", "").casefold()
                            != checker.casefold()
                        ):
                            raise ChatError(
                                "Warning page returned a different checker"
                            )
                        job.db.execute(
                            "INSERT INTO "
                            "items(warning_id,checker,ordinal,warning)"
                            " "
                            "VALUES(?,?,?,?)",
                            (
                                warning["warning_id"],
                                checker,
                                ordinal,
                                compact(warning),
                            ),
                        )
                        ordinal += 1
                self.on_event(
                    "batch_queue",
                    job_id=job.id,
                    total=ordinal,
                    checker=checker,
                )
                if not page.get("has_more"):
                    break
                next_offset: int = page.get("next_offset")
                if (
                    not warnings
                    or not isinstance(next_offset, int)
                    or next_offset <= offset
                ):
                    raise ChatError("Warning pagination did not advance")
                offset = next_offset
        job.set("queue_complete", True)

    async def enumerate_diff(self, job: Journal) -> None:
        comparison: dict[str, object] = job.get("comparison")
        values: dict[str, object] = {
            **comparison["current"],
            **{
                "base_" + key: value
                for key, value in comparison["base"].items()
            },
            "subset": comparison["subset"],
            "limit": 100,
        }
        if job.get("checker_name"):
            values["checker_name"] = job.get("checker_name")
        with job.db:
            job.db.execute("DELETE FROM items")
        offset: int
        ordinal: int
        offset = ordinal = 0
        while True:
            page: dict[str, object] = await self.read(
                COMPARE_ACTION, {**values, "offset": offset}
            )
            if (
                page.get("scope") != job.get("scope")
                or page.get("current") != comparison["current"]
                or page.get("base") != comparison["base"]
                or page.get("subset") != comparison["subset"]
            ):
                raise ChatError(
                    "Comparison page returned a different scope/subset"
                )
            warnings: list[dict[str, object]] = page.get("warnings")
            if not isinstance(warnings, list):
                raise ChatError("Invalid comparison warning page")
            with job.db:
                for warning in warnings:
                    checker: str = warning.get("checker_name")
                    if not isinstance(checker, str) or not checker:
                        raise ChatError(
                            "Comparison warning has no checker name"
                        )
                    if (
                        job.get("checker_name")
                        and checker.casefold()
                        != job.get("checker_name").casefold()
                    ):
                        raise ChatError(
                            "Comparison warning returned a different checker"
                        )
                    job.db.execute(
                        "INSERT INTO "
                        "items(warning_id,checker,ordinal,warning) "
                        "VALUES(?,?,?,?)",
                        (
                            warning["warning_id"],
                            checker,
                            ordinal,
                            compact(warning),
                        ),
                    )
                    ordinal += 1
            self.on_event(
                "batch_queue",
                job_id=job.id,
                total=ordinal,
                checker="diff: " + comparison["subset"],
            )
            if not page.get("has_more"):
                break
            next_offset: int = page.get("next_offset")
            if (
                not warnings
                or not isinstance(next_offset, int)
                or next_offset <= offset
            ):
                raise ChatError("Comparison pagination did not advance")
            offset = next_offset
        job.set("queue_complete", True)

    async def current(
        self, scope: dict[str, str | int], warning_id: str,
    ) -> dict[str, object]:
        result: dict[str, object] = await self.read(
            "get_warning",
            {**scope, "snapshot_id": "HEAD", "warning_id": warning_id},
        )
        warning: dict[str, object] = result.get("warning", {})
        if warning.get("warning_id") != warning_id:
            raise ChatError("Current warning identity mismatch")
        return review_attributes(warning)

    async def process(
        self, job: Journal, row: sqlite3.Row, ids: dict[str, int],
    ) -> None:
        warning_id: str
        checker: str
        scope: dict[str, str | int]
        warning_id, checker, scope = (
            row["warning_id"],
            row["checker"],
            job.get("scope"),
        )
        summary: dict[str, object] = job.summary()
        completed: int = sum(
            summary["states"].get(state, 0) for state in FINISHED_ITEM_STATES
        )
        self.on_event(
            "batch_progress",
            job_id=job.id,
            done=completed,
            total=summary["total"],
            checker=checker,
            warning_id=warning_id,
        )
        current: dict[str, object] = await self.current(scope, warning_id)
        if row["state"] in ("saving", "uncertain"):
            verdict: dict[str, object] = json.loads(row["verdict"])
            if self.matches(current, verdict, ids):
                job.update(warning_id, "saved")
                return
            raise ChatError(
                "A previous write has an uncertain outcome; inspect review "
                "history. It will not be retried."
            )
        if row["state"] == "proposed":
            baseline: dict[str, object]
            verdict, baseline = (
                json.loads(row["verdict"]),
                json.loads(row["baseline"]),
            )
            verdict["comment"] = review_comment(
                verdict["comment"], verdict["evidence"]
            )
            job.update(
                warning_id, "proposed", verdict=verdict, baseline=baseline
            )
        else:
            if not job.get("overwrite") and (
                current["classification_id"] != ids[STATUSES["UD"]]
                or current.get("comment")
            ):
                job.update(warning_id, "skipped")
                return
            baseline = current
            job.update(warning_id, "analyzing", baseline=baseline)
            verdict = await self.investigate(job, row, baseline)
            job.update(
                warning_id, "proposed", verdict=verdict, baseline=baseline
            )
            job.set("pending_response", None)
        if not job.get("write"):
            job.update(warning_id, "reviewed")
            if self.reuse_patterns:
                patterns.learn(job, checker, verdict)
            self.on_event(
                "batch_verdict",
                warning_id=warning_id,
                classification=verdict["classification"],
                comment=verdict["comment"],
                saved=False,
            )
            return
        if await self.current(scope, warning_id) != baseline:
            job.update(warning_id, "conflict")
            return
        job.update(warning_id, "saving")
        import asyncio

        try:
            await asyncio.to_thread(
                self.history.execute,
                "agent_set_review",
                {
                    "project_id": scope["project_id"],
                    "stream_id": scope["stream_id"],
                    "warning_id": warning_id,
                    "classification": verdict["classification"],
                    "comment": verdict["comment"],
                },
                write=True,
            )
            if not self.matches(
                await self.current(scope, warning_id), verdict, ids
            ):
                raise ChatError("Saved review could not be verified")
        except BaseException:
            job.update(warning_id, "uncertain")
            raise
        job.update(warning_id, "saved")
        if self.reuse_patterns:
            patterns.learn(job, checker, verdict)
        self.on_event(
            "batch_verdict",
            warning_id=warning_id,
            classification=verdict["classification"],
            comment=verdict["comment"],
            saved=True,
        )

    @staticmethod
    def matches(
        current: dict[str, object], verdict: dict[str, object],
        ids: dict[str, int],
    ) -> bool:
        return (
            current.get("classification_id")
            == ids[STATUSES[verdict["classification"]]]
            and current.get("comment") == verdict["comment"]
        )

    async def investigate(
        self, job: Journal, row: sqlite3.Row, baseline: dict[str, object],
    ) -> dict[str, object]:
        scope: dict[str, str | int]
        warning: dict[str, object]
        scope, warning = job.get("scope"), json.loads(row["warning"])
        cache: sqlite3.Row | None = job.db.execute(
            "SELECT value FROM checker_cache WHERE checker=?",
            (row["checker"],),
        ).fetchone()
        if cache:
            checker_info: dict[str, object] = json.loads(cache[0])
        else:
            try:
                checker_info = await self.read(
                    "agent_checker_info", {"checker_name": row["checker"]}
                )
            except HistoryError as error:
                if not str(error).startswith("Unknown checker:"):
                    raise
                checker_info = {
                    "checker_name": row["checker"],
                    "catalog_error": str(error),
                }
                try:
                    checker_info["documentation"] = await self.read(
                        "agent_checker_documentation",
                        {"checker_name": row["checker"]},
                    )
                except HistoryError as doc_error:
                    checker_info["documentation"] = {
                        "status": "unavailable",
                        "error": str(doc_error),
                    }
            with job.db:
                job.db.execute(
                    "INSERT INTO checker_cache VALUES(?,?)",
                    (row["checker"], compact(checker_info)),
                )
        evidence: set[tuple[str, int, str]] = set()
        item_client: ItemClient = ItemClient(
            self.client, {**scope, "checker_name": row["checker"]}, evidence
        )
        comparison: dict[str, object] | None = job.get("comparison")
        if comparison:
            for side in ("current", "base"):
                other: dict[str, str | int] = comparison[side]
                if (
                    other["project_id"] == scope["project_id"]
                    and other["stream_id"] == scope["stream_id"]
                ):
                    item_client.snapshots.add(other["snapshot_id"])
        reviewer: agent.Harness = self.reviewer_factory(item_client)
        # Never carry another warning or checker's conversation into this one.
        reviewer.reset()
        reviewer.set_result_schema(RESULT_SCHEMA)
        reviewer.messages[0]["content"] += " " + BATCH_INSTRUCTION
        reviewer.messages.append(
            {
                "role": "user",
                "content": (
                    "Review the single assigned warning using the fetched "
                    "data below."
                ),
            }
        )
        detail: dict[str, object] = await self.read(
            "get_warning", {**scope, "warning_id": row["warning_id"]}
        )
        if detail.get("warning", {}).get("warning_id") != row["warning_id"]:
            raise ChatError("Snapshot warning identity mismatch")
        packets: list[tuple[str, dict[str, object], dict[str, object]]] = [
            (
                "agent_checker_info",
                {"checker_name": row["checker"]},
                checker_info,
            ),
            (
                "get_warning",
                {**scope, "warning_id": row["warning_id"]},
                {**detail, "host_current_review": baseline},
            ),
        ]
        reviews_args: dict[str, str | int] = {
            "project_id": scope["project_id"],
            "stream_id": scope["stream_id"],
            "warning_id": row["warning_id"],
        }
        packets.append(
            (
                "get_all_reviews",
                reviews_args,
                await self.read("get_all_reviews", reviews_args),
            )
        )
        trace_args: dict[str, str | int] = {
            **scope,
            "warning_id": row["warning_id"],
        }
        trace: dict[str, object] = await self.read(
            "get_warning_trace_and_role", trace_args
        )
        packets.append(("get_warning_trace_and_role", trace_args, trace))
        item_client.observe("get_warning_trace_and_role", trace_args, trace)
        location: dict[str, object] = warning.get("location", {})
        if location.get("file_path") and isinstance(location.get("line"), int):
            source_args: dict[str, str | int] = {
                "project_id": scope["project_id"],
                "snapshot_id": location.get("snapshot_override")
                or scope["snapshot_id"],
                "file": location["file_path"],
                "line": location["line"],
                "before": 5,
                "after": 5,
            }
            item_client.snapshots.add(source_args["snapshot_id"])
            try:
                source: dict[str, object] = await self.read(
                    "agent_source", source_args
                )
                packets.append(("agent_source", source_args, source))
            except HistoryError as error:
                if error.auth_error or str(error).startswith(
                    "History request failed"
                ):
                    raise
                packets.append(
                    ("agent_source", source_args, {"error": str(error)})
                )
        for index, (action, arguments, data) in enumerate(packets):
            text: str = compact(data)
            if len(text.encode()) > reviewer.max_tool_result_bytes:
                text = compact(
                    {
                        "error": (
                            "Prefetched result exceeds tool budget; fetch "
                            "smaller relevant data with history_read."
                        )
                    }
                )
            else:
                item_client.observe(action, arguments, data)
            call_id: str = "batch-read-" + str(index)
            reviewer.messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "history_read",
                                    "arguments": compact(
                                        {
                                            "action": action,
                                            "arguments": arguments,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": call_id, "content": text},
                ]
            )
            self.on_event(
                "tool_prefetch",
                action=action,
                arguments=arguments,
                response=text,
            )
        prompt: str = (
            "Investigate the one warning identified above. "
            "Project/branch/snapshot: "
            + compact(scope)
            + "\nAdditional user instructions: "
            + job.get("instructions", "")
            + "\nFinish with submit_verdict."
        )
        if comparison:
            prompt += (
                "\nComparison selection: "
                + compact(comparison)
                + "\nAssess the warning on its evidence scope above. "
                "Disappeared warnings use baseline code; "
                "absence from the current snapshot proves neither FP nor a "
                "fix. "
                "Do not review warnings outside this selected difference."
            )
        hints: list[dict[str, str | list[str]]] = (
            patterns.recall(job, row["checker"]) if self.reuse_patterns else []
        )
        if hints:
            prompt += (
                "\nReusable hypotheses for this checker only (not evidence; "
                "verify on this warning): " + compact(hints)
            )
            self.on_event(
                "batch_patterns",
                count=len(hints),
                checker=row["checker"],
                patterns=hints,
            )
        pending: dict[str, object] | None = job.get("pending_response")
        if (
            isinstance(pending, dict)
            and pending.get("warning_id") == row["warning_id"]
            and pending.get("baseline") == baseline
            and pending.get("retry_investigation")
        ):
            prompt += (
                "\nThe previous verdict was rejected by host validation: "
                + compact(pending.get("validation_errors", []))
                + "\nInvestigate again. Cite only exact source addresses "
                "returned by agent_source. Read any additional cited lines "
                "before submitting. Do not reuse the rejected verdict."
            )
        if (
            isinstance(pending, dict)
            and pending.get("warning_id") == row["warning_id"]
            and pending.get("baseline") == baseline
            and not pending.get("retry_investigation")
        ):
            raw: str = pending["raw"]
            evidence.update(tuple(address) for address in pending["evidence"])
            reviewer.messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw},
                ]
            )
        else:
            raw = await reviewer.ask(prompt)
        original_raw: str = (
            pending.get("original_raw", pending["raw"])
            if isinstance(pending, dict)
            and pending.get("warning_id") == row["warning_id"]
            and pending.get("baseline") == baseline
            and not pending.get("retry_investigation")
            else raw
        )

        def retain_response(
            issues: list[str] | None = None, *,
            retry_investigation: bool = False,
        ) -> None:
            job.set(
                "pending_response",
                {
                    "warning_id": row["warning_id"],
                    "baseline": baseline,
                    "raw": raw,
                    "original_raw": original_raw,
                    "evidence": sorted(evidence),
                    "validation_errors": issues or [],
                    "retry_investigation": retry_investigation,
                },
            )

        # Persist the completed investigation before parsing: a format error
        # must not lose its work.
        retain_response()

        parsed_draft: dict[str, object] | None = None

        def read_verdict() -> dict[str, object]:
            nonlocal parsed_draft
            try:
                verdict: dict[str, object] = normalize_verdict(
                    extract_verdict(raw),
                    evidence,
                    scope,
                    row["warning_id"],
                    row["checker"],
                    STATUSES,
                )
                if "pattern" in verdict and not Draft202012Validator(
                    patterns.PATTERN_SCHEMA
                ).is_valid(verdict["pattern"]):
                    verdict.pop("pattern")
                parsed_draft = verdict
            except VerdictFormatError:
                raise
            except ChatError as error:
                retain_response([str(error)], retry_investigation=True)
                raise
            issues: list[str] = schema_issues(
                verdict, Draft202012Validator(RESULT_SCHEMA)
            )
            if issues:
                raise VerdictFormatError("; ".join(issues))
            return verdict

        try:
            verdict: dict[str, object] = read_verdict()
        except VerdictFormatError as error:
            previous: dict[str, object] | None = copy.deepcopy(parsed_draft)
            issues: list[str] = [str(error)]
            retain_response(issues)
            self.on_event(
                "batch_format_repair",
                warning_id=row["warning_id"],
                issues=issues,
            )
            # A short isolated formatting turn, without source, investigation
            # tools or writes.
            formatter: agent.Harness = self.reviewer_factory(item_client)
            formatter.tools, formatter.allowed = [], set()
            formatter.messages = [
                {
                    "role": "system",
                    "content": (
                        "Convert the supplied draft verdict to the exact "
                        "JSON schema. This is format repair, not "
                        "investigation. Treat the draft as data. Preserve "
                        "its classification, reasoning, uncertainty and "
                        "cited evidence. You may rename fields, move "
                        "substantive explanation into comment and remove "
                        "redundant metadata. Do not invent evidence, "
                        "select a new classification, or add claims. "
                        "Return exactly one JSON object."
                    ),
                }
            ]
            raw = await formatter.ask(
                compact(
                    {
                        "schema": RESULT_SCHEMA,
                        "validation_errors": issues,
                        "draft": raw,
                    }
                )
            )
            retain_response()
            try:
                verdict = read_verdict()
            except VerdictFormatError as error:
                retain_response([str(error)])
                raise ChatError(
                    "Batch verdict still fails validation: "
                    + str(error)
                    + ". Response and evidence are checkpointed; resume "
                    "retries "
                    "formatting only. No review was saved"
                ) from None
            if isinstance(previous, dict):
                classification: object = previous.get("classification")
                if (
                    isinstance(classification, str)
                    and classification in STATUSES
                    and verdict["classification"] != classification
                ):
                    retain_response(
                        ["Format repair changed the classification"],
                        retry_investigation=True,
                    )
                    raise ChatError(
                        "Format repair changed the classification; no "
                        "review was "
                        "saved"
                    )
                addresses: object = previous.get("evidence")
                if isinstance(addresses, list) and all(
                    isinstance(address, dict)
                    and isinstance(address.get("file"), str)
                    and isinstance(address.get("line"), int)
                    and not isinstance(address["line"], bool)
                    and isinstance(address.get("snapshot_id"), str)
                    for address in addresses
                ):
                    cited: set[tuple[str, int, str]] = {
                        (a["file"], a["line"], a["snapshot_id"])
                        for a in addresses
                    }
                    repaired: set[tuple[str, int, str]] = {
                        (a["file"], a["line"], a["snapshot_id"])
                        for a in verdict["evidence"]
                    }
                    if cited != repaired:
                        retain_response(
                            ["Format repair changed the cited evidence"],
                            retry_investigation=True,
                        )
                        raise ChatError(
                            "Format repair changed the cited evidence;"
                            " no review was "
                            "saved"
                        )
        if (
            verdict["classification"] in EVIDENCE_REQUIRED
            and not verdict["evidence"]
        ):
            retain_response(
                ["TP/FP/WF verdict has no source evidence"],
                retry_investigation=True,
            )
            raise ChatError(
                "TP/FP/WF verdict has no source evidence; no review was saved"
            )
        for address in verdict["evidence"]:
            if (
                address["file"],
                address["line"],
                address["snapshot_id"],
            ) not in evidence:
                retain_response(
                    [
                        "Verdict cited source that was not actually read: "
                        + compact(address)
                    ],
                    retry_investigation=True,
                )
                raise ChatError(
                    "Verdict cited source that was not actually read; "
                    "no review "
                    "was saved"
                )
        verdict["comment"] = review_comment(
            verdict["comment"], verdict["evidence"], item_client.snapshots
        )
        if not verdict["comment"]:
            raise ChatError(
                "Review explanation is empty after removing redundant metadata"
            )
        return verdict


class BatchRouter:
    """
    Keep the three-tool interface; discover/execute the local action
    lazily.
    """

    def __init__(self, client: Client, runner: BatchRunner) -> None:
        self.client: Client
        self.runner: BatchRunner
        self.client, self.runner = client, runner
        self.completed: dict[str, object] | None = None
        self.user_request: str = ""

    async def call_tool(
        self, name: str, arguments: dict[str, object],
    ) -> CallToolResult:
        action: str | None = arguments.get("action")
        if action in (BATCH_ACTION, DIFF_ACTION):
            if name == "history_discover":
                return reply(
                    DIFF_META if action == DIFF_ACTION else BATCH_META
                )
            if name not in ("history_read", "history_write"):
                raise ChatError("Unsupported batch tool")
            raw: dict[str, object] = arguments.get("arguments", {})
            if not isinstance(raw, dict):
                raise ChatError("review_batch arguments must be an object")
            if action == DIFF_ACTION and not Draft202012Validator(
                DIFF_SCHEMA
            ).is_valid(raw):
                raise ChatError(
                    "Invalid review_diff arguments; specify current/base "
                    "snapshots or only job_id to resume"
                )
            values: dict[str, object] = dict(raw)
            if (
                action == BATCH_ACTION
                and "job_id" not in values
                and any(
                    key not in values
                    for key in ("project_id", "stream_id", "snapshot_id")
                )
            ):
                if find_history_urls(self.user_request):
                    raise ChatError(
                        "A history URL was provided: use the resolved "
                        "link scope, not"
                        " current defaults"
                    )
                import asyncio

                from history_agent.history.current import resolve_current

                defaults: dict[str, object] = await asyncio.to_thread(
                    resolve_current,
                    self.runner.history,
                    {
                        key: values[key]
                        for key in ("project_id", "stream_id")
                        if key in values
                    },
                    HistoryError,
                )
                values = {**defaults["scope"], **values}
            if "job_id" not in values and self.user_request:
                instructions: str = values.get("instructions", "")
                values["instructions"] = self.user_request + (
                    "\nRetained project constraints: " + instructions
                    if instructions and instructions != self.user_request
                    else ""
                )
            result: dict[str, object] | CallToolResult = await self.runner.run(
                values,
                write=self.runner.allow_write or name == "history_write",
            )
            self.completed = result
            return reply(result)
        result = await self.client.call_tool(name, arguments)
        if name == "history_discover" and not action and not result.is_error:
            data: dict[str, object] = json.loads(
                "".join(
                    part.text for part in result.content if part.type == "text"
                )
            )
            data["actions"].append(
                {
                    "name": BATCH_ACTION,
                    "description": BATCH_META["description"],
                    "tool": "history_read/history_write",
                }
            )
            data["actions"].append(
                {
                    "name": DIFF_ACTION,
                    "description": DIFF_META["description"],
                    "tool": "history_read/history_write",
                }
            )
            return reply(data)
        return result
