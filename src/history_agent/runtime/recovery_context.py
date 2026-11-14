"""Retain complete successful exchanges without replaying model guesses."""

import copy
import json

from history_agent.inference.model import compact


def failed_result(message: dict[str, object]) -> bool:
    try:
        data = json.loads(message["content"])
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and bool(
        data.get("error") or data.get("context_reloaded")
    )


def retained_messages(
    messages: list[dict[str, object]],
    byte_limit: int,
) -> list[dict[str, object]]:
    units: list[tuple[bool, list[dict[str, object]]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        index += 1
        if message["role"] in ("system", "user"):
            units.append((True, [copy.deepcopy(message)]))
            continue
        calls = message.get("tool_calls", [])
        if message["role"] != "assistant" or not calls:
            continue
        results = []
        while index < len(messages) and messages[index]["role"] == "tool":
            result = messages[index]
            index += 1
            if not failed_result(result):
                results.append(copy.deepcopy(result))
        ids = {result["tool_call_id"] for result in results}
        complete = [copy.deepcopy(call) for call in calls if call["id"] in ids]
        if not complete:
            continue
        host = all(
            call["id"].startswith(("host-", "batch-read-"))
            for call in complete
        )
        units.append(
            (
                host,
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": complete,
                    },
                    *results,
                ],
            )
        )
    sizes = [len(compact(unit).encode()) for _, unit in units]
    total = sum(sizes)
    for index, (mandatory, _) in enumerate(units):
        if total <= byte_limit:
            break
        if not mandatory:
            total -= sizes[index]
            units[index] = (False, [])
    return [message for _, unit in units for message in unit]
