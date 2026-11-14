import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.containers import VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Select,
    Static,
)

from history_agent.configuration.defaults import load_defaults
from history_agent.interfaces.cli import parse_args
from history_agent.review.jobs import job_lock
from history_agent.ui.app import HistoryApp
from history_agent.ui.config import ConfigurationApp, ConfigurationScreen
from history_agent.ui.config_fields import SettingsField
from history_agent.ui.formatting import pretty, tool_preview
from history_agent.ui.jobs import UnfinishedJobs
from history_agent.ui.metrics import SessionMetrics
from tests.test_jobs import checkpoint


class FormatterTest(unittest.TestCase):
    def test_json_is_indented(self):
        rendered = pretty('{"nested":{"items":[1,2,3]}}')
        self.assertEqual(
            json.loads(rendered), {"nested": {"items": [1, 2, 3]}}
        )
        self.assertIn('\n    "items": [', rendered)

    def test_tool_preview_limits_only_tool_content(self):
        value = json.dumps(
            {
                "lines": [
                    {"number": number, "text": f"line {number}"}
                    for number in range(100)
                ]
            }
        )
        self.assertLess(len(tool_preview(value)), 420)
        self.assertIn("line 1", tool_preview(value))

    def test_metrics_accounts_for_reported_tokens(self):
        metrics = SessionMetrics()
        metrics.event("request_start", context_bytes=100, step=1)
        metrics.event("content", text="response")
        metrics.event(
            "request_end", usage={"prompt_tokens": 12, "completion_tokens": 6}
        )
        self.assertEqual(metrics.counts()[:2], (12, 6))
        self.assertEqual((metrics.session_in, metrics.session_out), (12, 6))

    def test_metrics_show_context_speed_and_reported_usage(self):
        with patch(
            "history_agent.ui.metrics.time.monotonic", return_value=100
        ) as clock:
            metrics = SessionMetrics()
            metrics.event("request_start", context_bytes=4096, step=1)
            clock.return_value = 102
            metrics.event("content", text="response")
            clock.return_value = 104
            metrics.event(
                "request_end",
                usage={
                    "prompt_tokens": 500,
                    "completion_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 200},
                },
            )
            summary = metrics.summary("Ready", context_limit=65536)
        self.assertIn("CTX 4.0/64.0 KiB", summary)
        self.assertIn("6.0 tok/s", summary)
        self.assertIn("IN 500  OUT 12  CACHE 200", summary)
        self.assertNotIn("~", summary)

    def test_metrics_mark_estimates_until_usage_is_reported(self):
        metrics = SessionMetrics()
        metrics.event("request_start", context_bytes=4096, step=1)
        metrics.event("content", text="response")
        summary = metrics.summary("Working", context_limit=65536)
        self.assertIn("IN ~1024  OUT ~2  CACHE -", summary)
        self.assertIn("tok/s", summary)

    def test_metrics_show_cumulative_job_review_time(self):
        metrics = SessionMetrics()
        metrics.event("batch_started", job_id="job1")
        metrics.event(
            "batch_progress",
            job_id="job1",
            done=1,
            total=3,
            checker="A_CHECKER",
            warning_id="a1",
        )
        metrics.event(
            "review_timing",
            warning_id="a1",
            status="completed",
            route="agent",
            wall_seconds=12.5,
            waiting_seconds=5.0,
            agent_seconds=7.5,
        )
        summary = metrics.summary("Working", context_limit=65536)
        self.assertIn("1/3", summary)
        self.assertIn("12.5s review", summary)
        detailed = metrics.summary("Working", context_limit=65536,
                                   detailed=True)
        self.assertIn("agent 7.5s", detailed)
        self.assertIn("wait 5.0s", detailed)
        metrics.event(
            "review_timing",
            warning_id="a1",
            status="completed",
            route="system_one",
            wall_seconds=3.25,
            waiting_seconds=0.0,
            agent_seconds=3.25,
        )
        self.assertIn("15.8s review",
                      metrics.summary("Working", context_limit=65536))
        metrics.event("batch_finished", summary={})
        self.assertNotIn("s review",
                         metrics.summary("Ready", context_limit=65536))
        metrics.event("batch_started", job_id="job2")
        self.assertIn("0.0s review",
                      metrics.summary("Working", context_limit=65536))


