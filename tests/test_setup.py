import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from history_agent.configuration.connection import discover_model
from history_agent.configuration.constants import CLIENTS
from history_agent.configuration.files import (
    Edit,
    read_data,
    write_plan,
)
from history_agent.configuration.setup import build_plan, main
from history_agent.configuration.setup_args import parse_args
from history_agent.history.client import Settings


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="history-agent-setup-test-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def args(self, client, *extra):
        return parse_args(
            [
                "--client",
                client,
                "--model-url",
                "https://models.example/tenant/v1",
                "--history-url",
                "https://history.example/deployment",
                "--model",
                "qwen",
                "--config-root",
                str(self.root),
                *extra,
            ]
        )

    def test_all_six_clients_round_trip_and_keep_unrelated_settings(self):
        for client in CLIENTS:
            with self.subTest(client=client):
                root = self.root / client
                args = self.args(client, "--config-root", str(root))
                plan = build_plan(args)
                # Existing files contain data the installer does not own.
                for edit in plan:
                    edit.path.parent.mkdir(parents=True, exist_ok=True)
                    if edit.format == "providers":
                        edit.path.write_text(
                            '[{"name":"existing","vendor":"other",'
                            '"secret":"keep"}]'
                        )
                    elif edit.format == "toml":
                        edit.path.write_text(
                            "# keep comment\n[profiles.personal]\n"
                            'model="keep"\n'
                            '[mcp_servers.other]\ncommand="keep"\n'
                        )
                    elif edit.format == "yaml":
                        edit.path.write_text(
                            "# keep comment\nother: keep\nmcp_servers:"
                            "\n  existing:\n    "
                            "command: keep\n"
                        )
                    elif edit.format == "dotenv":
                        edit.path.write_text("# keep comment\nOTHER=keep\n")
                    else:
                        edit.path.write_text('{"other":"keep"}')
                changed, backups = write_plan(plan)
                self.assertEqual(len(changed), len(plan))
                self.assertEqual(len(backups), len(plan))
                for edit in plan:
                    if edit.format == "dotenv":
                        self.assertIn("OTHER=keep", edit.path.read_text())
                        self.assertIn(
                            'OPENAI_API_KEY=""', edit.path.read_text()
                        )
                        continue
                    data = read_data(edit.path, edit.format)
                    if edit.format == "providers":
                        self.assertEqual(data[0]["secret"], "keep")
                        self.assertEqual(
                            data[1]["models"][0]["url"], args.model_endpoint
                        )
                        self.assertTrue(data[1]["models"][0]["toolCalling"])
                    elif edit.format == "toml":
                        self.assertEqual(
                            data["profiles"]["personal"]["model"], "keep"
                        )
                        self.assertEqual(
                            data["mcp_servers"]["other"]["command"], "keep"
                        )
                        self.assertIn("# keep comment", edit.path.read_text())
                    else:
                        self.assertEqual(data["other"], "keep")
                    self.assertEqual(edit.path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(write_plan(plan), ([], []))

    def test_protocol_mismatch_stops_before_changes(self):
        for client, url in [
            ("codex", "https://m/v1/chat/completions"),
            ("claude-code", "https://m/v1/responses"),
        ]:
            with self.subTest(client=client):
                args = self.args(client, "--model-url", url)
                with self.assertRaisesRegex(ValueError, "compatible endpoint"):
                    build_plan(args)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_messages_and_responses_urls_have_no_double_version(self):
        for client, api in [
            ("claude-code", "messages"),
            ("codex", "responses"),
        ]:
            args = self.args(
                client, "--model-url", "https://m/prefix/v1/" + api
            )
            build_plan(args)
            self.assertEqual(args.model_base, "https://m/prefix/v1")
            self.assertEqual(args.model_endpoint, "https://m/prefix/v1/" + api)

    def test_dry_run_has_no_files_or_secret_output(self):
        os.environ.update(
            HISTORY_PASSWORD="secret-history",
            HISTORY_USER="tester",
            HISTORY_AGENT_MODEL_API_KEY="secret-model",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(args=self.args("copilot", "--dry-run"))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn("secret-history", output.getvalue())
        self.assertNotIn("secret-model", output.getvalue())

    def test_malformed_existing_file_does_not_change_earlier_file(self):
        first, second = self.root / "first.json", self.root / "second.json"
        first.write_text('{"keep":true}')
        second.write_text("{broken")
        with self.assertRaises(ValueError):
            write_plan(
                [
                    Edit(first, "json", {"new": True}),
                    Edit(second, "json", {"new": True}),
                ]
            )
        self.assertEqual(first.read_text(), '{"keep":true}')
        self.assertEqual(list(self.root.glob("*.bak")), [])

    def test_write_failure_rolls_back_changed_files(self):
        from history_agent.configuration import files as setup

        first, second, third = (
            self.root / name
            for name in ("first.json", "second.json", "third.json")
        )
        original = b'{"keep":true}'
        first.write_bytes(original)
        write = setup.private_write

        def fail_last(path, content):
            if path == third:
                raise OSError("simulated write failure")
            write(path, content)

        with (
            patch.object(setup, "private_write", side_effect=fail_last),
            self.assertRaises(OSError),
        ):
            write_plan(
                [
                    Edit(path, "json", {"new": True})
                    for path in (first, second, third)
                ]
            )
        self.assertEqual(first.read_bytes(), original)
        self.assertFalse(second.exists())
        self.assertFalse(third.exists())
        self.assertEqual(
            next(self.root.glob("first.json.*.bak")).read_bytes(), original
        )

    def test_existing_opencode_legacy_schema_is_preserved(self):
        path = self.root / ".config/opencode/opencode.jsonc"
        path.parent.mkdir(parents=True)
        path.write_text(
            '// existing\n{provider: {other: {name:"keep"}}, '
            'mcp:{other:{type:"local",command:["keep"]}}}'
        )
        plan = build_plan(self.args("opencode"))
        write_plan(plan)
        data = read_data(path, "json")
        self.assertEqual(data["provider"]["other"]["name"], "keep")
        self.assertIn("history-agent", data["provider"])
        self.assertNotIn("providers", data)
        self.assertIn("history-mcp", data["mcp"])

    def test_private_history_config_and_environment_overrides(self):
        os.environ.update(
            HISTORY_USER="reviewer", HISTORY_PASSWORD="private-history"
        )
        plan = build_plan(self.args("copilot"))
        write_plan(plan)
        config_path = plan[0].path
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        with patch.dict(
            os.environ, {"HISTORY_CONFIG": str(config_path)}, clear=True
        ):
            config = Settings.from_env()
            self.assertEqual(config.url, "https://history.example/deployment")
            self.assertEqual(config.password, "private-history")
            os.environ["HISTORY_URL"] = "https://another.example"
            os.environ["HISTORY_USER"] = ""
            os.environ["HISTORY_PASSWORD"] = ""
            config = Settings.from_env()
            self.assertEqual(config.url, "https://another.example")
            self.assertEqual((config.user, config.password), ("", ""))

    def test_migrates_old_inline_environment_without_stale_history_url(self):
        args = self.args("copilot")
        path = self.root / ".copilot/mcp-config.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "history-mcp": {
                            "command": "old",
                            "env": {
                                "HISTORY_URL": "https://old.example",
                                "HISTORY_PASSWORD": "old-secret",
                                "OTHER": "keep",
                            },
                        }
                    }
                }
            )
        )
        write_plan(build_plan(args))
        env = json.loads(path.read_text())["mcpServers"]["history-mcp"]["env"]
        self.assertNotIn("HISTORY_URL", env)
        self.assertNotIn("HISTORY_PASSWORD", env)
        self.assertEqual(env["OTHER"], "keep")
        self.assertIn("HISTORY_CONFIG", env)

    def test_retargeting_history_does_not_reuse_previous_servers_credentials(
        self,
    ):
        args = self.args("copilot")
        path = args.install_dir / "history.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "HISTORY_URL": "https://old.example",
                    "HISTORY_USER": "old-user",
                    "HISTORY_PASSWORD": "old-secret",
                    "HISTORY_SESSION": "old-session",
                }
            )
        )
        write_plan(build_plan(args))
        data = json.loads(path.read_text())
        self.assertEqual(data["HISTORY_URL"], args.history_url)
        self.assertNotIn("HISTORY_PASSWORD", data)
        self.assertNotIn("HISTORY_SESSION", data)

    def test_configure_entry_point_dry_run_writes_nothing(self):
        project = Path(__file__).resolve().parents[1]
        argv = [
            sys.executable,
            str(project / "configure.py"),
            "--client",
            "copilot",
            "--model-url",
            "https://m",
            "--history-url",
            "https://h",
            "--model",
            "qwen",
            "--config-root",
            str(self.root),
            "--dry-run",
        ]
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would configure:", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_model_discovery_requires_a_unique_model_and_does_not_infer(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"data":[{"id":"qwen"}]}'
        )
        with patch(
            "urllib.request.OpenerDirector.open", return_value=response
        ) as request:
            self.assertEqual(discover_model("https://m/v1", "KEY"), "qwen")
        self.assertEqual(
            request.call_args.args[0].full_url, "https://m/v1/models"
        )
        self.assertEqual(request.call_args.args[0].get_method(), "GET")
        response.__enter__.return_value.read.return_value = (
            b'{"data":[{"id":"a"},{"id":"b"}]}'
        )
        with (
            patch("urllib.request.OpenerDirector.open", return_value=response),
            self.assertRaisesRegex(ValueError, "exactly one"),
        ):
            discover_model("https://m/v1", "KEY")


if __name__ == "__main__":
    unittest.main()
