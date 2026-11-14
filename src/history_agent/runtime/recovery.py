"""Bounded context reloads with explicit feedback after tool failures."""

import copy

from history_agent.inference.model import ChatError, compact
from history_agent.runtime.recovery_context import retained_messages


class ToolFailure(ChatError):
    def __init__(
        self,
        message: str,
        *,
        uncertain_write: bool = False,
        context: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.uncertain_write = uncertain_write
        self.context = context or {}


class TurnRecovery:
    def __init__(
        self, messages: list[dict[str, object]], *, max_retries: int = 8,
    ) -> None:
        self.max_retries = max_retries
        self.system = copy.deepcopy(messages[0])
        self.attempts = 0
        self.write_executed = False
        self.stop_after_feedback = False
        self.error = ""
        self.failures: list[dict[str, object]] = []

    def reload(self, harness, call: dict, error: ToolFailure) -> None:
        if self.attempts >= self.max_retries:
            raise ChatError(
                f"Tool failed after {self.max_retries} automatic reloads: "
                + str(error)
            ) from error
        self.attempts += 1
        self.error = str(error)[:1500]
        self.stop_after_feedback = error.uncertain_write or self.write_executed
        self.failures.append({"error": self.error, **error.context})
        instruction = (
            "The host automatically reloaded the context after a tool error. "
            "The error is provided below. Correct the request using "
            "discovered schemas and existing names; use smaller pages. "
            "Successful tool results below remain verified evidence. "
            "Use only the actions enumerated in the tool definitions. "
            "Do not repeat unsupported actions or successful writes. "
            "Use supplied resume_action/resume_arguments "
            "to continue the existing checkpoint instead of "
            "starting a new review job."
        )
        if self.stop_after_feedback:
            instruction += (
                " A write may have completed. Tools are disabled for this "
                "response. Explain the error and ask the user to inspect "
                "warning/review history before retrying; do not claim success."
            )
        harness.messages = retained_messages(
            harness.messages, harness.max_context_bytes // 2
        )
        harness.messages[0] = copy.deepcopy(self.system)
        harness.messages[0]["content"] += "\n\n" + instruction
        harness.messages.extend(
            [
                {"role": "assistant", "content": None, "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": compact(
                        {
                            **error.context,
                            "error": self.error,
                            "context_reloaded": True,
                            "previous_errors": self.failures[-4:-1],
                        }
                    ),
                },
            ]
        )
        harness.emit(
            "notice",
            message=(
                f"Automatic reload {self.attempts}/{self.max_retries}: "
                f"{self.error}"
            ),
        )

    def finish(self, harness, answer: str) -> str:
        if not self.stop_after_feedback:
            return answer
        harness.blocked = True
        return (
            "Tool error: "
            + self.error
            + " Inspect warning/review history before retrying.\n"
            + answer
        )
