---
name: history-batch
description: Bulk review, resumability and write safeguards.
---

# Review a project

For a request only to fetch/list warnings from a comparison URL, use the
resolved link's `warnings_action` with `warnings_arguments` plus the user's
checker name. Ask for the name if missing. A batch review is unnecessary for
listing; keep the selected subset even when the result is empty.

In model tool calls, `*_id`, `snapshot_override`, `hash`, `mti` and `token_group` contain host names such as `diana`, `max` and `chloe`. Copy returned names exactly; the host resolves real server IDs. Never invent names or submit raw IDs. `HEAD` remains a snapshot selector. Names remain valid across reloads in this session.

After a tool error the harness reloads context and supplies the error. The default is eight recoveries, configurable with `--max-tool-retries` or settings; recovery is separate from normal model steps. Correct the failed request using its schema and existing names. Re-read missing schemas/source as needed; do not replay successful writes. If a write outcome is uncertain, inspect review history before retrying.

User links pin the active selection in `history-chat`. Use `display` for readable
project/branch labels and returned host names for identity arguments. Jobs and
resumes outside the selected project/branch/snapshots/subset are rejected.
Do not replace a comparison job with a whole-project job. A new link replaces
the binding and clears old model context; `/reset` clears it. Multiple links
stay separate and require an unambiguous selection.

Discover `review_batch` through `history_discover(action="review_batch")`.
Snapshot arguments accept `HEAD`; the host pins it to a concrete snapshot before
creating the job, including both sides of a comparison.
With no user-specified project/snapshot and no link, call
`history_read(action="review_batch", arguments={})` to
propose reviews or `history_write(action="review_batch", arguments={})` to save when authorized.
The host uses `current`; it does not need model-driven project/snapshot discovery.
When a URL is supplied, pass its resolved `review_arguments` explicitly.
Bulk jobs follow the session's live-save setting even through `history_read`;
that tool name alone does not make a job a preview. Single-warning
`history_read` proposals never save. The host runs the entire queue internally
and returns a completed summary, not a first warning for the parent chat to
investigate. After a pre-write failure, use returned `resume_action` and
`resume_arguments` to continue the same job. Creating a job does not itself
mean any review was written. Uncertain writes still require history inspection.

Each warning gets an isolated context. Fetch exact checker documentation before evaluating
trace/source. Classify valid and actionable findings as TP, invalid detections as FP, and valid
but project-irrelevant or intentionally accepted findings as WF. A WF verdict must explain
both why the checker is correct and why a change is not warranted. TP, FP and WF require
actual source evidence. Do not infer WF from file names or severity alone.
The host only saves unreviewed UD warnings; existing classifications and review
comments are protected even if overwrite was requested. The host adds
`Bismark said:` to verdict comments. Use `job_id` to resume, never retry uncertain writes blindly.
Return a short summary with saved/proposed/skipped/conflicts and the job name.

For comparisons use `review_diff`, always with explicit current/base scopes and subset.
Do not silently broaden an explicit difference to all warnings.
