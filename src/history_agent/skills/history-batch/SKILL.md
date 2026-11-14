---
name: history-batch
description: Bulk review, resumability and write safeguards.
---

# Review a project

Discover `review_batch` through `history_discover(action="review_batch")`.
Snapshot arguments accept `HEAD`; the host pins it to a concrete snapshot before
creating the job, including both sides of a comparison.
With no user-specified project/snapshot and no link, call
`history_read(action="review_batch", arguments={})` to
propose reviews or `history_write(action="review_batch", arguments={})` to save when authorized.
The host uses `current`; it does not need model-driven project/snapshot discovery.
When a URL is supplied, pass its resolved `review_arguments` explicitly.

Each warning gets an isolated context. Fetch exact checker documentation before evaluating
trace/source. Classify valid and actionable findings as TP, invalid detections as FP, and valid
but project-irrelevant or intentionally accepted findings as WF. A WF verdict must explain
both why the checker is correct and why a change is not warranted. TP, FP and WF require
actual source evidence. Do not infer WF from file names or severity alone. Preserve existing reviews unless `overwrite=true` was explicitly authorized
and CLI permissions allow it. Use `job_id` to resume, never retry uncertain writes blindly.
Return a short summary with saved/proposed/skipped/conflicts and the job ID.

For comparisons use `review_diff`, always with explicit current/base scopes and subset.
Do not silently broaden an explicit difference to all warnings.
