# Diagnosing a review

Each chat launch writes private JSONL diagnostics under
`<batch-state-dir>/logs/`. With the default settings this is
`~/.local/state/history-agent/jobs/logs/`. Terminal mode prints the exact path
at startup. The UI keeps its welcome screen until the user starts a chat;
the log path is recorded in the file's `session_started` event.

The log records model parameters (including output limit and streaming flags),
messages and tool definitions, completed model responses,
tool names and arguments, returned data and errors, reload notices, and batch
progress. Worker events include the real checkpoint `job_id` so a stopped
warning can be traced across requests. Streaming fragments are omitted because
the completed responses are recorded. These files contain the source being
reviewed; access is restricted to the current user. Configured model/history
credentials and credential fields are redacted. No authorization headers or
configuration documents are recorded.

Each session keeps its current file and two rotated files, at most 10 MiB each.
An individual record larger than that limit is replaced by an omission marker.
Logging failures disable the file logger and report a warning without blocking
the review.

The SQLite checkpoint holds queue state, verdicts and timing; it cannot recreate
an old conversation from a run that did not have file logging. Terminal output
from those runs is available only while retained by the terminal application.

The harness embeds the adapter's supported action names into the tool schemas
before the first model request, without waiting for a server connection. A
successful catalog discovery narrows these names to the actions available on
that server. Invented names are rejected locally with the allowed actions and
source-navigation guidance.
Per-warning workers cannot select a different default project, start a nested
review job, or save reviews. After an error, reload keeps complete successful
tool exchanges, including source windows and discovered schemas, within the
context budget. It remembers recent failures, preserves host-provided scope,
and drops unexecuted calls from partially completed tool batches.
