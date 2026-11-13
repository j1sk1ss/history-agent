import asyncio
import io
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp import Client
from rich.console import Console

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.chat import ChatError, Harness, Model
from history_agent.interfaces.mcp import create_server
from history_agent.ui.terminal import TerminalUI
from history_agent.ui.text import ThinkingText
from tests.fake_history import FakeHistory
from tests.test_client import SOURCE


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def finish(reason="stop"):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


USAGE = {
    "choices": [],
    "usage": {
        "prompt_tokens": 200,
        "completion_tokens": 24,
        "prompt_tokens_details": {"cached_tokens": 128},
    },
}


def tool_chunks(name, arguments, id="call-1"):
    args = json.dumps(arguments, ensure_ascii=False)
    middle = len(args) // 2
    return [
        delta(
            tool_calls=[
                {
                    "index": 0,
                    "id": id,
                    "type": "function",
                    "function": {"name": name, "arguments": args[:middle]},
                }
            ]
        ),
        delta(
            tool_calls=[{"index": 0, "function": {"arguments": args[middle:]}}]
        ),
        finish("tool_calls"),
        USAGE,
        "[DONE]",
    ]


class StreamingServer:
    def __init__(self, responses, gate=None):
        self.responses, self.requests = responses, []
        self.gate = gate
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                owner.requests.append(payload)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = owner.responses[len(owner.requests) - 1]
                try:
                    self.wfile.write(b": keepalive\r\n\r\n")
                    for index, chunk in enumerate(chunks):
                        if index == 1 and owner.gate:
                            owner.gate.wait(timeout=10)
                        data = (
                            chunk
                            if isinstance(chunk, str)
                            else json.dumps(chunk, ensure_ascii=False)
                        )
                        self.wfile.write(
                            ("data: " + data + "\r\n\r\n").encode("utf-8")
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        if self.gate:
            self.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class StreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # The synchronous fixture shutdown may take half a second; it is
        # unrelated to stream latency.
        asyncio.get_running_loop().slow_callback_duration = 5

    async def test_reasoning_streams_early_with_exact_usage(
        self,
    ):
        gate, observed = threading.Event(), threading.Event()
        events = []

        def emit(kind, **data):
            events.append((kind, data))
            if kind == "reasoning":
                observed.set()

        with StreamingServer(
            [
                [
                    delta(reasoning_content="Проверяю указатель"),
                    delta(content="Answer"),
                    finish(),
                    USAGE,
                    "[DONE]",
                ]
            ],
            gate,
        ) as server:
            model = Model(server.url + "/v1", "synthetic")
            task = asyncio.create_task(
                asyncio.to_thread(model.complete, [], [], emit)
            )
            try:
                self.assertTrue(await asyncio.to_thread(observed.wait, 3))
                self.assertFalse(task.done())
            finally:
                gate.set()
            response = await task
            message = response["choices"][0]["message"]
            self.assertEqual(
                message["reasoning_content"], "Проверяю указатель"
            )
            self.assertEqual(message["content"], "Answer")
            self.assertEqual(response["usage"]["completion_tokens"], 24)
            self.assertTrue(server.requests[0]["stream"])
            self.assertTrue(
                server.requests[0]["stream_options"]["include_usage"]
            )
            self.assertEqual(events[0][0], "reasoning")

    async def test_tool_calls_wait_for_assembly_and_keep_reasoning(
        self,
    ):
        responses = [
            tool_chunks("history_discover", {"action": "agent_source"}),
            [
                delta(reasoning_content="Need the source"),
                *tool_chunks(
                    "history_read",
                    {"action": "agent_source", "arguments": SOURCE},
                ),
            ],
            [
                delta(content="Verified value"),
                delta(content=" = 42"),
                finish(),
                USAGE,
                "[DONE]",
            ],
        ]
        with FakeHistory() as history, StreamingServer(responses) as server:
            async with Client(
                create_server(
                    HistoryClient(
                        Settings(history.url, "tester", "test-secret")
                    )
                )
            ) as client:
                events = []
                harness = Harness(
                    client,
                    Model(server.url + "/v1", "synthetic"),
                    (await client.list_tools()).tools,
                    on_event=lambda kind, **data: events.append((kind, data)),
                )
                self.assertEqual(
                    await harness.ask("Inspect source"), "Verified value = 42"
                )
                self.assertEqual(
                    history.requests[-1]["action"], "agent_source"
                )
                self.assertFalse(history.writes)
                self.assertIn(
                    "пример", server.requests[-1]["messages"][-1]["content"]
                )
                reasoning = [
                    message["reasoning_content"]
                    for message in server.requests[-1]["messages"]
                    if "reasoning_content" in message
                ]
                self.assertEqual(reasoning, ["Need the source"])
                self.assertEqual(
                    [event[0] for event in events].count("tool_start"), 2
                )
                self.assertEqual(
                    [event[0] for event in events].count("tool_end"), 2
                )

    async def test_incomplete_or_malformed_stream_never_executes_tools(self):
        valid = tool_chunks(
            "history_read", {"action": "agent_source", "arguments": SOURCE}
        )
        for chunks in (
            valid[:2],
            [valid[0], "[DONE]"],
            [valid[0], "{broken"],
            valid[:2] + [finish("length"), "[DONE]"],
        ):
            with (
                self.subTest(chunks=chunks),
                FakeHistory() as history,
                StreamingServer([chunks]) as server,
            ):
                async with Client(
                    create_server(
                        HistoryClient(
                            Settings(history.url, "tester", "test-secret")
                        )
                    )
                ) as client:
                    harness = Harness(
                        client,
                        Model(server.url + "/v1", "synthetic"),
                        (await client.list_tools()).tools,
                    )
                    with self.assertRaises(ChatError):
                        await harness.ask("Inspect source")
                    self.assertTrue(harness.blocked)
                    self.assertFalse(history.requests)

    async def test_tool_not_executed_while_arguments_are_still_arriving(self):
        gate, observed = threading.Event(), threading.Event()
        chunks = tool_chunks(
            "history_read", {"action": "agent_source", "arguments": SOURCE}
        )
        responses = [chunks, [delta(content="Read"), finish(), "[DONE]"]]
        with (
            FakeHistory() as history,
            StreamingServer(responses, gate) as server,
        ):
            async with Client(
                create_server(
                    HistoryClient(
                        Settings(history.url, "tester", "test-secret")
                    )
                )
            ) as client:

                def emit(kind, **data):
                    if kind == "tool_delta":
                        observed.set()

                harness = Harness(
                    client,
                    Model(server.url + "/v1", "synthetic"),
                    (await client.list_tools()).tools,
                    on_event=emit,
                )
                task = asyncio.create_task(harness.ask("Inspect"))
                try:
                    self.assertTrue(await asyncio.to_thread(observed.wait, 3))
                    self.assertFalse(history.requests)
                    self.assertFalse(task.done())
                finally:
                    gate.set()
                self.assertEqual(await task, "Read")

    async def test_plain_cli_answer_omits_ansi_and_reasoning(
        self,
    ):
        responses = [
            [
                delta(reasoning_content="Synthetic thought"),
                delta(content="Plain answer"),
                finish(),
                USAGE,
                "[DONE]",
            ]
        ]
        with StreamingServer(responses) as server:
            process = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "history_agent.interfaces.chat",
                    "--model-url",
                    server.url,
                    "--model",
                    "synthetic",
                    "--prompt",
                    "Hello",
                    "--plain",
                    "--stats",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "HISTORY_CONFIG": ""},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout.strip(), "Plain answer")
            self.assertNotIn("\x1b", process.stdout)
            self.assertIn("cached_tokens=128", process.stderr)

    @unittest.skipUnless(
        os.name == "posix", "PTY test requires a POSIX terminal"
    )
    async def test_terminal_updates_before_tokens_and_streams_thoughts(
        self,
    ):
        await asyncio.to_thread(self.terminal_process)

    @unittest.skipUnless(
        os.name == "posix", "PTY test requires a POSIX terminal"
    )
    async def test_ctrl_c_restores_terminal_without_waiting_for_generation(
        self,
    ):
        await asyncio.to_thread(self.terminal_process, True)

    def terminal_process(self, interrupt=False):
        import pty

        gate = threading.Event()
        responses = [
            [
                delta(role="assistant"),
                delta(reasoning_content="Inspecting pointer"),
                delta(content="Terminal answer"),
                finish(),
                USAGE,
                "[DONE]",
            ]
        ]
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        with StreamingServer(responses, gate) as server:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key != "NO_COLOR"
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "history_agent.interfaces.chat",
                    "--model-url",
                    server.url,
                    "--model",
                    "synthetic",
                    "--prompt",
                    "Hello",
                    "--legacy-ui",
                ],
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=slave,
                env={
                    **environment,
                    "HISTORY_CONFIG": "",
                    "TERM": "xterm-256color",
                    "FORCE_COLOR": "1",
                },
            )
            os.close(slave)
            data = bytearray()
            try:
                deadline = time.monotonic() + 6
                while (
                    b"waiting for first token" not in data
                    and time.monotonic() < deadline
                ):
                    if select.select([master], [], [], 0.1)[0]:
                        data.extend(os.read(master, 65536))
                self.assertIn(b"waiting for first token", data)
                self.assertIsNone(process.poll())
                if interrupt:
                    process.send_signal(signal.SIGINT)
                    self.assertEqual(process.wait(timeout=3), 130)
                    while select.select([master], [], [], 0)[0]:
                        try:
                            data.extend(os.read(master, 65536))
                        except OSError:
                            break
                    self.assertIn(b"\x1b[?25h", data)
                    self.assertNotIn(b"Traceback", data)
                    return
                # Multiple redraws before any generated text prove the waiting
                # indicator is live.
                for _ in range(3):
                    if select.select([master], [], [], 0.3)[0]:
                        data.extend(os.read(master, 65536))
                self.assertGreater(data.count(b"waiting for first token"), 1)
                gate.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        data.extend(chunk)
                    if process.poll() is not None:
                        break
                self.assertEqual(
                    process.wait(timeout=3), 0, data.decode(errors="replace")
                )
                text = data.decode(errors="replace")
                self.assertIn("Inspecting pointer", text)
                self.assertIn("Terminal answer", text)
                self.assertIn("cache 128", text)
                self.assertIn("\x1b[", text)
                self.assertNotIn("Traceback", text)
            finally:
                gate.set()
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)


