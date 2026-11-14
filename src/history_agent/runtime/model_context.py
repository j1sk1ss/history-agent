"""Translate model messages without changing host evidence or journals."""

import copy
import json
import re

from history_agent.inference.model import compact
from history_agent.review.verdict import VerdictFormatError, extract_verdict
from history_agent.runtime.identities import INSTRUCTIONS, IdentityMap
from history_agent.runtime.structured import json_fragments


class ModelContext:
    def __init__(self) -> None:
        self.names = IdentityMap()

    def text(self, value: str) -> str:
        """Translate JSON packets embedded in host prompts."""
        value = json_fragments(value, self.packet)
        value = re.sub(
            r"https?://[^\s<>\"'`]+#(?:show|compare_svace2svace)/[^\s<>\"'`]+",
            "[History link resolved by the host]",
            value,
        )
        return value

    def packet(self, value: object) -> object:
        if isinstance(value, list):
            return [self.packet(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        encoded = self.names.encode(value)
        # A resolved URL's structured read/review arguments are authoritative.
        for key in ("url", "history_url"):
            if key in encoded:
                encoded[key] = "History link resolved by the host"
        return encoded

    def messages(
        self, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        result = copy.deepcopy(messages)
        for message in result:
            content = message.get("content")
            if isinstance(content, str):
                if message["role"] == "tool":
                    try:
                        message["content"] = compact(
                            self.packet(json.loads(content))
                        )
                    except ValueError:
                        message["content"] = content
                elif message["role"] != "assistant":
                    message["content"] = self.text(content)
            for call in message.get("tool_calls", []):
                function = call["function"]
                function["arguments"] = compact(
                    self.names.encode(json.loads(function["arguments"]))
                )
        if result and result[0]["role"] == "system":
            result[0]["content"] += " " + INSTRUCTIONS
        return result

    def tools(self, tools: list[dict[str, object]]) -> list[dict[str, object]]:
        result = copy.deepcopy(tools)
        for tool in result:
            function = tool["function"]
            function["parameters"] = self.names.schema(function["parameters"])
        return result

    def verdict(self, content: str) -> str:
        try:
            value = extract_verdict(content)
        except VerdictFormatError:
            return content
        return compact(self.names.decode(value))
