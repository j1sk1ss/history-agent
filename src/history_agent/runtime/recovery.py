"""Bounded context reloads with explicit feedback after tool failures."""

import copy

from history_agent.inference.model import ChatError, compact


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
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.seed = []
        host_calls = set()
        for message in messages:
            calls = message.get("tool_calls", [])
            if calls and all(
                call["id"].startswith(("host-", "batch-read-"))
                for call in calls
            ):
                host_calls.update(call["id"] for call in calls)
                self.seed.append(copy.deepcopy(message))
            elif message["role"] in ("system", "user") or (
                message["role"] == "tool"
                and message.get("tool_call_id") in host_calls
            ):
                self.seed.append(copy.deepcopy(message))
        self.attempts = 0
        self.write_executed = False
        self.stop_after_feedback = False
        self.error = ""

    def reload(self, harness, call: dict, error: ToolFailure) -> None:
        if self.attempts >= 2:
            raise ChatError(
                "Tool failed after 2 automatic reloads: " + str(error)
            ) from error
        self.attempts += 1
        self.error = str(error)[:1500]
        self.stop_after_feedback = error.uncertain_write or self.write_executed
        instruction = (
            "The host automatically reloaded the context after a tool error. "
            "The error is provided below. Correct the request using "
            "discovered schemas and existing names; use smaller pages. "
            "Do not repeat successful writes. Re-read missing schemas or "
            "source as needed."
        )
        if self.stop_after_feedback:
            instruction += (
                " A write may have completed. Tools are disabled for this "
                "response. Explain the error and ask the user to inspect "
                "warning/review history before retrying; do not claim success."
            )
        harness.messages = copy.deepcopy(self.seed)
        harness.messages.append({"role": "system", "content": instruction})
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
                        }
                    ),
                },
            ]
        )
        harness.emit(
            "notice",
            message=f"Automatic reload {self.attempts}/2: {self.error}",
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
