# History API for review agents

## Model names and HTTP identities

The harness and standalone MCP server expose session names such as `diana`,
`max` and `chloe` in all `*_id`, `snapshot_override`, `hash`, `mti` and
`token_group` fields. Model schemas use strings for these names, including
numeric HTTP project IDs. Copy names from tool results exactly. Real IDs,
credentials, integer conversion and scope validation stay in the host.
`HEAD` remains a supported snapshot selector. Source paths, source text,
checker names, CWE numbers and line/column offsets retain their meaning.
Names survive reload within a session; a new session resolves scope again.

The contract below describes HTTP transport; the host translates model names
to its raw IDs before sending requests. Examples are model-facing arguments
with names already returned by tools, never names to invent.

In `history-chat`, tool errors trigger up to two context reloads with explicit error
feedback. Correct arguments or use smaller pages. Re-read missing schemas/source as
needed; do not replay successful writes. An uncertain write disables tools for the feedback response; inspect
review history before any later retry.

These actions extend `/history/api?act=...`. Existing actions and response shapes
are unchanged. `get_actions` includes all actions. `actions_agent` provides input
JSON Schemas, parameter placement, read/write flags, `session_required` and
per-parameter `constraints`. `actions_userfriendly` provides descriptions and
human-readable parameter help without schemas. The Python adapter prefers
`actions_agent` and uses `agent_actions` when only that older name is advertised.
`history_discover(action=...)` exposes the selected schema and available session
and constraint metadata. Numeric bounds and defaults come from the server.
Extension responses are `{"status":"OK","data":...}`; errors use the existing
HTTP 400 / `status: ERROR` envelope. The catalog declares which actions require
a valid history session. Configured credentials/session are handled by the
adapter's transport.

| Action | Purpose |
|---|---|
| `agent_find_warnings` | Filter active warning instances by checker name |
| `agent_compare_warnings` | Page appeared, disappeared or matched warnings between two snapshots |
| `agent_checker_info` | Get catalog metadata for an exact checker name |
| `agent_source` | Read bounded, numbered source lines from a snapshot |
| `agent_resolve_symbol` | Follow DXR definition, declaration or usage links |
| `agent_set_review` | Save TP / FP / WF / UC / UD with an explanation |
| `actions_agent` | Discover extension schemas and constraints |
| `actions_userfriendly` | Describe extension actions and parameters |

## Find warnings by checker name

GET parameters: required `project_id`, `stream_id`, `snapshot_id`,
`checker_name`; optional `match` (`exact`, default, or `contains`), `offset`
(default 0) and `limit` (default 20, maximum 100). `snapshot_id` accepts an ID
or `HEAD` for the latest snapshot in the explicit branch. Pin a concrete ID
before paging or investigating warnings so later reads use the same analysis.
Name matching ignores case;
names are checker types such as `DEREF_OF_NULL`, not warning IDs or message text.

Filtering happens before pagination. Results are ordered by source file, line
and warning ID. The response includes `warnings`, `total`, `offset`, `limit`,
`has_more` and, when more results exist, `next_offset`. Each warning includes
`warning_id`, `checker_name`, `message`, `function_name`, `mti`, `details`,
`location`, `classification_name`, `comment`, `triaged_status` and
`user_attributes`. Location includes `file_path`, `line`, `hash` and optional
`snapshot_override`. Follow the override when reading its source.

Use the returned ID with existing `get_warning`, `get_warning_trace_and_role`
and `get_all_reviews` for full instance information. An empty result is valid,
including for an unknown checker name. Project read permissions and the normal
active-warning selection are enforced by the existing API.

Example arguments:

```json
{"project_id":"diana","stream_id":"max","snapshot_id":"chloe",
 "checker_name":"DEREF_OF_NULL","match":"exact","offset":0,"limit":20}
```

## Compare snapshots and select a review queue

`agent_compare_warnings` accepts GET `project_id`, `stream_id`, `snapshot_id`
(current) and `base_snapshot_id`. Optional `base_project_id` / `base_stream_id`
default to the current side. Either snapshot can be `HEAD` for the latest
snapshot in that side's project and branch. The returned `current` and `base`
identify the resolved snapshots; use concrete IDs for subsequent pages.
`subset` defaults to `appeared`; alternatives are `disappeared` and `both`.
Optional `checker_name` is an exact, case-insensitive filter before pagination.
`offset` defaults to 0; `limit` defaults to 20 and is at most 100.

The matching algorithm is the existing Svace2Svace UI comparator, including
renames, rather than subtraction of warning IDs. Current-only is UI First
(`appeared`); baseline-only is UI Second (`disappeared`). Matched current
warnings (`both`) contain `counterparts` with baseline warning data and matching
`kind` (`SAME`/`RENAME`). Each current warning occurs once even with multiple
counterparts. No chronological ordering is inferred; swapping sides reverses the
meaning of appeared/disappeared.

The response contains `current`, `base`, `scope`, `subset`, `warnings`, `total`,
`offset`, `limit`, `has_more` and optional `next_offset`. Warning data follows
`agent_find_warnings`. The evidence/review `scope` is baseline for disappeared,
current otherwise. Disappearance is not a classification and does not prove a fix.

On older servers the Python adapter exposes the same action by adapting
`get_warnings_compare_svace2svace`, correcting its reversed firstOnly/secondOnly
fields and resolving checker IDs. It downloads the complete legacy comparison
outside the model context before filtering/paging; the 2 MiB HTTP response limit
still applies. Rebuild the server for native pagination on large comparisons.
This legacy fallback requires concrete snapshot IDs.

