"""Descriptions exposed by local actions."""

CURRENT_DESCRIPTION: str = (
    "Resolve a default concrete review scope without asking the model to "
    "search projects, branches or snapshots. Projects and branches sort "
    "by name; first returned snapshot wins. Supply project_id, "
    "project_name or stream_id to narrow the selection. URL links take "
    "precedence: history-chat returns the active link scope and rejects "
    "conflicting constraints."
)

URL_DESCRIPTION: str = (
    "Resolve a SvaceLight comparison/project/warning URL to explicit "
    "scope and ready read/review arguments. For comparison warnings, use "
    "warnings_action with warnings_arguments plus the user's checker_name. "
    "Comparison defaults to appeared (First); preserves Second/Both. "
    "display contains project/branch labels; identity fields use host names."
)

LINK_WARNINGS_DESCRIPTION: str = (
    "Fetch warnings of the user-named checker from a resolved comparison "
    "link. Copy link_id from warnings_arguments; add checker_name and "
    "optional offset/limit. The host keeps both snapshots and subset pinned. "
    "An empty result stays empty; never broaden it to the whole project. "
    "This action only lists warnings, without reviewing or saving them."
)

COMPARE_DESCRIPTION: str = (
    "Compare current and base snapshots with the UI Svace comparator. "
    "appeared=current only (UI First), disappeared=base only (UI "
    "Second), both=matched current warnings. Default appeared. Returns "
    "paginated warnings and their evidence/review scope. Disappearance "
    "alone does not establish FP or a fix."
)

DOC_DESCRIPTION: str = (
    "Read the exact checker's description and examples from this history "
    "server's /guide. Paginate long articles."
)
