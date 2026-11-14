"""Separate immutable evidence from mutable review state in cache keys."""

import hashlib
import json

IMMUTABLE = frozenset(
    {
        "agent_source",
        "agent_resolve_symbol",
        "get_tokens",
        "get_ref_tokens",
        "get_file_tree",
        "get_warning_trace",
        "get_warning_trace_and_role",
    }
)
CATALOG = frozenset(
    {
        "get_actions",
        "actions_agent",
        "agent_actions",
        "agent_checker_info",
        "guide",
    }
)
MUTABLE = frozenset(
    {
        "get_projects",
        "get_project_group_info",
        "get_project_info",
        "branches",
        "get_warnings",
        "get_warning",
        "get_value_filter",
        "agent_find_warnings",
        "agent_compare_warnings",
        "get_warnings_compare_svace2svace",
    }
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def policy(action, query, settings):
    if action in CATALOG:
        return "static", 3600
    if action == "get_value_filter" and query.get("field") == "status":
        return "static", 3600
    if any(
        str(value).upper() == "HEAD"
        for key, value in query.items()
        if key.endswith("snapshot_id")
    ):
        return None
    if action in IMMUTABLE:
        if not query.get("snapshot_id"):
            return None
        return "static", settings.source_cache_ttl
    if action in MUTABLE:
        # Snapshot-less legacy reads implicitly refer to mutable HEAD state.
        if action in ("get_warning", "get_warnings") and not query.get(
            "snapshot_id",
        ):
            return None
        return "reviews", settings.cache_ttl
    # Authentication, writes, live review history and HEAD resolution bypass.
    return None
