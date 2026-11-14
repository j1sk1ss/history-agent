"""Saved model selection, CLI precedence and schema validation."""

import unittest
from unittest.mock import patch

from history_agent.configuration.credentials import model_api_key
from history_agent.configuration.defaults import validate_defaults
from history_agent.configuration.models import ModelCatalog
from history_agent.interfaces.arguments import parse_args

CATALOG = {
    "local": {
        "transport": "openai-compatible",
        "base_url": "http://localhost:9000/v1",
        "api_key": "sk-saved-model-key",
        "models": {
            "vendor/reviewer": {
                "label": "Reviewer",
                "max_context_bytes": 100000,
            },
            "small": {"max_output_tokens": 512},
        },
    },
    "messages": {
        "transport": "anthropic",
        "base_url": "https://model.example/v1",
        "models": {"reviewer": {}},
    },
}


class ModelCatalogTest(unittest.TestCase):
    def test_selection_preserves_model_ids_with_slashes(self):
        args = parse_args(
            [],
            defaults={
                "providers": CATALOG,
                "model_profile": "local/vendor/reviewer",
            },
        )
        self.assertEqual(args.model, "vendor/reviewer")
        self.assertEqual(args.model_url, "http://localhost:9000/v1")
        self.assertEqual(args.model_api_key, "sk-saved-model-key")
        self.assertEqual(args.max_context_bytes, 100000)

    def test_explicit_cli_overrides_selected_model(self):
        args = parse_args(
            [
                "--model-profile=local/small",
                "--max-output-tokens",
                "1234",
                "--model",
                "temporary",
            ],
            defaults={
                "providers": CATALOG,
                "model_profile": "messages/reviewer",
            },
        )
        self.assertEqual(args.provider, "openai-compatible")
        self.assertEqual(args.model, "temporary")
        self.assertEqual(args.max_output_tokens, 1234)

    def test_switching_profile_selects_transport_and_endpoint(self):
        args = parse_args(
            ["--model-profile", "messages/reviewer"],
            defaults={
                "providers": CATALOG,
                "model_profile": "local/small",
            },
        )
        self.assertEqual(args.provider, "anthropic")
        self.assertEqual(args.model_url, "https://model.example/v1")
        self.assertIsNone(args.model_key_env)

    def test_catalog_validates_connection_types_and_limits(self):
        for connection in (
            {
                "transport": "invalid",
                "base_url": "https://example.org",
                "models": {"x": {}},
            },
            {
                "base_url": "https://example.org",
                "api_key": 42,
                "models": {"x": {}},
            },
            {
                "base_url": "https://example.org",
                "models": {"x": {"max_context_bytes": -1}},
            },
        ):
            with (
                self.subTest(connection=connection),
                self.assertRaises(ValueError),
            ):
                ModelCatalog.validate({"test": connection})

    def test_direct_cli_key_overrides_selected_profile_and_environment(self):
        args = parse_args(["--model-api-key", "sk-cli-key"], defaults={
            "providers": CATALOG, "model_profile": "local/small",
        })
        with patch.dict("os.environ", {
            "HISTORY_AGENT_MODEL_API_KEY": "other",
        }):
            self.assertEqual(model_api_key(args), "sk-cli-key")

    def test_invalid_catalog_errors_do_not_echo_the_api_key(self):
        with self.assertRaises(ValueError) as error:
            ModelCatalog.validate({"test": {
                "base_url": "https://example.org", "api_key": "secret-token",
                "models": {"x": {"max_context_bytes": -1}},
            }})
        self.assertNotIn("secret-token", str(error.exception))

    def test_unknown_selected_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_defaults(
                {"providers": CATALOG, "model_profile": "missing/x"}
            )

    def test_invalid_profile_type_is_a_configuration_error(self):
        with self.assertRaises(ValueError):
            validate_defaults({"providers": CATALOG, "model_profile": True})

    def test_remote_profile_drops_implicit_local_template_option(self):
        args = parse_args(
            ["--model-profile", "messages/reviewer"],
            defaults={
                "provider": "llama",
                "no_thinking": True,
                "providers": CATALOG,
            },
        )
        self.assertFalse(args.no_thinking)

    def test_choices_include_every_added_model(self):
        self.assertEqual(len(ModelCatalog.choices(CATALOG)), 3)
        self.assertIn(
            ("local / Reviewer", "local/vendor/reviewer"),
            ModelCatalog.choices(CATALOG),
        )
