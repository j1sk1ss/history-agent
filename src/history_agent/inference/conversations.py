"""Convert tool conversations into provider-native messages."""

from __future__ import annotations

import json

from history_agent.inference.model import ChatError


def api_messages(
    messages: list[dict[str, object]],
    provider: str,
) -> tuple[str, list[dict[str, object]]]:
    """
    Convert the model-neutral conversation without dropping tool
    identities.
    """
    system: list[str]
    out: list[dict[str, object]]
    system, out = [], []
    for message in messages:
        role: str = message["role"]
        if role == "system":
            system.append(message.get("content") or "")
        elif role == "user":
            out.append(
                {"role": "user", "content": message.get("content") or ""}
            )
        elif role == "assistant":
            blocks: list[dict[str, object]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                function: dict[str, object] = call["function"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": function["name"],
                        "input": json.loads(function["arguments"]),
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block: dict[str, object] = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": message.get("content") or "",
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system), out


def responses_input(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for message in messages:
        if message["role"] in ("system", "user"):
            items.append(
                {
                    "role": message["role"],
                    "content": message.get("content") or "",
                }
            )
        elif message["role"] == "assistant":
            if isinstance(message.get("responses_items"), list):
                # Preserve opaque reasoning items for stateless Responses tool
                # continuations.
                items.extend(message["responses_items"])
                continue
            if message.get("content"):
                items.append(
                    {"role": "assistant", "content": message["content"]}
                )
            for call in message.get("tool_calls", []):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    }
                )
        elif message["role"] == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message.get("content") or "",
                }
            )
    return items


def gemini_messages(
    messages: list[dict[str, object]],
) -> tuple[str, list[dict[str, object]]]:
    system: list[str]
    out: list[dict[str, object]]
    system, out = [], []
    tool_names: dict[str, str] = {}
    for message in messages:
        role: str = message["role"]
        if role == "system":
            system.append(message.get("content") or "")
        elif role == "user":
            out.append(
                {
                    "role": "user",
                    "parts": [{"text": message.get("content") or ""}],
                }
            )
        elif role == "assistant":
            if isinstance(message.get("gemini_parts"), list):
                parts: list[dict[str, object]] = message["gemini_parts"]
            else:
                parts = []
                if message.get("content"):
                    parts.append({"text": message["content"]})
                for call in message.get("tool_calls", []):
                    parts.append(
                        {
                            "functionCall": {
                                "name": call["function"]["name"],
                                "args": json.loads(
                                    call["function"]["arguments"]
                                ),
                            }
                        }
                    )
            for call in message.get("tool_calls", []):
                tool_names[call["id"]] = call["function"]["name"]
            if parts:
                out.append({"role": "model", "parts": parts})
        elif role == "tool":
            # Tool-call IDs are not a Gemini field; preserve the original name.
            name: str | None = tool_names.get(message["tool_call_id"])
            if not name:
                raise ChatError(
                    "Gemini function response has no matching tool call"
                )
            raw: str = message.get("content") or ""
            try:
                data: object = json.loads(raw)
            except ValueError:
                data = {"text": raw}
            part: dict[str, object] = {
                "functionResponse": {
                    "name": name,
                    "response": data
                    if isinstance(data, dict)
                    else {"result": data},
                }
            }
            if (
                out
                and out[-1]["role"] == "user"
                and any("functionResponse" in p for p in out[-1]["parts"])
            ):
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
    return "\n\n".join(system), out
