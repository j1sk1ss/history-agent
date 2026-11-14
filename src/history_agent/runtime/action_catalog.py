"""Expose real adapter actions and reject invented names before transport."""

import copy

from mcp.types import Tool

from history_agent.runtime.recovery import ToolFailure

NAVIGATION = (
    "Use only actions in the action enum. There is no source text search "
    "action. Read numbered windows with agent_source; follow an observed "
    "symbol address with agent_resolve_symbol (file, line, column). "
    "agent_find_warnings searches warnings, not source code. Discover the "
    "chosen action's schema first. Base conclusions on returned snapshot "
    "source, not remembered upstream code; report missing context as UC."
)
WORKER_ACTIONS = {
    "get_warning",
    "get_warning_trace",
    "get_warning_trace_and_role",
    "get_all_reviews",
    "get_file_tree",
    "get_tokens",
    "get_ref_tokens",
    "get_value_filter",
    "agent_source",
    "agent_resolve_symbol",
    "agent_find_warnings",
    "agent_checker_info",
    "agent_checker_documentation",
}
DISCOVERY = {
    "workflow",
    "api",
    "skills",
    "skill:history-current",
    "skill:history-review",
    "skill:history-batch",
    "skill:history-navigation",
}


def constrain_tools(
    tools: list[Tool],
    catalog: dict[str, object],
    *,
    worker: bool = False,
) -> list[Tool]:
    actions: dict[str, set[str]] = {
        "history_read": set(),
        "history_write": set(),
    }
    for entry in catalog["actions"]:
        if worker and entry["name"] not in WORKER_ACTIONS:
            continue
        for name in actions:
            if name in entry["tool"].split("/"):
                actions[name].add(entry["name"])
    actions["history_discover"] = set.union(*actions.values()) | DISCOVERY
    result = []
    for tool in tools:
        if worker and tool.name in ("history_current", "history_write"):
            continue
        tool = tool.model_copy(deep=True)
        if tool.name in actions:
            properties = tool.input_schema.setdefault("properties", {})
            prop = properties.setdefault("action", {
                "type": ["string", "null"]
                if tool.name == "history_discover" else "string",
            })
            prop["enum"] = sorted(actions[tool.name])
            if tool.name == "history_discover":
                prop["enum"].append(None)
            tool.description = (tool.description or "") + " " + NAVIGATION
        result.append(tool)
    return result


def validate_action(
    name: str,
    arguments: dict[str, object],
    tools: list[dict[str, object]],
) -> None:
    for tool in tools:
        function = tool["function"]
        if function["name"] != name:
            continue
        prop = function["parameters"].get("properties", {}).get("action", {})
        choices = prop.get("enum")
        if choices is None or arguments.get("action") in choices:
            return
        action = arguments.get("action")
        raise ToolFailure(
            f"Unsupported action {action!r} for {name}; no request was sent.",
            context={
                "code": "unsupported_action",
                "available_actions": copy.deepcopy(choices),
                "guidance": NAVIGATION,
                "write_attempted": False,
            },
        )


def refresh_actions(
    tools: list[dict[str, object]], catalog: dict[str, object],
) -> None:
    """Narrow the host whitelist after actual server catalog discovery."""
    entries = catalog.get("actions")
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and isinstance(entry.get("tool"), str)
        for entry in entries
    ):
        return
    actions = {"history_read": set(), "history_write": set()}
    for entry in entries:
        for name in actions:
            if name in entry["tool"].split("/"):
                actions[name].add(entry["name"])
    actions["history_discover"] = set.union(*actions.values()) | DISCOVERY
    for tool in tools:
        function = tool["function"]
        if function["name"] not in actions:
            continue
        prop = function["parameters"].get("properties", {}).get("action", {})
        choices = prop.get("enum")
        if choices:
            narrowed = [name for name in choices
                        if name is None or name in actions[function["name"]]]
            # ModelContext omits tools with no actions from model requests.
            # Keep the empty set here so fabricated calls are also rejected.
            prop["enum"] = narrowed
