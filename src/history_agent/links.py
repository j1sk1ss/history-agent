"""
Resolve SvaceLight links through the configured server, without fetching
web pages.
"""

import re
from urllib.parse import unquote, urlsplit

URL_ACTION = "agent_resolve_history_url"
URL_META = {
    "name": URL_ACTION,
    "read_only": True,
    "description": "Resolve a SvaceLight comparison/project/warning URL to "
    "explicit project IDs, branches, "
    "concrete snapshots and ready read/review arguments. No "
    "reviews are saved. "
    "Comparison defaults to appeared (First); preserves Second/Both from URL.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 16384}
        },
    },
}


def find_history_urls(text):
    links = []
    for match in re.finditer(r'https?://[^\s<>"\u00a0()\[\]]+', text):
        url = match.group().rstrip(".,;)]}")
        if urlsplit(url).fragment.split("/", 1)[0] not in (
            "compare_svace2svace",
            "show",
        ):
            continue
        if url not in links:
            links.append(url)
    return links


def resolve_history_url(client, url, error_type):
    try:
        parsed, configured = urlsplit(url), urlsplit(client.endpoint)

        def origin(value):
            return (
                value.scheme.lower(),
                value.hostname,
                value.port or (443 if value.scheme == "https" else 80),
            )

        if (
            parsed.username
            or parsed.password
            or origin(parsed) != origin(configured)
        ):
            raise error_type(
                "History link belongs to a different server; start "
                "history-chat with its --history-url"
            )
        prefix = configured.path.removesuffix("/history/api")
        if parsed.path != prefix + "/history/svacelight/index.html":
            raise error_type(
                "Expected a SvaceLight history URL under the configured "
                "history URL prefix"
            )
        route, _, location_text = parsed.fragment.partition("#")
        parts = [unquote(part) for part in route.split("/")]
    except (ValueError, UnicodeError):
        raise error_type("Invalid history URL") from None
    comparison = parts[0] == "compare_svace2svace"
    if not comparison and parts[0] != "show":
        raise error_type(
            "Supported history views are compare_svace2svace and show"
        )
    needed = 7 if comparison else 4
    if len(parts) < needed or any(not part for part in parts[1:needed]):
        raise error_type(
            "History link is missing an explicit project, branch or snapshot"
        )
    names = {parts[1]}
    if comparison:
        names.add(parts[4])
    listing = client.execute("get_projects", {})
    projects = {name: set() for name in names}
    for group in listing.get("project_groups", []):
        info = client.execute(
            "get_project_group_info",
            {"project_group_id": group["project_group_id"]},
        )
        for project in info.get("project_group", {}).get("projects", []):
            name = project.get("project_name")
            if name in projects:
                projects[name].add(project["project_id"])
    for name, ids in projects.items():
        if len(ids) != 1:
            raise error_type(
                "Cannot resolve history link project unambiguously: " + name
            )

    def scope(start):
        selected = {
            "project_id": next(iter(projects[parts[start]])),
            "stream_id": parts[start + 1],
            "snapshot_id": parts[start + 2],
        }
        if selected["snapshot_id"].upper() == "HEAD":
            page = client.execute(
                "get_snapshots",
                {
                    "project_id": selected["project_id"],
                    "stream_id": selected["stream_id"],
                    "number": 1,
                },
            )
            snapshots = page.get("snapshots", [])
            if (
                not snapshots
                or not snapshots[0].get("snapshot_id")
                or snapshots[0]["snapshot_id"].upper() == "HEAD"
            ):
                raise error_type("Cannot resolve HEAD to a concrete snapshot")
            selected["snapshot_id"] = snapshots[0]["snapshot_id"]
        return selected

    current = scope(1)
    result = {
        "kind": "comparison" if comparison else "project",
        "current": current,
        "saves_reviews": False,
    }
    if comparison:
        base = scope(4)
        subset_name = parts[7].lower() if len(parts) > 7 else ""
        aliases = {
            "": "appeared",
            "first": "appeared",
            "appeared": "appeared",
            "second": "disappeared",
            "disappeared": "disappeared",
            "both": "both",
        }
        if subset_name not in aliases:
            raise error_type(
                "Unsupported comparison subset; choose First, Second or Both"
            )
        subset = aliases[subset_name]
        arguments = {
            **current,
            **{"base_" + key: value for key, value in base.items()},
            "subset": subset,
        }
        result.update(
            base=base,
            subset=subset,
            scope=base if subset == "disappeared" else current,
            read_action="agent_compare_warnings",
            read_arguments=arguments,
            review_action="review_diff",
            review_arguments=arguments,
            selection_note="The comparison subset defines the review queue. A "
            "selected "
            "warning is only the UI cursor.",
        )
        warning = parts[8] if len(parts) > 8 else ""
    else:
        warning = parts[4] if len(parts) > 4 else ""
        result.update(
            scope=current,
            review_action="review_batch",
            review_arguments=current,
        )
        if warning:
            result.update(
                kind="warning",
                read_action="get_warning",
                read_arguments={**current, "warning_id": warning},
            )
            result.pop("review_action")
            result.pop("review_arguments")
    if warning:
        result["selected_warning_id"] = warning
    if location_text:
        address = unquote(location_text).rsplit(":", 2)
        if len(address) == 3 and address[2].isdigit() and int(address[2]) > 0:
            result["location"] = {
                "file": address[0],
                "hash": address[1],
                "line": int(address[2]),
            }
    return result
