"""Saved model selection, CLI precedence and schema validation."""

import unittest

from history_agent.configuration.defaults import validate_defaults
from history_agent.configuration.models import ModelCatalog
from history_agent.interfaces.arguments import parse_args

CATALOG = {
    "local": {
        "transport": "openai-compatible",
        "base_url": "http://localhost:9000/v1",
        "api_key_env": "LOCAL_KEY",
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
        self.assertEqual(args.model_key_env, "LOCAL_KEY")
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

    def test_catalog_is_validated_and_contains_no_raw_key_field(self):
        for connection in (
            {
                "transport": "invalid",
                "base_url": "https://example.org",
                "models": {"x": {}},
            },
            {
                "base_url": "https://example.org",
                "api_key": "secret",
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

    def test_unknown_selected_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_defaults(
                {"providers": CATALOG, "model_profile": "missing/x"}
            )

    def test_choices_include_every_added_model(self):
        self.assertEqual(len(ModelCatalog.choices(CATALOG)), 3)
        self.assertIn(
            ("local / Reviewer", "local/vendor/reviewer"),
            ModelCatalog.choices(CATALOG),
        )
