"""Compare explicit snapshots and normalize legacy warning records."""

from __future__ import annotations

import copy
from collections.abc import Callable


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
    return (current, base)


def legacy_compare(
    call: Callable[..., dict[str, object]], values: dict[str, str | int],
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
    result: dict[str, object] = call("get_warnings_compare_svace2svace", query)
    if any(
        (
            not isinstance(result.get(key), list)
            for key in ("firstOnly", "secondOnly", "common")
        )
    ):
        raise error_type("Invalid legacy comparison response")
    subset: str = values["subset"]
    scope: dict[str, str | int] = base if subset == "disappeared" else current
    normalizer: WarningNormalizer = WarningNormalizer(call, error_type)
    selected: list[dict[str, object]] = normalizer.select(
        result, current, base, subset
    )
    checker: str | None = values.get("checker_name")
    warnings: list[dict[str, object]] = [
        data
        for data in selected
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
    offset, limit = (values["offset"], values["limit"])
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


class WarningNormalizer:
    def __init__(
        self, call: Callable[..., dict[str, object]],
        error_type: type[Exception],
    ) -> None:
        self.call: Callable[..., dict[str, object]] = call
        self.error_type: type[Exception] = error_type
        self.catalogs: dict[
            tuple[tuple[str, str | int], ...], dict[str, str]
        ] = {}

    def normalize(
        self, warning: dict[str, object], scope: dict[str, str | int],
    ) -> dict[str, object]:
        data: dict[str, object] = copy.deepcopy(warning)
        if not data.get("checker_name"):
            key: tuple[tuple[str, str | int], ...] = tuple(scope.items())
            if key not in self.catalogs:
                catalog: dict[str, object] = self.call(
                    "get_value_filter", {**scope, "field": "warn_types"}
                )
                self.catalogs[key] = {
                    str(checker["checker_id"]): checker["checker_name"]
                    for group in catalog.get("checker_groups", [])
                    for checker in group["checkers"]
                }
            name: str | None = self.catalogs[key].get(
                str(data.get("checker_id"))
            )
            if not name:
                raise self.error_type(
                    ("Comparison warning has no resolvable checker name")
                )
            data["checker_name"] = name
        if (
            not isinstance(data.get("warning_id"), str)
            or not data["warning_id"]
        ):
            raise self.error_type("Comparison warning has no history identity")
        return data

    def select(
        self, result: dict[str, object], current: dict[str, str | int],
        base: dict[str, str | int], subset: str,
    ) -> list[dict[str, object]]:
        scope: dict[str, str | int] = (
            base if subset == "disappeared" else current
        )
        selected: dict[str, dict[str, object]] = {}
        if subset == "both":
            for pair in result["common"]:
                data: dict[str, object] = self.normalize(
                    pair["second"], current
                )
                entry: dict[str, object] = selected.setdefault(
                    data["warning_id"], data
                )
                entry.setdefault("counterparts", []).append(
                    {
                        "warning": self.normalize(pair["first"], base),
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
                data = self.normalize(warning, scope)
                selected[data["warning_id"]] = data
        return list(selected.values())
