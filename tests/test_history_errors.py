"""Read failures identify the action and cause without claiming a write."""

import http.client
import socket
import unittest
from unittest.mock import patch
from urllib.error import URLError

from history_agent.history.settings import HistoryError, Settings
from history_agent.history.transport import HistoryTransport


class HistoryTransportErrorTest(unittest.TestCase):
    def test_read_diagnostics_explain_the_cause_without_exposing_secrets(self):
        transport = HistoryTransport(
            Settings(
                "https://history.example",
                "tester",
                "credential-secret",
                session="session-secret",
                timeout=37,
            )
        )
        for error, expected in (
            (
                URLError(TimeoutError("credential-secret")),
                "timed out after 37",
            ),
            (
                URLError(socket.gaierror(-2, "credential-secret")),
                "cannot resolve",
            ),
            (URLError(ConnectionRefusedError("session-secret")), "refused"),
            (
                http.client.IncompleteRead(b"credential-secret", 5),
                "interrupted",
            ),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    transport._opener,
                    "open",
                    side_effect=error,
                ),
            ):
                with self.assertRaises(HistoryError) as caught:
                    transport._call("agent_compare_warnings")
                detail = str(caught.exception)
                self.assertIn("agent_compare_warnings", detail)
                self.assertIn(expected, detail)
                for private in (
                    "credential-secret",
                    "session-secret",
                    "history.example",
                    "outcome may be unknown",
                    "Inspect review history",
                ):
                    self.assertNotIn(private, detail)

    def test_interrupted_write_retains_the_unknown_outcome_warning(self):
        transport = HistoryTransport(Settings("https://history.example"))
        with (
            patch.object(
                transport._opener,
                "open",
                side_effect=ConnectionResetError(),
            ),
            self.assertRaises(HistoryError) as caught,
        ):
            transport._call("agent_set_review")
        self.assertIn("agent_set_review", str(caught.exception))
        self.assertIn("outcome may be unknown", str(caught.exception))
        self.assertIn("Inspect review history", str(caught.exception))
