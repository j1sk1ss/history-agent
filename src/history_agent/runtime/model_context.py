"""Translate model messages without changing host evidence or journals."""

import copy
import json
import re

from history_agent.inference.model import compact
from history_agent.review.verdict import VerdictFormatError, extract_verdict
from history_agent.runtime.identities import INSTRUCTIONS, IdentityMap
from history_agent.runtime.structured import json_fragments


def hide_host_locations(value: object) -> object:
    if isinstance(value, list):
        return [hide_host_locations(entry) for entry in value]
    if isinstance(value, dict):
        return {
            key: "Checkpoint managed by the host; resume with job_id"
            if key == "checkpoint" and isinstance(entry, str)
            else "History link resolved by the host"
            if key in ("url", "history_url")
            and isinstance(entry, str)
            and (
                key == "history_url"
                or re.search(r"#(?:show|compare_svace2svace)/", entry)
            )
            else hide_host_locations(entry)
            for key, entry in value.items()
        }
    return value


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
        return hide_host_locations(self.names.encode(value))

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
                    self.packet(json.loads(function["arguments"]))
                )
        if result and result[0]["role"] == "system":
            result[0]["content"] += " " + INSTRUCTIONS
        return result

    def tools(self, tools: list[dict[str, object]]) -> list[dict[str, object]]:
        result = [tool for tool in copy.deepcopy(tools)
                  if tool["function"]["parameters"].get("properties", {})
                  .get("action", {}).get("enum") != []]
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
