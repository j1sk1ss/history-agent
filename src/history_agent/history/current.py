"""Deterministic default scope for an unspecified history review request.

All selection stays inside the adapter. A supplied SvaceLight URL is resolved
separately and always takes precedence over this fallback.
"""

from __future__ import annotations

from history_agent.history import client as history_client

CURRENT_ACTION: str = "current"
CURRENT_META: dict[str, object] = {
    "name": CURRENT_ACTION,
    "description": (
        "Resolve a default concrete review scope without asking the "
        "model to search projects, "
        "branches or snapshots. Projects and branches sort by name; "
        "first returned snapshot wins. "
        "Supply project_id, project_name or stream_id to narrow the "
        "selection. URL links take precedence."
    ),
    "read_only": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_id": {"type": "integer", "minimum": 0},
            "project_name": {"type": "string", "minLength": 1},
            "stream_id": {"type": "string", "minLength": 1},
        },
    },
}


def _named_branch(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("stream_id", "branch_name", "name", "branch", "stream"):
            name: object = item.get(key)
            if isinstance(name, str) and name:
                return name
    return None


def resolve_current(
    client: history_client.HistoryClient, args: dict[str, str | int],
    error_type: type[Exception],
) -> dict[str, object]:
    if args.get("project_id") is not None and args.get("project_name"):
        raise error_type("Choose project_id or project_name, not both")
    groups: list[dict[str, object]] = client.execute("get_projects", {}).get(
        "project_groups", []
    )
    projects: list[dict[str, str | int]] = []
    if not isinstance(groups, list):
        raise error_type("History project catalog is invalid")
    for group in groups:
        if not isinstance(group, dict) or "project_group_id" not in group:
            continue
        data: dict[str, object] = client.execute(
            "get_project_group_info",
            {"project_group_id": group["project_group_id"]},
        )
        entries: list[str | dict[str, object]] | None = data.get(
            "project_group", {}
        ).get("projects", [])
        if not isinstance(entries, list):
            continue
        for project in entries:
            if (
                isinstance(project, dict)
                and type(project.get("project_id")) is int
            ):
                projects.append(
                    {
                        "project_id": project["project_id"],
                        "project_name": str(
                            project.get("project_name")
                            or project["project_id"]
                        ),
                    }
                )
    # A single-project deployment may have no project groups in the catalog.
    if not projects and not groups:
        projects = [{"project_id": 0, "project_name": "0"}]
    if "project_id" in args:
        projects = [
            item
            for item in projects
            if item["project_id"] == args["project_id"]
        ]
    if args.get("project_name"):
        projects = [
            item
            for item in projects
            if item["project_name"] == args["project_name"]
        ]
    if not projects:
        raise error_type(
            "No matching history project; choose a valid project or "
            "inspect server access"
        )
    projects.sort(
        key=lambda item: (
            item["project_name"].casefold(),
            item["project_name"],
            item["project_id"],
        )
    )
    project: dict[str, str | int] = projects[0]
    if "stream_id" in args:
        branch: str = args["stream_id"]
    else:
        info: dict[str, object] = client.execute(
            "branches", {"project_id": project["project_id"]}
        )
        entries = next(
            (
                info[key]
                for key in (
                    "branches",
                    "streams",
                    "branch_names",
                    "stream_ids",
                )
                if isinstance(info.get(key), list)
            ),
            None,
        )
        if entries is None:
            raise error_type(
                "History did not return branches for the selected project; "
                "specify stream_id"
            )
        names: list[str] = sorted(
            set(name for item in entries if (name := _named_branch(item))),
            key=lambda value: (value.casefold(), value),
        )
        if not names:
            raise error_type("No branches found; specify stream_id")
        branch = names[0]
    result: dict[str, object] = client.execute(
        "get_snapshots",
        {
            "project_id": project["project_id"],
            "stream_id": branch,
            "number": 1,
        },
    )
    snapshots: list[dict[str, object]] = result.get("snapshots", [])
    if (
        not isinstance(snapshots, list)
        or not snapshots
        or not isinstance(snapshots[0], dict)
    ):
        raise error_type("No snapshot in the selected project/branch")
    snapshot: str = snapshots[0].get("snapshot_id")
    if (
        not isinstance(snapshot, str)
        or not snapshot
        or snapshot.upper() == "HEAD"
    ):
        raise error_type("History did not return a concrete snapshot ID")
    return {
        **project,
        "stream_id": branch,
        "snapshot_id": snapshot,
        "selection_policy": (
            "project and branch alphabetically; first returned snapshot"
        ),
        "scope": {
            "project_id": project["project_id"],
            "stream_id": branch,
            "snapshot_id": snapshot,
        },
    }
