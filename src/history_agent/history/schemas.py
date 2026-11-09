"""Public action metadata and adapter permissions."""

CURRENT_ACTION: str = "current"
CURRENT_META: dict[str, object] = {
    "name": CURRENT_ACTION,
    "description": (
        "Resolve a default concrete review scope without asking the "
        "model to search projects, "
        "branches or snapshots. Projects and branches sort by name; "
        "first returned snapshot wins. "
        "Supply project_id, project_name or stream_id to narrow the "
        "selection. URL links take precedence."
    ),
    "read_only": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_id": {"type": "integer", "minimum": 0},
            "project_name": {"type": "string", "minLength": 1},
            "stream_id": {"type": "string", "minLength": 1},
        },
    },
}


URL_ACTION: str = "agent_resolve_history_url"
URL_META: dict[str, object] = {
    "name": URL_ACTION,
    "read_only": True,
    "description": "Resolve a SvaceLight comparison/project/warning URL to "
    "explicit project IDs, branches, "
    "concrete snapshots and ready read/review arguments. No "
    "reviews are saved. "
    "Comparison defaults to appeared (First); preserves Second/Both from URL.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 16384}
        },
    },
}


COMPARE_ACTION: str = "agent_compare_warnings"
COMPARE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project_id", "stream_id", "snapshot_id", "base_snapshot_id"],
    "properties": {
        "project_id": {"type": "integer", "minimum": 0},
        "stream_id": {"type": "string", "minLength": 1},
        "snapshot_id": {
            "type": "string",
            "minLength": 1,
            "not": {"const": "HEAD"},
        },
        "base_project_id": {"type": "integer", "minimum": 0},
        "base_stream_id": {"type": "string", "minLength": 1},
        "base_snapshot_id": {
            "type": "string",
            "minLength": 1,
            "not": {"const": "HEAD"},
        },
        "subset": {
            "enum": ["appeared", "disappeared", "both"],
            "type": "string",
            "default": "appeared",
        },
        "checker_name": {"type": "string", "minLength": 1},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
        },
    },
}
COMPARE_META: dict[str, object] = {
    "name": COMPARE_ACTION,
    "read_only": True,
    "input_schema": COMPARE_SCHEMA,
    "description": "Compare current and base snapshots with the UI Svace "
    "comparator. "
    "appeared=current only (UI First), disappeared=base only (UI "
    "Second), both=matched current warnings. "
    "Default appeared. Returns paginated warnings and their "
    "evidence/review scope. "
    "Disappearance alone does not establish FP or a fix.",
}


DOC_ACTION: str = "agent_checker_documentation"
DOC_METADATA: dict[str, object] = {
    "name": DOC_ACTION,
    "description": (
        "Read the exact checker's description and examples from this "
        "history server's /guide. Paginate long articles."
    ),
    "read_only": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["checker_name"],
        "properties": {
            "checker_name": {"type": "string", "minLength": 1},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8000,
                "default": 3000,
            },
        },
    },
}

READ_ACTIONS: frozenset[str] = frozenset(
    {
        "whoami",
        "get_projects",
        "get_project_group_info",
        "get_project_info",
        "branches",
        "get_snapshots",
        "get_warnings",
        "get_warning",
        "get_warning_trace",
        "get_warning_trace_and_role",
        "get_all_reviews",
        "get_file_tree",
        "get_tokens",
        "get_ref_tokens",
        "get_value_filter",
        "agent_source",
        "agent_resolve_symbol",
        "agent_find_warnings",
        "agent_checker_info",
        DOC_ACTION,
        COMPARE_ACTION,
        URL_ACTION,
        CURRENT_ACTION,
    }
)

WRITE_ACTIONS: frozenset[str] = frozenset({"agent_set_review"})

MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024
