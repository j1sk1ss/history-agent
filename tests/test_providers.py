"""
Local mocked API protocol tests; no API keys or external services required.
"""

import unittest

from history_agent.inference.model import Model
from history_agent.inference.providers import gemini_messages, responses_input
from tests.test_streaming import StreamingServer

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "history_current",
            "description": "Select current scope",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
MESSAGES = [
    {"role": "system", "content": "Review warnings"},
    {"role": "user", "content": "Annotate"},
]


class NativeProviderTest(unittest.TestCase):
    def test_anthropic_tool_stream_and_usage(self):
        chunks = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 15}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "history_current",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        ]
        with StreamingServer([chunks]) as server:
            response = Model(
                server.url + "/v1", "test", key="secret", provider="anthropic"
            ).complete(MESSAGES, TOOLS)
            self.assertEqual(
                response["choices"][0]["finish_reason"], "tool_calls"
            )
            self.assertEqual(
                response["choices"][0]["message"]["tool_calls"][0]["id"],
                "tu_1",
            )
            self.assertEqual(response["usage"]["prompt_tokens"], 15)
            self.assertEqual(
                server.requests[0]["tools"][0]["name"], "history_current"
            )

    def test_gemini_tool_and_thought_signature_roundtrip(self):
        part = {
            "functionCall": {"name": "history_current", "args": {}},
            "thoughtSignature": "sample-signature",
        }
        chunks = [
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": "provider-supplied reasoning",
                                    "thought": True,
                                },
                                part,
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 9,
                    "candidatesTokenCount": 4,
                },
            }
        ]
        with StreamingServer([chunks]) as server:
            message = Model(
                server.url, "gemini-test", key="secret", provider="gemini"
            ).complete(MESSAGES, TOOLS)["choices"][0]["message"]
            self.assertEqual(
                message["reasoning_content"], "provider-supplied reasoning"
            )
            tool = message["tool_calls"][0]
            _, converted = gemini_messages(
                MESSAGES
                + [
                    message,
                    {
                        "role": "tool",
                        "tool_call_id": tool["id"],
                        "content": '{"project_id":2}',
                    },
                ]
            )
            self.assertEqual(
                converted[-2]["parts"][-1]["thoughtSignature"],
                "sample-signature",
            )
            self.assertEqual(
                converted[-1]["parts"][0]["functionResponse"]["name"],
                "history_current",
            )

    def test_codex_responses_preserves_opaque_reasoning_item_and_call_id(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "encrypted_content": "opaque-test",
        }
        tool = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "history_current",
            "arguments": "{}",
        }
        chunks = [
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": reasoning,
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": tool,
            },
            {
                "type": "response.completed",
                "response": {
                    "output": [reasoning, tool],
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                },
            },
        ]
        with StreamingServer([chunks]) as server:
            message = Model(
                server.url + "/v1",
                "codex-test",
                key="secret",
                provider="codex",
            ).complete(MESSAGES, TOOLS)["choices"][0]["message"]
            self.assertEqual(message["tool_calls"][0]["id"], "call_1")
            history = responses_input(
                MESSAGES
                + [
                    message,
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": "{}",
                    },
                ]
            )
            self.assertEqual(history[-3], reasoning)
            self.assertEqual(history[-2], tool)
            self.assertEqual(history[-1]["type"], "function_call_output")
            self.assertEqual(
                server.requests[0]["reasoning"], {"summary": "auto"}
            )


if __name__ == "__main__":
    unittest.main()
