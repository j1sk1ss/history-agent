"""Discover actions and execute validated history requests."""

from __future__ import annotations

import threading
from _thread import RLock

from history_agent.contracts import ActionMetadata
from history_agent.history.actions import (
    COMPARE_WARNINGS,
    LOCAL_ACTIONS,
    LOCAL_REQUESTS,
)
from history_agent.history.catalog import extension_action, legacy_action
from history_agent.history.comparison import legacy_compare
from history_agent.history.constants import (
    COMPARE_ACTION,
    CURRENT_ACTION,
    DOC_ACTION,
    READ_ACTIONS,
    SCHEMA_CATALOG_ACTIONS,
    URL_ACTION,
    WRITE_ACTIONS,
)
from history_agent.history.current import resolve_current
from history_agent.history.guide import Guide, GuideError
from history_agent.history.links import resolve_history_url
from history_agent.history.settings import HistoryError, Settings
from history_agent.history.transport import HistoryTransport
from history_agent.history.validation import validate_arguments
from history_agent.review.comments import review_comment


class HistoryClient:
    def __init__(self, settings: Settings) -> None:
        self._transport: HistoryTransport = HistoryTransport(settings)
        self.settings: Settings = settings
        self.endpoint: str = self._transport.endpoint
        self._guide: Guide = Guide(
            self.endpoint.removesuffix("/history/api") + "/guide",
            self._load_guide,
        )
        self._catalog: dict[str, ActionMetadata] | None = None
        self._legacy_comparison: bool = False
        self._lock: RLock = threading.RLock()

    def _load_guide(self) -> str:
        return self._transport.load_guide(self._guide.url)

    def _call(
        self, action: str, query: dict[str, str | int | None] | None = None,
        body: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        return self._transport._call(action, query, body)

    def _load_catalog(self) -> dict[str, ActionMetadata]:
        if self._catalog is not None:
            return self._catalog
        legacy: dict[str, dict[str, object]] = self._call("get_actions").get(
            "actions", {}
        )
        if not isinstance(legacy, dict):
            raise HistoryError(
                "History get_actions returned an invalid catalog"
            )
        catalog: dict[str, ActionMetadata] = {
            name: legacy_action(name, metadata)
            for name, metadata in legacy.items()
            if name in READ_ACTIONS and not name.startswith("agent_")
        }
        catalog_action: str | None = next(
            (name for name in SCHEMA_CATALOG_ACTIONS if name in legacy), None
        )
        if catalog_action:
            data: object = self._call(catalog_action).get("data")
            if not isinstance(data, dict) or not isinstance(
                data.get("actions"), dict
            ):
                raise HistoryError(
                    f"History {catalog_action} returned an invalid catalog"
                )
            for name, metadata in data["actions"].items():
                if name not in READ_ACTIONS | WRITE_ACTIONS:
                    continue
                catalog[name] = extension_action(name, metadata)
        for definition in LOCAL_ACTIONS:
            catalog[definition.name] = definition.metadata()
        if (
            COMPARE_ACTION not in catalog
            and "get_warnings_compare_svace2svace" in legacy
        ):
            catalog[COMPARE_ACTION] = COMPARE_WARNINGS.metadata()
            self._legacy_comparison = True
        self._catalog = catalog
        return catalog

    def discover(self, action: str | None = None) -> dict[str, object]:
        with self._lock:
            catalog: dict[str, ActionMetadata] = self._load_catalog()
            if action:
                if action not in catalog:
                    raise HistoryError(
                        "Action is unavailable through this adapter: " + action
                    )
                return catalog[action].model_dump(exclude_none=True)
            return {
                "actions": [
                    {
                        "name": name,
                        "description": info.description[:180],
                        "tool": "history_read"
                        if info.read_only
                        else "history_write",
                    }
                    for name, info in sorted(catalog.items())
                ],
                "workflow": "history://workflow",
                "next": (
                    "Call history_discover(action=...) for an "
                    "action's input schema"
                ),
            }

    def execute(
        self, action: str, arguments: dict[str, object], *,
        write: bool = False,
    ) -> dict[str, object]:
        if action not in (WRITE_ACTIONS if write else READ_ACTIONS):
            raise HistoryError(
                "Action is not allowed through this tool: " + action
            )
        with self._lock:
            schema: dict[str, object]
            values: dict[str, object]
            schema, values = self.prepare_arguments(action, arguments)
            if action == DOC_ACTION:
                try:
                    return self._guide.checker(
                        values["checker_name"],
                        values["offset"],
                        values["limit"],
                    )
                except GuideError as error:
                    raise HistoryError(str(error)) from None
            if action == COMPARE_ACTION and self._legacy_comparison:
                return legacy_compare(self._call, values, HistoryError)
            if action == URL_ACTION:
                return resolve_history_url(
                    self.execute, self.endpoint, values["url"], HistoryError
                )
            if action == CURRENT_ACTION:
                return resolve_current(self.execute, values, HistoryError)
            query: dict[str, str]
            body: dict[str, str]
            query, body = ({}, {})
            for key, value in values.items():
                if isinstance(value, bool):
                    value: str = str(value).lower()
                target: dict[str, str] = (
                    body
                    if schema["properties"][key].get(
                        "x-http-parameter-location"
                    )
                    == "post"
                    else query
                )
                target[key] = str(value)
            result: dict[str, object] = self._call(action, query, body or None)
            if action.startswith("agent_"):
                data: dict[str, object] = result["data"]
                if action == "agent_checker_info":
                    try:
                        documentation: dict[str, str | int | bool] = (
                            self._guide.checker(values["checker_name"])
                        )
                    except GuideError as error:
                        documentation = {
                            "status": "unavailable",
                            "error": str(error),
                        }
                    return {**data, "documentation": documentation}
                return data
            return {
                key: value for key, value in result.items() if key != "status"
            }

    def prepare_arguments(
        self, action: str, arguments: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object]]:
        info: dict[str, object] = self.discover(action)
        schema: dict[str, object] = info["input_schema"]
        values: dict[str, object] = validate_arguments(
            arguments,
            schema,
            LOCAL_REQUESTS.get(action)
            if action != COMPARE_ACTION or self._legacy_comparison
            else None,
        )
        if action == "agent_set_review":
            values["comment"] = review_comment(values["comment"])
            if not values["comment"]:
                raise HistoryError(
                    "A non-empty review explanation is required"
                )
        return (schema, values)
