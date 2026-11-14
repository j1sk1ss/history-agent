"""Prepare action schemas and persist conversation events before display."""

import sys
from functools import partial

from history_agent.history.constants import READ_ACTIONS, WRITE_ACTIONS
from history_agent.review.actions import BATCH_REVIEW, DIFFERENCE_REVIEW
from history_agent.runtime.action_catalog import constrain_tools
from history_agent.runtime.diagnostics import DiagnosticLog


class ConversationEvents:
    def model_events(self, **context: object):
        callback = partial(self.model_event, **context)
        callback.quiet = self.ui is not None
        return callback

    async def prepare_tools(self) -> None:
        catalog = {"actions": [
            {"name": name, "tool": tool}
            for tool, names in (
                ("history_read", READ_ACTIONS),
                ("history_write", WRITE_ACTIONS),
            ) for name in sorted(names)
        ]}
        catalog["actions"].extend(
            {
                "name": definition.name,
                "tool": "history_read/history_write",
            }
            for definition in (BATCH_REVIEW, DIFFERENCE_REVIEW)
        )
        self.worker_tools = constrain_tools(self.tools, catalog, worker=True)
        self.tools = constrain_tools(self.tools, catalog)
        settings = getattr(self.history, "settings", None)
        self.diagnostics = DiagnosticLog(
            self.args.batch_state_dir,
            secrets=(
                getattr(self.model, "key", None),
                getattr(settings, "password", None),
                getattr(settings, "session", None),
            ),
        )
        if not self.diagnostics.disabled:
            self.diagnostics.event(
                "session_started", log_path=str(self.diagnostics.path),
            )
            if not self.ui:
                print(
                    f"Diagnostic log: {self.diagnostics.path}",
                    file=sys.stderr,
                )

    def model_event(self, kind: str, **data: object) -> None:
        self.diagnostics.event(kind, **data)
        if self.ui:
            self.ui.event(kind, **data)
        elif kind in ("notice", "failure"):
            print(data.get("message", ""), file=sys.stderr)

    def batch_event(self, kind: str, **data: object) -> None:
        self.diagnostics.event(kind, **data)
        if self.ui:
            self.ui.event(kind, **data)
        elif kind in ("batch_started", "batch_progress"):
            if kind == "batch_started":
                print("batch job:", data["job_id"], file=sys.stderr)
            else:
                print(
                    f"batch {data['done']}/{data['total']}: "
                    f"{data['checker']} {data['warning_id']}",
                    file=sys.stderr,
                )
        elif kind == "notice":
            print(data["message"], file=sys.stderr)
