import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from history_agent.cli import history_settings, main, parse_args
from history_agent.client import Settings
from history_agent.config import (
    config_path,
    load_defaults,
    save_defaults,
    validate_defaults,
)


class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings" / "chat.json"
        environment = patch.dict(
            os.environ, {"HISTORY_AGENT_CONFIG": str(self.path)}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_only_explicit_save_persists_reusable_settings(self):
        args = parse_args(
            [
                "--model-url",
                "https://model.example/v1",
                "--model",
                "qwen",
                "--prompt",
                "Review this",
                "--plain",
                "--allow-write",
                "--no-thinking",
            ]
        )
        self.assertEqual(load_defaults(), {})
        save_defaults(args)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["model"], "qwen")
        self.assertTrue(data["no_thinking"])
        self.assertNotIn("prompt", data)
        self.assertNotIn("ui", data)
        self.assertNotIn("batch_state_dir", data)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            parse_args([], defaults=load_defaults()).model, "qwen"
        )

    def test_cli_overrides_saved_values_without_writing(
        self,
    ):
        saved = parse_args(
            [
                "--provider",
                "llama",
                "--model",
                "saved",
                "--allow-write",
                "--no-thinking",
                "--no-stream",
                "--no-stream-usage",
                "--no-patterns",
                "--stats",
            ]
        )
        save_defaults(saved)
        before = self.path.read_bytes()
        args = parse_args(
            [
                "--model",
                "temporary",
                "--max-steps",
                "3",
                "--read-only",
                "--thinking",
                "--stream",
                "--stream-usage",
                "--patterns",
                "--no-stats",
            ],
            defaults=load_defaults(),
        )
        self.assertEqual(args.provider, "llama")
        self.assertEqual(args.model, "temporary")
        self.assertEqual(args.max_steps, 3)
        for name in (
            "allow_write",
            "no_thinking",
            "no_stream",
            "no_stream_usage",
            "no_patterns",
            "stats",
        ):
            self.assertFalse(getattr(args, name), name)
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_credentials_round_trip_and_override_old_session(self):
        args = parse_args([])
        args.history_user = "reviewer"
        args.history_password = " secret with spaces "
        save_defaults(args)
        restored = parse_args([], defaults=load_defaults())
        with patch(
            "history_agent.cli.Settings.from_env",
            return_value=Settings(
                "http://history.example", "old", "old", "stale-session"
            ),
        ):
            settings = history_settings(restored)
        self.assertEqual(settings.user, "reviewer")
        self.assertEqual(settings.password, " secret with spaces ")
        self.assertIsNone(settings.session)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "both"):
            validate_defaults({"history_user": "reviewer"})

    def test_fatal_reload_resumes_job_without_reopening_configuration(self):
        configured = parse_args(["--prompt", "Review all"])
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            editor.return_value.run.return_value = configured
            backend.side_effect = [{"reload_job": "a" * 32}, None]
            main([])
        editor.assert_called_once()
        self.assertEqual(backend.await_count, 2)
        self.assertEqual(configured.resume, "a" * 32)
        self.assertIsNone(configured.prompt)

    def test_first_bare_launch_opens_configuration_before_backend(self):
        configured = parse_args(["--provider", "llama", "--model", "qwen"])
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            editor.return_value.run.return_value = configured
            main([])
        editor.assert_called_once()
        backend.assert_awaited_once_with(configured)

    def test_saved_bare_launch_skips_editor_and_cli_overrides_are_temporary(
        self,
    ):
        save_defaults(parse_args(["--provider", "llama", "--model", "saved"]))
        before = self.path.read_bytes()
        with (
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            main([])
            self.assertEqual(backend.await_args.args[0].model, "saved")
            main(["--model", "temporary"])
            self.assertEqual(backend.await_args.args[0].model, "temporary")
        editor.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_one_off_launch_does_not_create_defaults(self):
        with (
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            main(["--provider", "llama", "--model", "qwen", "--plain"])
        backend.assert_awaited_once()
        editor.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_explicit_editor_and_editor_requested_from_chat(self):
        configured = parse_args(["--provider", "llama", "--model", "qwen"])
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            editor.return_value.run.return_value = configured
            backend.side_effect = ["configure", None]
            main(["--configure"])
        self.assertEqual(editor.call_count, 2)
        self.assertEqual(backend.await_count, 2)

    def test_cancelling_first_configuration_does_not_start_or_save(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            editor.return_value.run.return_value = None
            main([])
        backend.assert_not_awaited()
        self.assertFalse(self.path.exists())

    def test_saved_chat_settings_reconnect_without_reopening_editor(self):
        configured = parse_args(
            ["--provider", "llama", "--model", "updated", "--prompt", "Old"]
        )
        with (
            patch("history_agent.ui.config.ConfigurationApp") as editor,
            patch("history_agent.cli.run", new_callable=AsyncMock) as backend,
        ):
            backend.side_effect = [{"settings": configured}, None]
            main(["--provider", "llama", "--model", "original"])
        editor.assert_not_called()
        self.assertEqual(backend.await_count, 2)
        self.assertIs(backend.await_args.args[0], configured)
        self.assertIsNone(configured.prompt)

    def test_non_terminal_first_launch_has_short_actionable_error(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            self.assertRaisesRegex(SystemExit, "--configure"),
        ):
            main([])
        self.assertFalse(self.path.exists())

    def test_invalid_defaults_and_secrets_are_rejected(self):
        for values in (
            {"api_key": "secret"},
            {"provider": "unknown"},
            {"max_steps": 0},
            {"timeout": float("nan")},
            {"no_thinking": "false"},
            {"max_steps": True},
            {"model_url": "https://user:secret@example.org"},
            {"model_key_env": "not a name"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_defaults(values)
        self.path.parent.mkdir()
        self.path.write_text("{broken")
        with self.assertRaisesRegex(
            ValueError, "Cannot read chat configuration"
        ):
            load_defaults()
        # Help remains available even if defaults need manual repair.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            main(["--help"])
        self.assertEqual(result.exception.code, 0)

    def test_xdg_config_location(self):
        with patch.dict(
            os.environ,
            {
                "HISTORY_AGENT_CONFIG": "",
                "XDG_CONFIG_HOME": "/tmp/custom-config",
            },
        ):
            self.assertEqual(
                config_path(),
                Path("/tmp/custom-config/history-agent/config.json"),
            )

    def test_explicit_legacy_interface_works_with_redirected_input(self):
        self.assertTrue(parse_args(["--legacy-ui"]).ui)
