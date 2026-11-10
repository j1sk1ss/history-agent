import os
import unittest
from unittest.mock import patch

from history_agent.configuration.probes import (
    probe_model_connection,
    probe_system_one_connection,
)
from history_agent.inference.model import ChatError, Model
from history_agent.interfaces.arguments import parse_args
from history_agent.review.system_one import SystemOne


class ConnectionProbeTest(unittest.TestCase):
    def test_main_probe_uses_the_selected_model_endpoint_and_key(self):
        args = parse_args([
            "--model-url", "https://model.example/v1/chat/completions",
            "--model", "selected-model", "--model-key-env", "TEST_MODEL_KEY",
        ])
        with (
            patch.dict(os.environ, {"TEST_MODEL_KEY": "test-key"}),
            patch.object(
                Model, "complete", autospec=True,
                return_value={"choices": [{}]},
            ) as complete,
        ):
            connected, _ = probe_model_connection(args)
        self.assertTrue(connected)
        model = complete.call_args.args[0]
        self.assertEqual(model.base, "https://model.example/v1")
        self.assertEqual(model.name, "selected-model")
        self.assertEqual(model.key, "test-key")
        self.assertLessEqual(model.timeout, 5)

    def test_rejected_main_model_is_not_reported_as_connected(self):
        args = parse_args([
            "--model-url", "https://model.example/v1", "--model", "missing",
        ])
        with patch.object(
            Model, "complete", side_effect=ChatError("Not found")
        ):
            connected, detail = probe_model_connection(args)
        self.assertFalse(connected)
        self.assertEqual(detail, "Not found")

    def test_local_model_is_discovered_with_a_short_timeout(self):
        args = parse_args(["--model-url", "https://model.example"])
        with patch(
            "history_agent.configuration.probes.discover_model",
            return_value="only-model",
        ) as discover, patch.object(
            Model, "complete", autospec=True, return_value={"choices": [{}]},
        ) as complete:
            connected, _ = probe_model_connection(args)
        self.assertTrue(connected)
        self.assertEqual(complete.call_args.args[0].name, "only-model")
        self.assertEqual(discover.call_args.kwargs["timeout"], 5)

    def test_remote_provider_keeps_its_transport(self):
        args = parse_args([
            "--provider", "anthropic", "--model", "selected-model",
            "--model-url", "https://model.example/v1/messages",
            "--model-key-env", "TEST_MODEL_KEY",
        ])
        with (
            patch.dict(os.environ, {"TEST_MODEL_KEY": "test-key"}),
            patch.object(
                Model, "complete", autospec=True,
                return_value={"choices": [{}]},
            ) as complete,
        ):
            connected, _ = probe_model_connection(args)
        self.assertTrue(connected)
        model = complete.call_args.args[0]
        self.assertEqual(model.provider, "anthropic")
        self.assertEqual(model.base, "https://model.example/v1")

    def test_system_one_disabled_success_and_failure(self):
        with patch.object(SystemOne, "probe", autospec=True) as probe:
            self.assertFalse(probe_system_one_connection(parse_args([]))[0])
            probe.assert_not_called()
            args = parse_args(["--system-one-model", "selected-fast-model"])
            for available in (True, False):
                probe.return_value = available
                self.assertEqual(
                    probe_system_one_connection(args)[0], available
                )
                self.assertEqual(
                    probe.call_args.args[0].model, "selected-fast-model"
                )
