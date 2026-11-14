"""Useful model transport failures without exposing untrusted server data."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from history_agent.inference.model import ChatError, Model


class ModelErrorTest(unittest.TestCase):
    def request_failure(self, error):
        model = Model(
            "https://proxy.example/v1",
            "team/reviewer",
            "credential-secret",
            provider="litellm",
            timeout=45,
        )
        with patch(
            "history_agent.inference.model.urllib.request.build_opener",
        ) as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(ChatError) as caught:
                model.complete([{"role": "user", "content": "Reply OK."}], [])
        build.return_value.open.assert_called_once()
        return str(caught.exception)

    def test_http_status_identifies_rejection_without_echoing_secrets(self):
        for status, explanation in (
            (400, "request|parameter|model"),
            (401, "auth|key|permission"),
            (404, "endpoint|model"),
        ):
            with self.subTest(status=status):
                body = io.BytesIO(
                    b'{"error": "credential-secret body-secret"}'
                )
                error = HTTPError(
                    "https://proxy.example/v1?token=url-secret",
                    status,
                    "reason-secret",
                    {},
                    body,
                )
                detail = self.request_failure(error)
                self.assertIn(f"HTTP {status}", detail)
                self.assertRegex(detail.lower(), explanation)
                for secret in (
                    "credential-secret",
                    "body-secret",
                    "url-secret",
                    "reason-secret",
                ):
                    self.assertNotIn(secret, detail)
                self.assertTrue(body.closed)

    def test_direct_and_wrapped_timeouts_are_identified_without_reason(self):
        for error in (
            TimeoutError("credential-secret"),
            URLError(TimeoutError("credential-secret")),
        ):
            with self.subTest(error=type(error).__name__):
                detail = self.request_failure(error)
                self.assertIn("timed out", detail.lower())
                self.assertNotIn("credential-secret", detail)

    def test_400_identifies_supported_parameter_without_echoing_body(self):
        body = io.BytesIO(
            b'{"error":{"param":"stream_options",'
            b'"message":"credential-secret private-body"}}'
        )
        detail = self.request_failure(
            HTTPError(
                "https://proxy.example/v1",
                400,
                "Bad Request",
                {},
                body,
            )
        )
        self.assertIn("Rejected parameter: stream_options", detail)
        self.assertIn("Disable streaming usage", detail)
        self.assertNotIn("private-body", detail)
        self.assertTrue(body.closed)

    def test_litellm_nested_limit_error_explains_real_backend_rejection(self):
        nested = {
            "error": {
                "message": "max_tokens=999999 cannot be greater than "
                "max_model_len=262144. Please request fewer output tokens. "
                "credential-secret private-body",
                "type": "BadRequestError",
                "param": "max_tokens",
                "code": 400,
            }
        }
        body = io.BytesIO(
            json.dumps(
                {
                    "error": {
                        "message": "litellm.BadRequestError: "
                        "Hosted_vllmException - "
                        + json.dumps(nested)
                        + "\nLiteLLM: no fallback was attempted.",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "400",
                    }
                }
            ).encode()
        )
        detail = self.request_failure(
            HTTPError(
                "https://proxy.example/v1",
                400,
                "Bad Request",
                {},
                body,
            )
        )
        self.assertIn("max_tokens=999999", detail)
        self.assertIn("max_model_len=262144", detail)
        self.assertIn("Lower Output tokens per request", detail)
        self.assertNotIn("credential-secret", detail)
        self.assertNotIn("private-body", detail)
        self.assertTrue(body.closed)

    def test_embedded_parameter_hint_survives_null_proxy_parameter(self):
        nested = {
            "error": {
                "param": "stream_options",
                "message": "Unsupported private-body",
            }
        }
        body = io.BytesIO(
            json.dumps(
                {
                    "error": {
                        "param": None,
                        "message": "Proxy: " + json.dumps(nested),
                    }
                }
            ).encode()
        )
        detail = self.request_failure(
            HTTPError(
                "https://proxy.example/v1",
                400,
                "Bad Request",
                {},
                body,
            )
        )
        self.assertIn("Rejected parameter: stream_options", detail)
        self.assertNotIn("private-body", detail)

    def test_malformed_and_unknown_errors_remain_bounded_and_private(self):
        for value in (
            b"<html>credential-secret private-body</html>",
            b'{"error":{"param":"credential-secret",'
            b'"message":"private-body"}}',
        ):
            with self.subTest(value=value):
                detail = self.request_failure(
                    HTTPError(
                        "https://proxy.example/v1",
                        400,
                        "Bad Request",
                        {},
                        io.BytesIO(value),
                    )
                )
                self.assertIn("HTTP 400", detail)
                self.assertNotIn("credential-secret", detail)
                self.assertNotIn("private-body", detail)

    def test_litellm_system_message_order_rejection_is_explained(self):
        nested = {"error": {
            "message": "System message must be at the beginning. private-body",
            "param": None,
        }}
        body = io.BytesIO(json.dumps({"error": {
            "message": "Hosted_vllmException - " + json.dumps(nested),
            "param": None,
        }}).encode())
        detail = self.request_failure(HTTPError(
            "https://proxy.example/v1", 400, "Bad Request", {}, body,
        ))
        self.assertIn(
            "system instructions must be in the initial message", detail
        )
        self.assertNotIn("private-body", detail)
        self.assertTrue(body.closed)
