"""Descriptions exposed by local actions."""

CURRENT_DESCRIPTION: str = (
    "Resolve a default concrete review scope without asking the model to "
    "search projects, branches or snapshots. Projects and branches sort "
    "by name; first returned snapshot wins. Supply project_id, "
    "project_name or stream_id to narrow the selection. URL links take "
    "precedence."
)

URL_DESCRIPTION: str = (
    "Resolve a SvaceLight comparison/project/warning URL to explicit "
    "project IDs, branches, concrete snapshots and ready read/review "
    "arguments. No reviews are saved. Comparison defaults to appeared "
    "(First); preserves Second/Both from URL."
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
