"""Local action names and adapter permissions."""

CURRENT_ACTION: str = "current"

URL_ACTION: str = "agent_resolve_history_url"

COMPARE_ACTION: str = "agent_compare_warnings"

DOC_ACTION: str = "agent_checker_documentation"

SCHEMA_CATALOG_ACTIONS: tuple[str, ...] = ("actions_agent", "agent_actions")

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
