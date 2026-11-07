---
name: history-current
description: Select a deterministic project, branch and snapshot for unscoped review requests.
---

# Resolve scope

For requests like "review", "annotate" or "classify the warnings" with no link or IDs, call
`history_current()` directly, or `history_read(action="current", arguments={})` after schema discovery.
Use its returned `scope` unchanged. The adapter selects the project name alphabetically,
then the branch alphabetically, then the first snapshot returned by `get_snapshots(number=1)`.
This is **not** a search for a newest/best snapshot. A single-project server may select project 0.

When a project ID/name or branch is explicitly supplied, pass it as a constraint; never override it.
If an actual SvaceLight URL is supplied, use `agent_resolve_history_url` and **do not** use
`current` as a fallback for that URL. Never guess a project ID when the adapter reports an error.

For an unspecified whole-project job, `review_batch` accepts `{}` and performs the
same default resolution in the host. The host is responsible for pinning a concrete snapshot.
