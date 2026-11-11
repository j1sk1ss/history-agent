"""Provider-specific continuation data must survive the harness unchanged."""

import unittest

from history_agent.inference.anthropic import AnthropicState
from history_agent.inference.conversations import (
    api_messages,
    gemini_messages,
    responses_input,
)
from history_agent.runtime.parsing import parse_message


class ProviderContextTest(unittest.TestCase):
    def state(self):
        return AnthropicState(lambda *args, **kwargs: None)

    def test_streamed_anthropic_signature_and_block_order_are_preserved(self):
        state = self.state()
        blocks = [
            {"type": "thinking", "thinking": "", "signature": ""},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": ""},
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "ask_user",
                "input": {},
            },
        ]
        for index, block in enumerate(blocks):
            state.feed(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": block,
                }
            )
        for index, kind, field, value in (
            (0, "thinking_delta", "thinking", "provider reasoning"),
            (0, "signature_delta", "signature", "signed-"),
            (0, "signature_delta", "signature", "value"),
            (2, "text_delta", "text", "Need project context"),
            (
                3,
                "input_json_delta",
                "partial_json",
                '{"question":"Allowed input?"}',
            ),
        ):
            state.feed(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": kind, field: value},
                }
            )
        state.feed(
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
        )
        state.feed({"type": "message_stop"})
        message, _ = parse_message(state.build_result(), {"ask_user"})
        _, converted = api_messages(
            [
                {"role": "system", "content": "Review"},
                {"role": "user", "content": "Warning"},
                message,
                {
                    "role": "tool",
                    "tool_call_id": "tool-1",
                    "content": '{"answer":"Yes"}',
                },
            ],
            "anthropic",
        )
        retained = converted[-2]["content"]
        self.assertEqual(
            [b["type"] for b in retained], [b["type"] for b in blocks]
        )
        self.assertEqual(retained[0]["thinking"], "provider reasoning")
        self.assertEqual(retained[0]["signature"], "signed-value")
        self.assertEqual(retained[1], blocks[1])
        self.assertEqual(retained[2]["text"], "Need project context")
        self.assertEqual(retained[3]["input"], {"question": "Allowed input?"})
        self.assertNotIn("json_fragments", retained[3])

    def test_nonstream_anthropic_keeps_signed_reasoning_and_tool_identity(
        self,
    ):
        blocks = [
            {
                "type": "thinking",
                "thinking": "provider reasoning",
                "signature": "signature",
            },
            {
                "type": "tool_use",
                "id": "tool-2",
                "name": "ask_user",
                "input": {"question": "Policy?"},
            },
        ]
        state = self.state()
        state.feed(
            {"type": "message", "stop_reason": "tool_use", "content": blocks}
        )
        message, calls = parse_message(state.build_result(), {"ask_user"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(message["anthropic_blocks"], blocks)

    def test_question_answers_keep_tool_identity_in_every_native_format(self):
        messages = [
            {"role": "system", "content": "Review"},
            {"role": "user", "content": "Warning"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "question-1",
                        "type": "function",
                        "function": {
                            "name": "ask_user",
                            "arguments": '{"question":"Policy?"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "question-1",
                "content": '{"answer":"Allowed"}',
            },
        ]
        system, anthropic = api_messages(messages, "anthropic")
        self.assertEqual(system, "Review")
        self.assertEqual(
            anthropic[-1]["content"][0]["tool_use_id"], "question-1"
        )
        self.assertEqual(
            responses_input(messages)[-1]["call_id"], "question-1"
        )
        system, gemini = gemini_messages(messages)
        self.assertEqual(system, "Review")
        response = gemini[-1]["parts"][0]["functionResponse"]
        self.assertEqual(
            response, {"name": "ask_user", "response": {"answer": "Allowed"}}
        )
