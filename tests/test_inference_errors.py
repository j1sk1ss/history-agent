"""Useful model transport failures without exposing untrusted server data."""

import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from history_agent.inference.model import ChatError, Model


class ModelErrorTest(unittest.TestCase):
    def request_failure(self, error):
        model = Model(
            "https://proxy.example/v1", "team/reviewer", "credential-secret",
            provider="litellm", timeout=45,
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
                    "https://proxy.example/v1?token=url-secret", status,
                    "reason-secret", {}, body,
                )
                detail = self.request_failure(error)
                self.assertIn(f"HTTP {status}", detail)
                self.assertRegex(detail.lower(), explanation)
                for secret in (
                    "credential-secret", "body-secret", "url-secret",
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
