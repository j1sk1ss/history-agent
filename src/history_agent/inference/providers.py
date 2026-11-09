"""
Native API transports normalized to the harness's Chat Completions tool
loop.

No external SDK is required. Providers expose their own reasoning only if
their API actually returns it; private, unavailable reasoning is never
synthesized.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from http.client import HTTPResponse

from history_agent.history.client import NoRedirects
from history_agent.inference.model import (
    MAX_RESPONSE_BYTES,
    ChatError,
    Model,
    compact,
    sse_events,
)


def api_messages(
    messages: list[dict[str, object]], provider: str,
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


def result(
    content: str | None, calls: list[tuple[str, str, str | dict[str, object]]],
    reason: str, usage: dict[str, object] | None = None, *,
    reasoning: str | None = None,
    gemini_parts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": content or None,
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": compact(arguments)
                    if not isinstance(arguments, str)
                    else arguments,
                },
            }
            for call_id, name, arguments in calls
        ]
    if gemini_parts:
        message["gemini_parts"] = gemini_parts
    return {
        "choices": [{"message": message, "finish_reason": reason}],
        "usage": usage,
    }


def _request(
    base: str, path: str, payload: dict[str, object], headers: dict[str, str],
    timeout: float, *, stream: bool,
) -> HTTPResponse:
    url: str = base.rstrip("/") + path
    request: urllib.request.Request = urllib.request.Request(
        url,
        data=compact(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    opener: urllib.request.OpenerDirector = urllib.request.build_opener(
        NoRedirects()
    )
    return opener.open(request, timeout=timeout)


def _records(
    response: HTTPResponse, stream: bool,
) -> Iterator[dict[str, object]]:
    if stream and response.headers.get_content_type() == "text/event-stream":
        for data in sse_events(response):
            if data.strip() == "[DONE]":
                return
            yield json.loads(data)
    else:
        raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ChatError("Model response exceeds 2 MiB")
        yield json.loads(raw)


def anthropic(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    system: str
    converted: list[dict[str, object]]
    system, converted = api_messages(messages, "anthropic")
    payload: dict[str, object] = {
        "model": model.name,
        "max_tokens": model.max_output_tokens,
        "messages": converted,
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ],
        "stream": model.stream,
    }
    if system:
        payload["system"] = system
    headers: dict[str, str] = {
        "x-api-key": model.key or "",
        "anthropic-version": "2023-06-01",
    }
    with _request(
        model.base,
        "/messages",
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        text: list[str]
        thinking: list[str]
        calls: list[tuple[str, str, str | dict[str, object]]]
        blocks: dict[int, dict[str, object]]
        usage: dict[str, object]
        text, thinking, calls, blocks, usage = [], [], [], {}, {}
        stopped: bool
        finish: str | None
        stopped, finish = False, None
        for item in _records(response, model.stream):
            if "error" in item or item.get("type") == "error":
                raise ChatError("Anthropic returned an API error")
            typ: str | None = item.get("type")
            if typ == "message_start":
                usage.update(item.get("message", {}).get("usage") or {})
            elif typ == "content_block_start":
                idx: int = item["index"]
                b: dict[str, object] = item["content_block"]
                blocks[idx] = b.copy()
                if b.get("type") == "text" and b.get("text"):
                    text.append(b["text"])
                    emit("content", text=b["text"])
            elif typ == "content_block_delta":
                b = blocks.get(item["index"], {})
                delta: dict[str, object] = item.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text.append(delta["text"])
                    emit("content", text=delta["text"])
                elif delta.get("type") == "thinking_delta" and delta.get(
                    "thinking"
                ):
                    thinking.append(delta["thinking"])
                    emit("reasoning", text=delta["thinking"])
                elif delta.get("type") == "input_json_delta":
                    b["json_fragments"] = b.get(
                        "json_fragments", ""
                    ) + delta.get("partial_json", "")
                    if delta.get("partial_json"):
                        emit("tool_delta", text=delta["partial_json"])
            elif typ == "message_delta":
                usage.update(item.get("usage") or {})
                finish = item.get("delta", {}).get("stop_reason")
            elif typ == "message_stop":
                stopped = True
            elif typ == "message":  # non-stream response
                stopped = True
                finish = item.get("stop_reason")
                usage.update(item.get("usage") or {})
                for b in item.get("content", []):
                    if b.get("type") == "text":
                        text.append(b.get("text", ""))
                        emit("content", text=b.get("text", ""))
                    elif b.get("type") == "thinking":
                        thinking.append(b.get("thinking", ""))
                        emit("reasoning", text=b.get("thinking", ""))
                    elif b.get("type") == "tool_use":
                        calls.append((b["id"], b["name"], b.get("input", {})))
        if not stopped:
            raise ChatError(
                "Anthropic stream ended before completion; no tool calls "
                "executed"
            )
        for idx in sorted(blocks):
            b = blocks[idx]
            if b.get("type") == "tool_use":
                arg: str | None = b.get("json_fragments")
                calls.append(
                    (
                        b["id"],
                        b["name"],
                        json.loads(arg) if arg else b.get("input", {}),
                    )
                )
        stop: str | None = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }.get(finish)
        if stop is None:
            raise ChatError("Unexpected Anthropic finish reason")
        stats: dict[str, object] = {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": usage.get("cache_read_input_tokens")
            },
        }
        emit("usage", usage=stats)
        return result(
            "".join(text), calls, stop, stats, reasoning="".join(thinking)
        )


def gemini(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    system: str
    converted: list[dict[str, object]]
    system, converted = gemini_messages(messages)
    payload: dict[str, object] = {
        "contents": converted,
        "generationConfig": {"maxOutputTokens": model.max_output_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t["function"]["name"],
                        "description": t["function"]["description"],
                        "parametersJsonSchema": t["function"]["parameters"],
                    }
                    for t in tools
                ]
            }
        ]
    model_id: str = urllib.parse.quote(model.name, safe="-_.")
    endpoint: str = (
        f"/models/{model_id}:streamGenerateContent?alt=sse"
        if model.stream
        else f"/models/{model_id}:generateContent"
    )
    headers: dict[str, str] = {"x-goog-api-key": model.key or ""}
    with _request(
        model.base,
        endpoint,
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        text: list[str]
        thought: list[str]
        calls: list[tuple[str, str, str | dict[str, object]]]
        preserved: list[dict[str, object]]
        text, thought, calls, preserved = [], [], [], []
        usage: dict[str, object]
        finish: str | None
        usage, finish = {}, None
        for item in _records(response, model.stream):
            if item.get("error"):
                raise ChatError("Gemini returned an API error")
            usage.update(item.get("usageMetadata") or {})
            for candidate in item.get("candidates", []):
                finish = candidate.get("finishReason") or finish
                for part in candidate.get("content", {}).get("parts", []):
                    preserved.append(part)
                    if isinstance(part.get("text"), str):
                        if part.get("thought"):
                            thought.append(part["text"])
                            emit("reasoning", text=part["text"])
                        else:
                            text.append(part["text"])
                            emit("content", text=part["text"])
                    if isinstance(part.get("functionCall"), dict):
                        call: dict[str, object] = part["functionCall"]
                        args: dict[str, object] = call.get("args") or {}
                        calls.append(
                            (
                                f"gemini-{len(calls)}-{call['name']}",
                                call["name"],
                                args,
                            )
                        )
                        emit("tool_delta", text=compact(args))
        stop: str | None = {
            "STOP": "tool_calls" if calls else "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
        }.get(finish)
        if stop is None:
            raise ChatError(
                "Gemini response is incomplete or blocked; no tool calls "
                "executed"
            )
        stats: dict[str, object] = {
            "prompt_tokens": usage.get("promptTokenCount"),
            "completion_tokens": usage.get("candidatesTokenCount"),
            "prompt_tokens_details": {
                "cached_tokens": usage.get("cachedContentTokenCount")
            },
        }
        emit("usage", usage=stats)
        return result(
            "".join(text),
            calls,
            stop,
            stats,
            reasoning="".join(thought),
            gemini_parts=preserved,
        )


def responses(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model.name,
        "input": responses_input(messages),
        "stream": model.stream,
        "max_output_tokens": model.max_output_tokens,
        "tools": [
            {
                "type": "function",
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"]["parameters"],
            }
            for t in tools
        ],
    }
    if model.provider == "codex":
        # Summaries are public API output, not the model's private chain of
        # thought.
        payload["reasoning"] = {"summary": "auto"}
        payload["include"] = ["reasoning.encrypted_content"]
    headers: dict[str, str] = {"Authorization": "Bearer " + (model.key or "")}
    with _request(
        model.base,
        "/responses",
        payload,
        headers,
        model.timeout,
        stream=model.stream,
    ) as response:
        text: list[str]
        thought: list[str]
        calls: list[tuple[str, str, str | dict[str, object]]]
        current: dict[str, dict[str, object]]
        text, thought, calls, current = [], [], [], {}
        output_items: dict[int, dict[str, object]] = {}
        completed_output: list[dict[str, object]] | None = None
        done: bool
        usage: dict[str, object]
        done, usage = False, {}
        for item in _records(response, model.stream):
            typ: str | None = item.get("type")
            if typ in (
                "response.failed",
                "response.incomplete",
                "error",
            ) or item.get("error"):
                raise ChatError(
                    "Responses API returned a failed/incomplete response"
                )
            if typ == "response.output_text.delta":
                t: str = item.get("delta", "")
                text.append(t)
                emit("content", text=t)
            elif typ == "response.reasoning_summary_text.delta":
                t = item.get("delta", "")
                thought.append(t)
                emit("reasoning", text=t)
            elif typ == "response.function_call_arguments.delta":
                entry: dict[str, object] = current.setdefault(
                    item["item_id"], {"arguments": ""}
                )
                entry["arguments"] += item.get("delta", "")
                emit("tool_delta", text=item.get("delta", ""))
            elif typ == "response.output_item.added":
                b: dict[str, object] = item.get("item", {})
                if b.get("type") == "function_call":
                    current[b["id"]] = {
                        **b,
                        "arguments": b.get("arguments", ""),
                    }
            elif typ == "response.output_item.done":
                b = item.get("item", {})
                output_items[item.get("output_index", len(output_items))] = b
                if b.get("type") == "function_call":
                    current[b["id"]] = b
            elif typ == "response.completed":
                done = True
                completed_output = item.get("response", {}).get("output")
                usage = item.get("response", {}).get("usage") or {}
            elif "output" in item and isinstance(item["output"], list):
                done = True
                usage = item.get("usage") or {}
                completed_output = item["output"]
                for b in item["output"]:
                    if b.get("type") == "function_call":
                        current[b.get("id", b["call_id"])] = b
                    elif b.get("type") == "message":
                        for part in b.get("content", []):
                            if part.get("type") == "output_text":
                                t = part.get("text", "")
                                text.append(t)
                                emit("content", text=t)
                    elif b.get("type") == "reasoning":
                        for summary in b.get("summary", []):
                            t = summary.get("text", "")
                            thought.append(t)
                            emit("reasoning", text=t)
        if not done:
            raise ChatError(
                "Responses stream ended without completion; no tools executed"
            )
        retained: list[dict[str, object]] = (
            completed_output
            if isinstance(completed_output, list)
            else [output_items[index] for index in sorted(output_items)]
        )
        if not text:
            for item in retained:
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if block.get("type") == "output_text":
                            chunk: str = block.get("text", "")
                            text.append(chunk)
                            emit("content", text=chunk)
        if not thought:
            for item in retained:
                if item.get("type") == "reasoning":
                    for part in item.get("summary", []):
                        chunk = part.get("text", "")
                        thought.append(chunk)
                        emit("reasoning", text=chunk)
        if not current:
            for item in retained:
                if item.get("type") == "function_call":
                    current[item.get("id", item["call_id"])] = item
        for b in current.values():
            if b.get("type") == "function_call":
                calls.append(
                    (b["call_id"], b["name"], b.get("arguments", "{}"))
                )
        stats: dict[str, object] = {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "prompt_tokens_details": {
                "cached_tokens": (usage.get("input_tokens_details") or {}).get(
                    "cached_tokens"
                )
            },
        }
        emit("usage", usage=stats)
        normalized: dict[str, object] = result(
            "".join(text),
            calls,
            "tool_calls" if calls else "stop",
            stats,
            reasoning="".join(thought),
        )
        if retained:
            normalized["choices"][0]["message"]["responses_items"] = retained
        return normalized


def complete_native(
    model: Model, messages: list[dict[str, object]],
    tools: list[dict[str, object]], emit: Callable[..., None],
) -> dict[str, object]:
    try:
        if model.provider in ("anthropic", "claude"):
            return anthropic(model, messages, tools, emit)
        if model.provider == "gemini":
            return gemini(model, messages, tools, emit)
        return responses(model, messages, tools, emit)
    except (
        OSError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        raise ChatError(
            "Model request failed; check provider URL, API key, model and"
            " protocol. No tools executed."
        ) from None
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError):
        raise ChatError(
            "Provider returned invalid JSON or tool calls; no tools executed"
        ) from None
