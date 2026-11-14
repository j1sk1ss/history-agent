---
name: history-batch
description: Bulk review, resumability and write safeguards.
---

# Review a project

In model tool calls, `*_id`, `snapshot_override`, `hash`, `mti` and `token_group` contain host names such as `diana`, `max` and `chloe`. Copy returned names exactly; the host resolves real server IDs. Never invent names or submit raw IDs. `HEAD` remains a snapshot selector. Names remain valid across reloads in this session.

After a tool error the harness reloads context and supplies the error. Correct the failed request using its schema and existing names. Re-read missing schemas/source as needed; do not replay successful writes. If a write outcome is uncertain, inspect review history before retrying.

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
Return a short summary with saved/proposed/skipped/conflicts and the job name.

For comparisons use `review_diff`, always with explicit current/base scopes and subset.
Do not silently broaden an explicit difference to all warnings.
