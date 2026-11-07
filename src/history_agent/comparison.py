"""
Explicit current/base comparison, including a bridge for older history
servers.
"""

import copy

COMPARE_ACTION = "agent_compare_warnings"
COMPARE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project_id", "stream_id", "snapshot_id", "base_snapshot_id"],
    "properties": {
        "project_id": {"type": "integer", "minimum": 0},
        "stream_id": {"type": "string", "minLength": 1},
        "snapshot_id": {
            "type": "string",
            "minLength": 1,
            "not": {"const": "HEAD"},
        },
        "base_project_id": {"type": "integer", "minimum": 0},
        "base_stream_id": {"type": "string", "minLength": 1},
        "base_snapshot_id": {
            "type": "string",
            "minLength": 1,
            "not": {"const": "HEAD"},
        },
        "subset": {
            "enum": ["appeared", "disappeared", "both"],
            "type": "string",
            "default": "appeared",
        },
        "checker_name": {"type": "string", "minLength": 1},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
        },
    },
}
COMPARE_META = {
    "name": COMPARE_ACTION,
    "read_only": True,
    "input_schema": COMPARE_SCHEMA,
    "description": "Compare current and base snapshots with the UI Svace "
    "comparator. "
    "appeared=current only (UI First), disappeared=base only (UI "
    "Second), both=matched current warnings. "
    "Default appeared. Returns paginated warnings and their "
    "evidence/review scope. "
    "Disappearance alone does not establish FP or a fix.",
}


def scopes(values):
    current = {
        key: values[key] for key in ("project_id", "stream_id", "snapshot_id")
    }
    base = {
        "project_id": values.get("base_project_id", current["project_id"]),
        "stream_id": values.get("base_stream_id", current["stream_id"]),
        "snapshot_id": values["base_snapshot_id"],
    }
    return current, base


def legacy_compare(client, values, error_type):
    """
    Legacy HTTP firstOnly is UI Second: performComparison reverses its
    inputs.
    """
    current, base = scopes(values)
    query = {key + "_1": value for key, value in current.items()}
    query.update({key + "_2": value for key, value in base.items()})
    result = client._call("get_warnings_compare_svace2svace", query)
    if any(
        not isinstance(result.get(key), list)
        for key in ("firstOnly", "secondOnly", "common")
    ):
        raise error_type("Invalid legacy comparison response")
    catalogs = {}

    def normalize(warning, scope):
        data = copy.deepcopy(warning)
        if not data.get("checker_name"):
            key = tuple(scope.items())
            if key not in catalogs:
                catalog = client._call(
                    "get_value_filter", {**scope, "field": "warn_types"}
                )
                catalogs[key] = {
                    str(checker["checker_id"]): checker["checker_name"]
                    for group in catalog.get("checker_groups", [])
                    for checker in group["checkers"]
                }
            name = catalogs[key].get(str(data.get("checker_id")))
            if not name:
                raise error_type(
                    "Comparison warning has no resolvable checker name"
                )
            data["checker_name"] = name
        if (
            not isinstance(data.get("warning_id"), str)
            or not data["warning_id"]
        ):
            raise error_type("Comparison warning has no history identity")
        return data

    subset = values["subset"]
    scope = base if subset == "disappeared" else current
    selected = {}
    if subset == "both":
        for pair in result["common"]:
            # Legacy pair.second is the current warning, pair.first is
            # baseline.
            data = normalize(pair["second"], current)
            entry = selected.setdefault(data["warning_id"], data)
            entry.setdefault("counterparts", []).append(
                {
                    "warning": normalize(pair["first"], base),
                    "kind": pair["kind"],
                }
            )
        for entry in selected.values():
            entry["counterparts"].sort(
                key=lambda match: match["warning"]["warning_id"]
            )
    else:
        for warning in result[
            "secondOnly" if subset == "appeared" else "firstOnly"
        ]:
            data = normalize(warning, scope)
            selected[data["warning_id"]] = data
    checker = values.get("checker_name")
    warnings = [
        data
        for data in selected.values()
        if not checker or data["checker_name"].casefold() == checker.casefold()
    ]
    warnings.sort(
        key=lambda data: (
            data["checker_name"],
            data["location"]["file_path"],
            data["location"]["line"],
            data["warning_id"],
        )
    )
    offset, limit = values["offset"], values["limit"]
    end = min(len(warnings), offset + limit)
    page = {
        "current": current,
        "base": base,
        "scope": scope,
        "subset": subset,
        "warnings": warnings[offset:end],
        "total": len(warnings),
        "offset": offset,
        "limit": limit,
        "has_more": end < len(warnings),
    }
    if page["has_more"]:
        page["next_offset"] = end
    return page
