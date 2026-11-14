"""Validate source and warning evidence against user-selected link scopes."""

from __future__ import annotations

import json

SCOPE_KEYS = ("project_id", "stream_id", "snapshot_id")
SOURCE_ACTIONS = frozenset(
    {
        "agent_source",
        "agent_resolve_symbol",
        "get_tokens",
        "get_ref_tokens",
        "get_file_tree",
    }
)


class LinkReads:
    def __init__(self) -> None:
        self.overrides: dict[tuple[object, object], set[str]] = {}

    def matches(self, args, scope, action):
        for key in SCOPE_KEYS:
            if key not in args or args[key] == scope[key]:
                continue
            if key == "snapshot_id":
                if args[key] == "HEAD" and action == "get_warning":
                    continue
                overrides = self.overrides.get(
                    (scope["project_id"], scope["stream_id"]),
                    set(),
                ) | self.overrides.get((scope["project_id"], None), set())
                if action in SOURCE_ACTIONS and args[key] in overrides:
                    continue
            return False
        return True

    def prepare(self, action, args, schema, links, reject):
        properties = schema.get("properties", {})
        scoped = set(properties) & set(SCOPE_KEYS)
        if not scoped:
            return args
        candidates = []
        for link in links:
            if link["kind"] == "comparison" and action in (
                "get_warnings",
                "agent_find_warnings",
            ):
                continue
            if link["kind"] == "warning" and (
                args.get("warning_id", link["selected_warning_id"])
                != link["selected_warning_id"]
                or action in ("get_warnings", "agent_find_warnings")
            ):
                continue
            # Prefer the effective evidence side when no side was specified.
            scopes = [link["scope"]]
            for side in ("current", "base"):
                if side in link and link[side] not in scopes:
                    scopes.append(link[side])
            for scope in scopes:
                if self.matches(args, scope, action):
                    values = {**{k: scope[k] for k in scoped}, **args}
                    if (
                        link["kind"] == "warning"
                        and "warning_id" in properties
                    ):
                        values.setdefault(
                            "warning_id",
                            link["selected_warning_id"],
                        )
                    if values not in candidates:
                        candidates.append(values)
                    break
        if len(candidates) != 1:
            reject(
                "Read left the active project/branch/snapshot, broadened the "
                "warning selection, or did not identify one link. Use the "
                "resolved warnings_action/read_action and its arguments"
            )
        return candidates[0]

    def observe(self, args, data):
        if "project_id" not in args:
            return
        key = (args["project_id"], args.get("stream_id"))

        def visit(value):
            if isinstance(value, dict):
                for field, entry in value.items():
                    if field == "snapshot_override" and isinstance(entry, str):
                        self.overrides.setdefault(key, set()).add(entry)
                    elif field in ("trace", "traces") and isinstance(
                        entry, str
                    ):
                        try:
                            visit(json.loads(entry))
                        except ValueError:
                            pass
                    else:
                        visit(entry)
            elif isinstance(value, list):
                for entry in value:
                    visit(entry)

        visit(data)