The microharness additionally discovers local `review_diff` through its three
tools. It accepts the comparison scope, subset (default `appeared`), optional
`checker_name`, `instructions` and `overwrite`, or only `job_id` for resume.
Current and base snapshots may be IDs or `HEAD`; the host resolves `HEAD` through
`get_snapshots(number=1)` for each project/branch before creating the job. Review
queues and resumed jobs always use those pinned concrete snapshots.
`history_read` generates proposals; authorized `history_write` saves them.
Every warning gets a fresh context with its exact checker guide and source on
the selected side. Checkpoints pin both sides, subset, mode, endpoint and model;
finished warnings are not repeated on resume. These model-running actions are
local to `history-chat`; other MCP clients iterate the read comparison action
and save reviews with `agent_set_review`.

## Get checker information by name

`agent_checker_info` takes required GET parameter `checker_name`. Matching is
exact and case-insensitive. Unknown names are errors. The catalog comes from
the server's suppression settings and does not depend on a project's findings.

The response contains `checker_name`, `display_name`, `description`,
`description_source`, `severity`, `reliability`, `group`, `languages`, `cwe`
(numeric IDs) and `cert` (rule IDs). Descriptions use checker text when present,
otherwise group comments or the group title. `description_source` distinguishes
`checker`, `group` and `group_title`; a group title is not detailed checker help.
Deprecated or custom checkers absent from this server's catalog can still be
found by `agent_find_warnings`, but have no invented catalog description.

## Source, navigation and review

`agent_source` requires `project_id`, `snapshot_id`, `file` and one-based
`line`. `before` and `after` default to 10; the total window is at most 200 lines
and 65536 characters. The response identifies the source hash and encoding,
contains numbered `lines`, and indicates continuation through `has_more` /
`next_line`. Files are resolved from the selected snapshot's source tree.

`agent_resolve_symbol` adds one-based `column`, `relation` (`definition`,
`declaration`, `usage`), `offset` (default 0) and `limit` (default 20, max 100).
It requires DXR data. Overlapping tokens remain separate candidates; token group
IDs are strings and target pagination applies to each candidate independently.
The one-based `column` input differs from zero-based offsets in `get_tokens`.
`file` is a path in the snapshot, not a server filesystem path. Review source
and symbol reads use the concrete snapshot pinned for the warning, honoring
`snapshot_override` on each location.

`agent_set_review` requires GET `project_id`, explicit `stream_id`, `warning_id`
and POST `classification`, `comment`. A non-empty explanation is mandatory.
TP = Confirmed, FP = False Positive, WF = Won't fix, UC = Unclear, UD = Undecided.
Use TP when the detected issue holds and is relevant, FP when the reported condition does not
hold, and WF when the condition does hold but is not actionable in the project's context.
WF is not a false positive. A technically correct warning in an intentional test fixture or
accepted implementation can be WF when that rationale is supported by evidence.
Writes use the normal permission and history commit API and affect current
branch state. They have no conditional-write protection; inspect existing
reviews before overwriting and inspect history before retrying an uncertain
write. The legacy review API can materialize an unset `trust_rank` as zero;
this extension does not change that existing behavior.

MCP adapters and model/client setup are maintained outside the Svace repository.

## Guide articles in the Python adapter

`agent_resolve_history_url` is another adapter-local read action. Its required
`url` is a SvaceLight `show` or `compare_svace2svace` link from the configured
history origin and deployment prefix. It decodes route segments, resolves
project names through project groups and pins HEAD through `get_snapshots`.
It returns `current`, optional `base` / `subset`, evidence/review `scope`,
ready `read_action` / `read_arguments` and optional local `review_action` /
`review_arguments`. Local review actions require the microharness; other MCP
clients use the read action and their own review loop. No web page is fetched
and no reviews are saved. A missing comparison subset defaults to appeared.
The selected warning/source location is returned separately as a UI cursor.
An unknown project or ambiguous name is an error rather than a guessed ID.

`history-chat` resolves links from the user's request before the first model
completion and supplies compact tool results with these arguments. Resolved
links do not authorize review writes; the user's request and `--allow-write`
still control saving.

The adapter enriches `agent_checker_info` with `documentation` from the connected
server's `/guide`: `status`, `checker_name`, `guide_title`, `source_url`, `text`,
`offset`, `total_characters`, `has_more` and optional `next_offset`. The original
catalog description remains unchanged. Missing guide/articles are explicitly
reported as `documentation.status: unavailable` with an error explanation.

`agent_checker_documentation` is an adapter-local read action, not an HTTP API
route. It accepts `checker_name`, `offset` (default 0), and `limit` (default 3000,
max 8000 characters). It returns only the exact checker article, including
examples, never a parent rule or neighboring checker as a substitute. The full
guide is indexed and cached in Python for five minutes, outside model context;
each reply contains only the requested article/page. Deployment prefixes and
the configured history origin are preserved; external links are not fetched.

## Adapter-local `current` defaults and model skills

`history_current(project_id?, project_name?, stream_id?)` (a dedicated MCP tool)
and `history_read(action="current",arguments={...})` return `scope` containing
`project_id`, `stream_id`, and concrete `snapshot_id`, plus `project_name` and
`selection_policy`. The adapter enumerates project groups once, sorts project
names and branches alphabetically, and uses the **first** snapshot returned by
`get_snapshots(number=1)`. It never selects `HEAD` or guesses an unavailable
project; a missing branch/snapshot is an error. A user-supplied SvaceLight URL
always overrides this default and must be resolved through the link action.

Four task-specific skills can be listed with `history_discover(action="skills")`:
`history-current`, `history-review`, `history-batch`, `history-navigation`.
Read only the needed file with `history_discover(action="skill:NAME")`. The
legacy `history://workflow` resource remains available for older clients.
