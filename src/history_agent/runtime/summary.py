"""Retain only small completed-job summaries in the interactive context."""

import json

from history_agent.inference.model import compact
from history_agent.prompts import Prompts


def pretty_json_text(value: str) -> str:
    """
    Pretty-print complete JSON on display; keep compact JSON inside model
    context.
    """
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return value


class ConversationSummary:
    def retain_summary(self, summary: dict[str, object]) -> None:
        self.harness.reset()
        self.harness.messages[0]["content"] += Prompts.get(
            "runtime.batch_hint"
        )
        small: dict[str, object] = {
            key: summary[key]
            for key in (
                "job_id",
                "scope",
                "write",
                "total",
                "states",
                "classifications",
            )
        }
        if summary.get("comparison"):
            small["comparison"] = summary["comparison"]
        self.harness.messages.append(
            {
                "role": "assistant",
                "content": "Previous batch summary: " + compact(small),
            }
        )
        self.router.completed = None
