"""
Opt-in native tool-call probe: real model -> MCP client -> isolated history
fixture.
"""

import argparse
import asyncio
import json
import urllib.request

from mcp import Client

from history_agent.client import HistoryClient, Settings
from history_agent.server import create_server
from tests.fake_history import FakeHistory


def completion(url, model, messages, tools):
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "max_tokens": 384,
        "temperature": 0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


async def probe(url, model):
    with FakeHistory() as fake:
        server = create_server(
            HistoryClient(Settings(fake.url, "tester", "test-secret"))
        )
        async with Client(server) as client:
            available = (await client.list_tools()).tools
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in available
                if tool.name in {"history_discover", "history_read"}
            ]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are testing a history integration. Use "
                        "the supplied "
                        "tools; do not invent results. "
                        "First discover the schema of agent_source, "
                        "then read the "
                        "requested address with history_read. "
                        "After the source tool succeeds, answer with "
                        "the returned "
                        "source line. Do not request workflow."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Read the source at project_id 0, snapshot_id "
                        "'snapshot', "
                        "file 'src/a.c', line 3. "
                        "Use a window of zero lines before and after."
                    ),
                },
            ]
            steps = []
            for _ in range(8):
                result = await asyncio.to_thread(
                    completion, url, model, messages, tools
                )
                message = result["choices"][0]["message"]
                messages.append(message)
                calls = message.get("tool_calls", [])
                if not calls:
                    if not any(
                        step["tool"] == "history_read" and step["ok"]
                        for step in steps
                    ):
                        raise RuntimeError(
                            "Model answered before reading the "
                            "synthetic source"
                        )
                    if fake.writes:
                        raise RuntimeError("Probe unexpectedly wrote a review")
                    return {
                        "model": model,
                        "steps": steps,
                        "answer": message.get("content", ""),
                        "history_logins": fake.logins,
                        "real_history_writes": 0,
                    }
                for call in calls:
                    function = call["function"]
                    if function["name"] not in {
                        "history_discover",
                        "history_read",
                    }:
                        raise RuntimeError(
                            "Model requested an unavailable tool"
                        )
                    arguments = json.loads(function["arguments"])
                    outcome = await client.call_tool(
                        function["name"], arguments
                    )
                    steps.append(
                        {
                            "tool": function["name"],
                            "action": arguments.get("action"),
                            "ok": not outcome.is_error,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": "\n".join(
                                part.text
                                for part in outcome.content
                                if part.type == "text"
                            ),
                        }
                    )
            raise RuntimeError("Probe exceeded eight model turns")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="qwen-local")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(probe(args.url, args.model)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
