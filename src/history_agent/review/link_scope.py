"""Keep user-selected history links authoritative across model retries."""

from __future__ import annotations

import copy
import json

from history_agent.history.constants import (
    COMPARE_ACTION,
    CURRENT_ACTION,
    LINK_WARNINGS_ACTION,
    URL_ACTION,
)
from history_agent.review.link_jobs import check_resume
from history_agent.review.link_reads import LinkReads
from history_agent.runtime.recovery import ToolFailure


class LinkScope:
    def __init__(self, history, root) -> None:
        self.history, self.root = history, root
        self.links: dict[str, dict[str, object]] = {}
        self.reads = LinkReads()

    def bind(self, links: dict[str, dict[str, object]]) -> bool:
        def selection(values):
            return [
                {
                    key: value
                    for key, value in link.items()
                    if key != "link_id" and key != "warnings_arguments"
                }
                for link in values.values()
            ]

        changed = selection(self.links) != selection(links)
        self.links = copy.deepcopy(links)
        if changed:
            self.reads = LinkReads()
        return changed

    def reject(self, message: str) -> None:
        raise ToolFailure(
            message + ". Use the active link; do not select another project.",
            context={"active_links": list(self.links.values())},
        )

    def choose(self, candidates):
        if len(candidates) != 1:
            self.reject(
                "Request does not identify one active link unambiguously"
            )
        return candidates[0]

    def current(self, args):
        candidates = []
        for link in self.links.values():
            scope = link["scope"]
            side = "base" if link.get("subset") == "disappeared" else "current"
            if all(
                value is None
                or value
                == (
                    link["display"][side + "_project"]
                    if key == "project_name"
                    else scope.get(key)
                )
                for key, value in args.items()
            ):
                candidates.append(link)
        link = self.choose(candidates)
        return {**link, "selection": "Pinned to the user's history link"}

    def prepare(self, name, arguments):
        if not self.links or name == "history_discover":
            return arguments, None
        if name == "history_current":
            return arguments, self.current(arguments)
        action = arguments.get("action")
        if not isinstance(action, str) or not action:
            self.reject("Specify a discovered history action")
        args = arguments.get("arguments", {})
        if not isinstance(args, dict):
            self.reject("Action arguments must be an object")
        if action == CURRENT_ACTION:
            return arguments, self.current(args)
        if action == URL_ACTION:
            link = self.links.get(args.get("url"))
            if not link:
                self.reject("URL was not supplied in the active user request")
            return arguments, copy.deepcopy(link)
        if action == LINK_WARNINGS_ACTION:
            if not any(
                args.get("link_id") == link.get("link_id")
                for link in self.links.values()
                if link.get("link_id")
            ):
                self.reject("Comparison link belongs to a previous selection")
            return arguments, None
        if action in ("get_projects", "get_project_group_info"):
            self.reject("Global project discovery is unnecessary for a link")
        if action in ("review_batch", "review_diff"):
            values = self.batch(action, args)
        elif action == COMPARE_ACTION:
            values = self.comparison(args)
        else:
            schema = self.history.discover(action)["input_schema"]
            values = self.reads.prepare(
                action,
                args,
                schema,
                list(self.links.values()),
                self.reject,
            )
        return {**arguments, "arguments": values}, None

    def comparison(self, args):
        candidates = [
            link
            for link in self.links.values()
            if link["kind"] == "comparison"
            and all(
                key not in args or args[key] == value
                for key, value in link["read_arguments"].items()
            )
        ]
        link = self.choose(candidates)
        return {**link["read_arguments"], **args}

    def batch(self, action, args):
        if "job_id" in args:
            check_resume(self.root, args, self.links.values(), self.reject)
            return args
        if action == "review_diff" or "base_snapshot_id" in args:
            return self.comparison(args)
        candidates = [
            link
            for link in self.links.values()
            if (link["kind"] != "comparison" or args.get("warning_id"))
            and all(
                key not in args or args[key] == value
                for key, value in link["scope"].items()
            )
            and (
                link["kind"] != "warning"
                or args.get(
                    "warning_id",
                    link["selected_warning_id"],
                )
                == link["selected_warning_id"]
            )
        ]
        link = self.choose(candidates)
        values = {**link["scope"], **args}
        if link["kind"] == "warning":
            values["warning_id"] = link["selected_warning_id"]
        return values

    def observe(self, arguments, result):
        if not self.links or result.is_error:
            return
        try:
            data = json.loads(
                "".join(
                    part.text for part in result.content if part.type == "text"
                )
            )
            self.reads.observe(arguments.get("arguments", {}), data)
        except (ValueError, TypeError):
            pass
