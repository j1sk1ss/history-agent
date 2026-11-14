"""Bounded model transport selected by explicit provider settings."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from history_agent.http import NoRedirects
from history_agent.inference.common import ChatError, compact, request_failure
from history_agent.inference.stream import read_completion


@dataclass(frozen=True)
class Model:
    base: str
    name: str
    key: str | None = None
    timeout: float = 60
    max_output_tokens: int = 2048
    no_thinking: bool = False
    stream: bool = True
    stream_usage: bool = True
    provider: str = "openai-compatible"

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_event: Callable[..., None] | None = None,
    ) -> dict[str, object]:
        emit: Callable[..., None] = on_event or (lambda *args, **kwargs: None)
        if self.provider in (
            "anthropic",
            "claude",
            "gemini",
            "openai-responses",
            "codex",
        ):
            from history_agent.inference.providers import complete_native

            return complete_native(self, messages, tools, emit)
        payload: dict[str, object] = self.payload(messages, tools)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        request: urllib.request.Request = urllib.request.Request(
            self.base + "/chat/completions",
            data=compact(payload).encode("utf-8"),
            headers=headers,
        )
        opener: urllib.request.OpenerDirector = urllib.request.build_opener(
            NoRedirects()
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return read_completion(response, emit)
        except (
            OSError,
            urllib.error.URLError,
            http.client.HTTPException,
        ) as error:
            detail = request_failure(error)
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise ChatError(
                detail + " No automatic retry; no tools "
                "from this response were executed."
            ) from None
        except (ValueError, UnicodeError, KeyError, TypeError, AttributeError):
            raise ChatError(
                (
                    "Model returned an invalid response or stream; no"
                    " tools from this response were executed"
                )
            ) from None

    def payload(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.name,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "stream": self.stream,
        }
        if tools:
            payload["tools"] = tools
        if self.stream and self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if self.no_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload
