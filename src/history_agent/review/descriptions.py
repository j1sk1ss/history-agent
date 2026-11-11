"""Descriptions exposed by local actions."""

BATCH_DESCRIPTION: str = (
    "Review all warnings with fresh per-warning model context, grouped "
    "by checker. Missing project/branch/snapshot defaults to "
    "history_current (alphabetical project/branch, first snapshot); URL "
    "scopes must be passed explicitly. The session save setting controls "
    "immediate server writes, including when called through "
    "history_read. Resume with only job_id. Existing reviews are "
    "preserved by default. To investigate one warning, supply warning_id; "
    "this reads only that warning and records timing. Single-warning "
    "proposals through history_read never write; use history_write only "
    "when saving is requested."
)

DIFF_DESCRIPTION: str = (
    "Review only a snapshot difference in isolated resumable contexts. "
    "Requires current and base snapshots; default appeared (UI First). "
    "disappeared (UI Second) uses baseline source and baseline review "
    "branch. both uses current source. Disappearance is not proof of "
    "FP/fix. The session save setting controls immediate server writes, "
    "including when called through history_read. Resume with only "
    "job_id."
)
