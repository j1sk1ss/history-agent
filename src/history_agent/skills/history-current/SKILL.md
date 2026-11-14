---
name: history-current
description: Select a deterministic project, branch and snapshot for unscoped review requests.
---

# Resolve scope

In model tool calls, `*_id`, `snapshot_override`, `hash`, `mti` and `token_group` contain host names such as `diana`, `max` and `chloe`. Copy returned names exactly; the host resolves real server IDs. Never invent names or submit raw IDs. `HEAD` remains a snapshot selector. Names remain valid across reloads in this session.

After a tool error the harness reloads context and supplies the error. Correct the failed request using its schema and existing names. Re-read missing schemas/source as needed; do not replay successful writes. If a write outcome is uncertain, inspect review history before retrying.

For requests like "review", "annotate" or "classify the warnings" with no link or IDs, call
`history_current()` directly, or `history_read(action="current", arguments={})` after schema discovery.
Use its returned `scope` unchanged. The adapter selects the project name alphabetically,
then the branch alphabetically, then the first snapshot returned by `get_snapshots(number=1)`.
This is **not** a search for a newest/best snapshot. The host handles local single-project server defaults.

When a project name or returned project/branch alias is supplied, pass it as a constraint; never override it.
If an actual SvaceLight URL is supplied, use `agent_resolve_history_url` and **do not** use
`current` as a fallback for that URL. Never guess a project ID when the adapter reports an error.

For an unspecified whole-project job, `review_batch` accepts `{}` and performs the
same default resolution in the host. The host is responsible for pinning a concrete snapshot.