class InterfaceTest(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        args = parse_args(
            [
                "--read-only",
                "--batch-state-dir",
                "/tmp/history-agent-test-jobs",
            ]
        )
        return HistoryApp(args)

    async def test_minimal_top_bar_and_disabled_palette(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)):
            self.assertFalse(app.ENABLE_COMMAND_PALETTE)
            self.assertEqual(
                str(app.query_one("#topbar").render()), "HISTORY  |  READ ONLY"
            )
            self.assertFalse(app.query("Header"))
            self.assertFalse(app.query("Footer"))

    async def test_home_becomes_chat_after_first_message(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            app.adapter.event("welcome", context=100)
            app.adapter.event("input_ready")
            await pilot.pause()
            self.assertTrue(app.query_one("#body").has_class("home"))
            self.assertFalse(app.cards)
            composer = app.query_one("#composer", Input)
            self.assertEqual(composer.value, "")
            self.assertIn("history link", composer.placeholder)
            self.assertEqual(
                composer.get_component_styles("input--placeholder").color.a,
                0.4,
            )
            self.assertEqual(app.focused, composer)
            self.assertTrue(
                await pilot.click("#review-project", offset=(2, 0))
            )
            await pilot.pause()
            self.assertEqual(app.inputs.get_nowait(), "/batch")
            app.adapter.event("begin", text="Review all warnings")
            await pilot.pause()
            self.assertFalse(app.query_one("#body").has_class("home"))

    async def test_settings_command_is_handled_locally(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            app.adapter.event("begin", text="Review this warning")
            app.adapter.event("content", text="Existing response")
            app.adapter.event("finish")
            app.adapter.event("input_ready")
            await pilot.pause()
            chat = app.screen
            cards = list(app.cards)
            composer = app.query_one("#composer", Input)
            composer.value = "Unsent draft"
            app.metrics.session_in = 123
            app.last_job_id = "a" * 32
            app.submit("/config")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfigurationScreen)
            app.screen.query_one("#model", Input).value = "Discard this"
            await pilot.click("#cancel")
            await pilot.pause()
            self.assertIs(app.screen, chat)
            self.assertIsNone(app.return_value)
            self.assertEqual(app.cards, cards)
            self.assertEqual(app.metrics.session_in, 123)
            self.assertEqual(app.last_job_id, "a" * 32)
            self.assertEqual(composer.value, "Unsent draft")
            self.assertIs(app.focused, composer)
            self.assertIsNone(app.args.model)
            self.assertTrue(app.inputs.empty())

    async def test_settings_keyboard_back_returns_to_home(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            app.adapter.event("input_ready")
            await pilot.pause()
            home = app.screen
            for key in ("escape", "ctrl+q", "ctrl+c"):
                await pilot.click("#settings")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfigurationScreen)
                await pilot.press(key)
                await pilot.pause()
                self.assertIs(app.screen, home)
                self.assertTrue(app.query_one("#body").has_class("home"))
                self.assertTrue(app.waiting)
            app.submit("Can still chat")
            self.assertEqual(app.inputs.get_nowait(), "Can still chat")

    async def test_fatal_error_allows_reload_and_error_is_shown_once(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            app.adapter.event("begin", text="Review all")
            app.adapter.event("batch_started", job_id="a" * 32)
            app.adapter.event("failure", message="Stopped batch")
            app.adapter.error_message("Stopped batch")
            app.adapter.event("backend_done")
            await pilot.pause()
            self.assertEqual(
                len([card for card in app.cards if card.kind == "error"]), 1
            )
            self.assertFalse(app.query_one("#composer").disabled)
            app.submit("/reload")
            await pilot.pause()
            self.assertEqual(app.return_value, {"reload_job": "a" * 32})

    async def test_metrics_are_visible_without_stats_flag(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            app.adapter.event("welcome", context=4096)
            await pilot.pause()
            app.refresh_display()
            widget = app.query_one("#metrics")
            self.assertTrue(widget.display)
            self.assertIn("CTX 4.0/64.0 KiB", str(widget.render()))
            self.assertIn("tok/s", str(widget.render()))
            self.assertIn("CACHE", str(widget.render()))
            self.assertGreaterEqual(widget.size.height, 2)

    async def test_stream_follows_layout_and_respects_manual_scrolling(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            app.adapter.event("begin", text="Review this warning")
            app.adapter.event("request_start", step=1, context_bytes=4096)
            viewport = app.query_one("#timeline", VerticalScroll)
            for kind in ("reasoning", "content", "content"):
                app.adapter.event(
                    kind, text="A model output paragraph.\n\n" * 30
                )
                await pilot.pause(0.25)
                self.assertGreater(viewport.max_scroll_y, 0)
                self.assertTrue(viewport.is_vertical_scroll_end)

            viewport.scroll_to(
                y=viewport.max_scroll_y - 10, animate=False, immediate=True
            )
            await pilot.pause()
            position = viewport.scroll_y
            app.adapter.event("content", text="More model output.\n\n" * 30)
            await pilot.pause(0.25)
            self.assertEqual(viewport.scroll_y, position)
            self.assertFalse(viewport.is_vertical_scroll_end)

            viewport.scroll_end(animate=False, immediate=True)
            app.adapter.event("content", text="Final paragraphs.\n\n" * 30)
            await pilot.pause(0.25)
            self.assertTrue(viewport.is_vertical_scroll_end)
            await pilot.resize_terminal(70, 20)
            await pilot.pause()
            self.assertTrue(viewport.is_vertical_scroll_end)

    async def test_answer_and_reasoning_unabridged_tool_result_collapsible(
        self,
    ):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            app.adapter.event("begin", text="review this warning")
            app.adapter.event("request_start", step=1, context_bytes=100)
            reasoning = "reasoning " * 250
            answer = "answer " * 250
            app.adapter.event("reasoning", text=reasoning)
            app.adapter.event("content", text=answer)
            app.adapter.event("request_end", usage={})
            app.adapter.event(
                "tool_result",
                action="current",
                event_id="id",
                arguments={"project": 1},
                response='{"result":{"value":7}}',
            )
            await pilot.pause()
            app.refresh_display()
            cards = {card.kind: card for card in app.cards}
            self.assertEqual(cards["reasoning"].text, reasoning)
            self.assertEqual(cards["answer"].text, answer)
            self.assertIn('\n    "result": {', cards["tool"].text)
            disclosure = cards["tool"].query_one(Collapsible)
            self.assertTrue(disclosure.collapsed)
            disclosure.collapsed = False
            await pilot.pause()
            self.assertFalse(disclosure.collapsed)


class JobInterfaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.args = parse_args(
            [
                "--provider",
                "llama",
                "--model",
                "qwen",
                "--batch-state-dir",
                str(self.root),
            ]
        )

    async def test_home_lists_only_unfinished_jobs_and_resumes_selected_id(
        self,
    ):
        path = checkpoint(self.root, ["reviewed", "pending"])
        finished = checkpoint(self.root, ["reviewed"])
        app = HistoryApp(self.args)
        async with app.run_test(size=(80, 24)) as pilot:
            self.assertTrue(app.query_one("#body").has_class("has-jobs"))
            self.assertTrue(app.query_one(UnfinishedJobs).disabled)
            self.assertFalse(app.query("#resume-" + finished.stem))
            label = str(app.query_one(".job-description").render())
            self.assertIn("1/2", label)
            self.assertIn("master", label)
            self.assertIn("qwen", label)
            app.adapter.event("input_ready")
            await pilot.pause()
            await pilot.click("#resume-" + path.stem)
            self.assertEqual(app.inputs.get_nowait(), "/resume " + path.stem)
            self.assertTrue(app.query_one(UnfinishedJobs).disabled)

    async def test_delete_refreshes_list_and_restores_empty_home(self):
        path = checkpoint(self.root, ["pending"])
        app = HistoryApp(self.args)
        async with app.run_test(size=(80, 24)) as pilot:
            app.adapter.event("input_ready")
            await pilot.pause()
            await pilot.click("#delete-" + path.stem)
            await pilot.pause()
            self.assertFalse(path.exists())
            self.assertFalse(app.query_one("#body").has_class("has-jobs"))
            self.assertFalse(app.query(".job-row"))
            self.assertTrue(app.inputs.empty())
            self.assertTrue(app.query_one("#body").has_class("home"))

    async def test_running_job_delete_error_keeps_home_and_checkpoint(self):
        path = checkpoint(self.root, ["analyzing"])
        app = HistoryApp(self.args)
        async with app.run_test(size=(80, 24)) as pilot:
            app.adapter.event("input_ready")
            await pilot.pause()
            with job_lock(path):
                await pilot.click("#delete-" + path.stem)
                await pilot.pause()
            self.assertTrue(path.exists())
            self.assertIn(
                "already running", str(app.query_one("#jobs-error").render())
            )
            self.assertFalse(app.query_one(UnfinishedJobs).disabled)
            self.assertTrue(app.query_one("#body").has_class("home"))

    async def test_many_jobs_scroll_without_covering_chat_or_metrics(self):
        paths = [checkpoint(self.root, ["pending"]) for _ in range(8)]
        app = HistoryApp(self.args)
        async with app.run_test(size=(80, 35)) as pilot:
            app.adapter.event("welcome", context=4096)
            app.adapter.event("input_ready")
            await pilot.pause()
            for size in ((80, 24), (60, 20)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                composer = app.query_one("#composer")
                metrics = app.query_one("#metrics")
                jobs = app.query_one("#jobs-list", VerticalScroll)
                self.assertGreaterEqual(composer.region.y, 0)
                self.assertLessEqual(composer.region.bottom, metrics.region.y)
                self.assertGreater(jobs.max_scroll_y, 0)
                self.assertGreaterEqual(
                    app.query_one("#home-title").region.y, 1
                )
            button = app.query_one("#resume-" + paths[0].stem, Button)
            button.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            await pilot.click("#" + button.id)
            self.assertEqual(
                app.inputs.get_nowait(), "/resume " + paths[0].stem
            )


class ConfigurationInterfaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("probe_model_connection", "probe_system_one_connection"):
            probe = patch(
                "history_agent.ui.config." + name,
                return_value=(False, "Test endpoint unavailable"),
            )
            probe.start()
            self.addCleanup(probe.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "chat.json"
        environment = patch.dict(
            os.environ,
            {"HISTORY_AGENT_CONFIG": str(self.path), "HISTORY_CONFIG": ""},
        )
        environment.start()
        self.addCleanup(environment.stop)

    async def test_connection_squares_refresh_after_settings_change(self):
        app = ConfigurationApp(parse_args(["--system-one-model", "fast"]))
        with patch(
            "history_agent.ui.config.probe_model_connection",
            return_value=(True, "Main model answered"),
        ), patch(
            "history_agent.ui.config.probe_system_one_connection",
            side_effect=lambda args: (
                args.system_one_model == "fast", "System 1 test response"
            ),
        ):
            async with app.run_test(size=(100, 60)) as pilot:
                screen = app.screen
                if screen.connection_timer:
                    screen.connection_timer.stop()
                await pilot.click("#check-connections")
                await app.workers.wait_for_complete()
                main = screen.query_one("#model-status", Static)
                fast = screen.query_one("#system-one-status", Static)
                self.assertIn("[green]■", str(main.content))
                self.assertIn("[green]■", str(fast.content))
                self.assertEqual(main.tooltip, "Main model answered")
                screen.query_one("#system_one_model", Input).value = "offline"
                await pilot.pause(0.5)
                await app.workers.wait_for_complete()
                self.assertIn("[red]■", str(fast.content))
                self.assertIn("[green]■", str(main.content))

    async def test_save_from_chat_returns_new_settings_for_reconnection(self):
        app = HistoryApp(parse_args([]))
        async with app.run_test(size=(100, 45)) as pilot:
            app.adapter.event("input_ready")
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfigurationScreen)
            app.screen.query_one("#model", Input).value = "new-model"
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual(app.return_value["settings"].model, "new-model")
            self.assertEqual(load_defaults()["model"], "new-model")

    async def test_visible_connection_error_and_timeout_change_rechecks(self):
        app = ConfigurationApp(parse_args(["--timeout", "45"]))
        detail = "HTTP 401: check API key [proxy-token]"
        with patch(
            "history_agent.ui.config.probe_model_connection",
            return_value=(False, detail),
        ) as probe:
            async with app.run_test(size=(100, 60)) as pilot:
                await pilot.click("#check-connections")
                await app.workers.wait_for_complete()
                await pilot.pause()
                error = app.screen.query_one("#model-connection-error", Static)
                self.assertEqual(str(error.render()), detail)
                self.assertTrue(error.display)
                self.assertGreater(error.region.height, 0)
                self.assertEqual(probe.call_args.args[0].timeout, 45)
                probe.reset_mock()
                app.screen.query_one("#timeout", Input).value = "17.5"
                await pilot.pause(0.5)
                await app.workers.wait_for_complete()
                self.assertTrue(probe.called)
                self.assertEqual(probe.call_args.args[0].timeout, 17.5)
                probe.return_value = True, "Main model answered"
                await pilot.click("#check-connections")
                await app.workers.wait_for_complete()
                self.assertEqual(str(error.content), "")
                probe.reset_mock()
                for value in ("0", "nan", "invalid"):
                    with self.subTest(timeout=value):
                        app.screen.query_one("#timeout", Input).value = value
                        await pilot.pause(0.5)
                        await app.workers.wait_for_complete()
                        self.assertIn(
                            "must be positive numbers",
                            str(error.content),
                        )
                        probe.assert_not_called()

    async def test_credentials_and_hover_descriptions(self):
        app = ConfigurationApp(parse_args([]))
        async with app.run_test(size=(100, 60)) as pilot:
            self.assertFalse(
                any(
                    "Save these defaults for future launches"
                    in str(widget.render())
                    for widget in app.screen.query("Static")
                )
            )
            password = app.screen.query_one("#history_password", Input)
            self.assertTrue(password.password)
            for name in ("model-settings", "history-settings"):
                fields = list(app.screen.query_one("#" + name).query(
                    SettingsField
                ))
                for previous, current in zip(fields, fields[1:]):
                    self.assertGreater(
                        current.region.y, previous.region.bottom
                    )
            for widget in app.screen.query("Input, Select, Checkbox"):
                self.assertTrue(widget.tooltip, widget.id)
            checkbox = app.screen.query_one("#no_patterns", Checkbox)
            self.assertIn("hypotheses", checkbox.tooltip)
            app.screen.query_one("#model", Input).value = "qwen"
            app.screen.query_one("#history_user", Input).value = "reviewer"
            password.value = " secret "
            await pilot.click("#save")
            await pilot.pause()
            saved = load_defaults()
            self.assertEqual(saved["history_user"], "reviewer")
            self.assertEqual(saved["history_password"], " secret ")
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_settings_actions_stay_visible_in_small_terminal(self):
        for size in ((40, 20), (80, 24)):
            with self.subTest(size=size):
                app = ConfigurationApp(parse_args([]))
                async with app.run_test(size=size) as pilot:
                    viewport = app.screen.query_one(
                        "#settings", VerticalScroll
                    )
                    self.assertGreater(viewport.max_scroll_y, 0)
                    self.assertEqual(viewport.max_scroll_x, 0)
                    save = app.screen.query_one("#save", Button)
                    self.assertGreaterEqual(save.region.y, 0)
                    self.assertLessEqual(save.region.bottom, size[1])
                    viewport.scroll_end(animate=False, immediate=True)
                    await pilot.pause()
                    self.assertLessEqual(save.region.bottom, size[1])

    async def test_save_preserves_transient_cli_request(
        self,
    ):
        args = parse_args(["--prompt", "Review this warning", "--plain"])
        app = ConfigurationApp(args)
        async with app.run_test(size=(100, 45)) as pilot:
            app.screen.query_one("#model", Input).value = "qwen-local"
            app.screen.query_one(
                "#model_url", Input
            ).value = "http://localhost:8000/v1"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsNotNone(app.return_value)
            self.assertEqual(app.return_value.prompt, "Review this warning")
            self.assertEqual(load_defaults()["model"], "qwen-local")
            self.assertFalse(args.model)

    async def test_invalid_form_stays_open_and_cancel_does_not_write(self):
        app = ConfigurationApp(parse_args([]))
        async with app.run_test(size=(100, 45)) as pilot:
            app.screen.query_one("#model_url", Input).value = "invalid-url"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIn(
                "HTTP(S)",
                str(app.screen.query_one("#settings-error").render()),
            )
            self.assertFalse(self.path.exists())
            await pilot.press("escape")
            self.assertIsNone(app.return_value)
        self.assertFalse(self.path.exists())

    async def test_provider_placeholders_and_required_remote_model(self):
        app = ConfigurationApp(parse_args([]))
        async with app.run_test(size=(100, 45)) as pilot:
            app.screen.query_one("#provider", Select).value = "deepseek"
            await pilot.pause()
            self.assertEqual(
                app.screen.query_one("#model_url", Input).placeholder,
                "https://api.deepseek.com/v1",
            )
            self.assertTrue(
                app.screen.query_one("#model_api_key", Input).password
            )
            self.assertEqual(
                app.screen.query_one("#model_url", Input).value, ""
            )
            await pilot.click("#save")
            await pilot.pause()
            self.assertIn(
                "model ID",
                str(app.screen.query_one("#settings-error").render()),
            )
            self.assertFalse(self.path.exists())
