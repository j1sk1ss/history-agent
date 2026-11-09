"""Choose a deterministic project, branch and concrete snapshot."""

from collections.abc import Callable


class CurrentScope:
    def __init__(
        self,
        execute: Callable[[str, dict[str, object]], dict[str, object]],
        error_type: type[Exception],
    ) -> None:
        self.execute: Callable[[str, dict[str, object]], dict[str, object]] = (
            execute
        )
        self.error_type: type[Exception] = error_type

    def resolve(self, args: dict[str, str | int]) -> dict[str, object]:
        if args.get("project_id") is not None and args.get("project_name"):
            raise self.error_type(
                "Choose project_id or project_name, not both"
            )
        project: dict[str, str | int] = self.project(args)
        branch: str = (
            args["stream_id"] if "stream_id" in args else self.branch(project)
        )
        snapshot: str = self.snapshot(project["project_id"], branch)
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

    def projects(self) -> list[dict[str, str | int]]:
        groups: object = self.execute("get_projects", {}).get(
            "project_groups", []
        )
        if not isinstance(groups, list):
            raise self.error_type("History project catalog is invalid")
        projects: list[dict[str, str | int]] = []
        for group in groups:
            if not isinstance(group, dict) or "project_group_id" not in group:
                continue
            data: dict[str, object] = self.execute(
                "get_project_group_info",
                {"project_group_id": group["project_group_id"]},
            )
            entries: object = data.get("project_group", {}).get("projects", [])
            if not isinstance(entries, list):
                continue
            projects.extend(
                {
                    "project_id": project["project_id"],
                    "project_name": str(
                        project.get("project_name") or project["project_id"]
                    ),
                }
                for project in entries
                if (
                    isinstance(project, dict)
                    and type(project.get("project_id")) is int
                )
            )
        if not projects and not groups:
            return [{"project_id": 0, "project_name": "0"}]
        return projects

    def project(self, args: dict[str, str | int]) -> dict[str, str | int]:
        projects: list[dict[str, str | int]] = self.projects()
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
            raise self.error_type(
                (
                    "No matching history project; choose a valid "
                    "project or inspect server access"
                )
            )
        return min(
            projects,
            key=lambda item: (
                item["project_name"].casefold(),
                item["project_name"],
                item["project_id"],
            ),
        )

    def branch(self, project: dict[str, str | int]) -> str:
        info: dict[str, object] = self.execute(
            "branches", {"project_id": project["project_id"]}
        )
        entries: list[str | dict[str, object]] | None = next(
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
            raise self.error_type(
                (
                    "History did not return branches for the selected"
                    " project; specify stream_id"
                )
            )
        names: set[str] = {
            name for item in entries if (name := named_branch(item))
        }
        if not names:
            raise self.error_type("No branches found; specify stream_id")
        return min(names, key=lambda name: (name.casefold(), name))

    def snapshot(self, project_id: int, branch: str) -> str:
        result: dict[str, object] = self.execute(
            "get_snapshots",
            {
                "project_id": project_id,
                "stream_id": branch,
                "number": 1,
            },
        )
        snapshots: object = result.get("snapshots", [])
        if (
            not isinstance(snapshots, list)
            or not snapshots
            or not isinstance(snapshots[0], dict)
        ):
            raise self.error_type("No snapshot in the selected project/branch")
        snapshot: object = snapshots[0].get("snapshot_id")
        if (
            not isinstance(snapshot, str)
            or not snapshot
            or snapshot.upper() == "HEAD"
        ):
            raise self.error_type(
                "History did not return a concrete snapshot ID"
            )
        return snapshot


def named_branch(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("stream_id", "branch_name", "name", "branch", "stream"):
            name: object = item.get(key)
            if isinstance(name, str) and name:
                return name
    return None


def resolve_current(
    execute: Callable[[str, dict[str, object]], dict[str, object]],
    args: dict[str, str | int],
    error_type: type[Exception],
) -> dict[str, object]:
    return CurrentScope(execute, error_type).resolve(args)