class TerminalTest(unittest.TestCase):
    def ui(self, **kwargs):
        output = io.StringIO()
        console = Console(
            file=output,
            force_terminal=True,
            no_color=False,
            width=100,
            height=30,
            color_system="256",
        )
        return TerminalUI("qwen-test", console=console, **kwargs), output

    def test_reported_usage_replaces_estimates_once(
        self,
    ):
        ui, output = self.ui()
        ui.event("request_start", step=1, context_bytes=1600)
        ui.event("reasoning", text="Check the pointer")
        ui.console.print(ui.render())
        self.assertIn("≈400", output.getvalue())
        self.assertIn("Thinking", output.getvalue())
        ui.event("usage", usage=USAGE["usage"])
        ui.event("usage", usage=USAGE["usage"])
        ui.event("request_end", usage=USAGE["usage"])
        self.assertEqual(ui.session_in, 200)
        self.assertEqual(ui.session_out, 24)
        self.assertEqual(ui.session_cached, 128)
        self.assertFalse(ui.session_estimated_out)
        ui.console.print(ui.footer())
        self.assertIn("cache 128", output.getvalue())

    def test_footer_shows_cumulative_review_time_during_batch(self):
        ui, output = self.ui()
        ui.event("batch_started", job_id="job1")
        ui.event(
            "batch_progress",
            job_id="job1",
            done=1,
            total=2,
            checker="A_CHECKER",
            warning_id="a1",
        )
        ui.event(
            "review_timing",
            warning_id="a1",
            status="completed",
            route="agent",
            wall_seconds=12.5,
            waiting_seconds=5.0,
            agent_seconds=7.5,
        )
        self.assertAlmostEqual(ui.review_wall, 12.5)
        ui.console.print(ui.footer())
        self.assertIn("1/2", output.getvalue())
        self.assertIn("12.5s review", output.getvalue())
        ui.event(
            "review_timing",
            warning_id="a2",
            status="completed",
            route="agent",
            wall_seconds=3.25,
            waiting_seconds=0.0,
            agent_seconds=3.25,
        )
        output.truncate(0)
        output.seek(0)
        ui.console.print(ui.footer())
        self.assertIn("15.8s review", output.getvalue())

    def test_split_think_tags_and_gray_reasoning_are_separate_from_answer(
        self,
    ):
        blocks = []
        parser = ThinkingText(
            lambda kind, text: blocks.append((kind, text)) if text else None
        )
        for chunk in ("<thi", "nk>Pointer ", "check</th", "ink>Answer"):
            parser.feed(chunk)
        parser.flush()
        self.assertEqual(
            "".join(text for kind, text in blocks if kind == "reasoning"),
            "Pointer check",
        )
        self.assertEqual(
            "".join(text for kind, text in blocks if kind == "content"),
            "Answer",
        )
        ui, output = self.ui()
        ui.event("request_start", step=1, context_bytes=1500)
        ui.event("reasoning", text="Pointer check")
        ui.event("content", text="Final answer")
        ui.console.print(ui.body())
        self.assertIn("Pointer check", output.getvalue())
        self.assertIn("Final answer", output.getvalue())
        self.assertIn("\x1b[38;5;", output.getvalue())

    def test_thought_visibility_and_terminal_escape_filter(self):
        ui, output = self.ui(show_thoughts=False)
        ui.event("reasoning", text="hidden thought")
        ui.event("content", text="[bold]literal\x1b]52;c;bad\x07 answer")
        ui.console.print(ui.body())
        self.assertNotIn("hidden thought", output.getvalue())
        self.assertIn("[bold]literal", output.getvalue())
        self.assertNotIn("\x1b]52", output.getvalue())

    def test_error_keeps_partial_output_visible_and_marks_it_stopped(self):
        ui, output = self.ui()
        ui.event("request_start", step=1, context_bytes=1600)
        ui.event("content", text="Partial answer")
        ui.event("failure", message="Stream disconnected")
        self.assertTrue(ui.session_estimated_out)
        self.assertGreater(ui.session_out, 0)
        ui.finish()
        self.assertIn("Partial answer", output.getvalue())
        self.assertIn("Stopped", output.getvalue())
        self.assertIn("Stream disconnected", output.getvalue())


if __name__ == "__main__":
    unittest.main()
