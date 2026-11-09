"""Schemas and public metadata for review tools."""

import copy

from history_agent.history.schemas import COMPARE_SCHEMA
from history_agent.review.models import ReviewVerdict

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
RESULT_SCHEMA: dict[str, object] = ReviewVerdict.model_json_schema()
