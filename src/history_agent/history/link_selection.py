"""Keep comparison scopes on the host while models select a checker."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable

from history_agent.history.constants import (
    COMPARE_ACTION,
    LINK_WARNINGS_ACTION,
)
from history_agent.history.settings import HistoryError


class LinkSelections:
    def __init__(self) -> None:
        self.scopes: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()

    def bind(self, resolved: dict[str, object]) -> dict[str, object]:
        with self._lock:
            return self._bind(resolved)

    def _bind(self, resolved):
        if resolved["kind"] != "comparison":
            return resolved
        link_id = f"history-link-{len(self.scopes) + 1}"
        self.scopes[link_id] = copy.deepcopy(resolved["read_arguments"])
        return {
            **resolved,
            "link_id": link_id,
            "warnings_action": LINK_WARNINGS_ACTION,
            "warnings_arguments": {"link_id": link_id},
            "next_step": (
                "To fetch warnings, discover warnings_action and call it with "
                "warnings_arguments plus the checker_name given by the user. "
                "If the checker name is missing, ask for it. Do not guess it "
                "or start a review job just to list warnings. First/appeared "
                "is the UI default when the URL has no subset. A zero total "
                "means no matching warnings in this selection; do not change "
                "the subset or search the whole project."
            ),
        }

    def read(
        self,
        execute: Callable[[str, dict[str, object]], dict[str, object]],
        arguments: dict[str, object],
    ) -> dict[str, object]:
        link_id = arguments["link_id"]
        with self._lock:
            scope = copy.deepcopy(self.scopes.get(link_id))
        if scope is None:
            raise HistoryError(
                "Unknown comparison link; resolve the user's history URL first"
            )
        result = execute(
            COMPARE_ACTION,
            {
                **scope,
                **{k: v for k, v in arguments.items() if k != "link_id"},
            },
        )
        return {**result, "link_id": link_id}
