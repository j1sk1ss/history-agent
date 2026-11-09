"""Construct model messages for host-prefetched tool results."""

from history_agent.inference.model import compact


def tool_exchange(
    call_id: str,
    action: str,
    arguments: dict[str, object],
    contents: str,
) -> list[dict[str, object]]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "history_read",
                        "arguments": compact(
                            {
                                "action": action,
                                "arguments": arguments,
                            }
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": contents},
    ]
