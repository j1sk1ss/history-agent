"""
Explicit current/base comparison, including a bridge for older history
servers.
"""

from __future__ import annotations

import copy

from . import client as history_client

COMPARE_ACTION: str = "agent_compare_warnings"
COMPARE_SCHEMA: dict[str, object] = {
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
COMPARE_META: dict[str, object] = {
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


def scopes(
    values: dict[str, str | int],
) -> tuple[dict[str, str | int], dict[str, str | int]]:
    current: dict[str, str | int] = {
        key: values[key] for key in ("project_id", "stream_id", "snapshot_id")
    }
    base: dict[str, str | int] = {
        "project_id": values.get("base_project_id", current["project_id"]),
        "stream_id": values.get("base_stream_id", current["stream_id"]),
        "snapshot_id": values["base_snapshot_id"],
    }
    return current, base


def legacy_compare(
    client: history_client.HistoryClient, values: dict[str, str | int],
    error_type: type[Exception],
) -> dict[str, object]:
    """
    Legacy HTTP firstOnly is UI Second: performComparison reverses its
    inputs.
    """
    current: dict[str, str | int]
    base: dict[str, str | int]
    current, base = scopes(values)
    query: dict[str, str | int] = {
        key + "_1": value for key, value in current.items()
    }
    query.update({key + "_2": value for key, value in base.items()})
    result: dict[str, object] = client._call(
        "get_warnings_compare_svace2svace", query
    )
    if any(
        not isinstance(result.get(key), list)
        for key in ("firstOnly", "secondOnly", "common")
    ):
        raise error_type("Invalid legacy comparison response")
    catalogs: dict[tuple[tuple[str, str | int], ...], dict[str, str]] = {}

    def normalize(
        warning: dict[str, object], scope: dict[str, str | int],
    ) -> dict[str, object]:
        data: dict[str, object] = copy.deepcopy(warning)
        if not data.get("checker_name"):
            key: tuple[tuple[str, str | int], ...] = tuple(scope.items())
            if key not in catalogs:
                catalog: dict[str, object] = client._call(
                    "get_value_filter", {**scope, "field": "warn_types"}
                )
                catalogs[key] = {
                    str(checker["checker_id"]): checker["checker_name"]
                    for group in catalog.get("checker_groups", [])
                    for checker in group["checkers"]
                }
            name: str | None = catalogs[key].get(str(data.get("checker_id")))
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

    subset: str = values["subset"]
    scope: dict[str, str | int] = base if subset == "disappeared" else current
    selected: dict[str, dict[str, object]] = {}
    if subset == "both":
        for pair in result["common"]:
            # Legacy pair.second is the current warning, pair.first is
            # baseline.
            data: dict[str, object] = normalize(pair["second"], current)
            entry: dict[str, object] = selected.setdefault(
                data["warning_id"], data
            )
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
    checker: str | None = values.get("checker_name")
    warnings: list[dict[str, object]] = [
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
    offset: int
    limit: int
    offset, limit = values["offset"], values["limit"]
    end: int = min(len(warnings), offset + limit)
    page: dict[str, object] = {
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
