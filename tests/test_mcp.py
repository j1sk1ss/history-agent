import asyncio
import os
import socket
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

from mcp import Client, StdioServerParameters

from history_agent.history.client import HistoryClient, Settings
from history_agent.interfaces.mcp import create_server
from tests.fake_history import FakeHistory
from tests.test_client import SOURCE


class MCPTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)

    async def test_sdk_client_discovers_tools_resources_and_prompt(self):
        server = create_server(
            HistoryClient(Settings(self.fake.url, "tester", "test-secret"))
        )
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual(
                {tool.name for tool in tools},
                {
                    "history_discover",
                    "history_read",
                    "history_write",
                    "history_current",
                },
            )
            read = next(tool for tool in tools if tool.name == "history_read")
            scope = await client.call_tool("history_current", {})
            self.assertFalse(scope.is_error)
            self.assertIn("snapshot-first", scope.content[0].text)
            skill = await client.call_tool(
                "history_discover", {"action": "skills"}
            )
            self.assertIn("history-batch", skill.content[0].text)
            self.assertTrue(read.annotations.read_only_hint)
            resource = await client.read_resource("history://workflow")
            self.assertIn("agent_source", resource.contents[0].text)
            reference = await client.read_resource("history://api")
            self.assertIn("agent_checker_info", reference.contents[0].text)
            reference_tool = await client.call_tool(
                "history_discover", {"action": "api"}
            )
            self.assertFalse(reference_tool.is_error)
            self.assertIn(
                "agent_find_warnings", reference_tool.content[0].text
            )
            prompt = await client.get_prompt(
                "review_warning", {"project_id": "0", "warning_id": "warning"}
            )
            self.assertIn("warning", prompt.messages[0].content.text)
            error = await client.call_tool(
                "history_read", {"action": "shutdown", "arguments": {}}
            )
            self.assertTrue(error.is_error)
            self.assertIn("not allowed", error.content[0].text)

    async def test_real_stdio_process_to_http_history(self):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "history_agent.interfaces.mcp"],
            env={
                **os.environ,
                "HISTORY_URL": self.fake.url,
                "HISTORY_USER": "tester",
                "HISTORY_PASSWORD": "test-secret",
                "HISTORY_SESSION": "",
            },
        )
        async with Client(params) as client:
            catalog = await client.call_tool("history_discover", {})
            self.assertFalse(catalog.is_error)
            source = await client.call_tool(
                "history_read", {"action": "agent_source", "arguments": SOURCE}
            )
            self.assertFalse(source.is_error)
            self.assertIn("пример", source.content[0].text)
            self.assertEqual(self.fake.logins, 1)

    async def test_shared_http_endpoint_serves_two_mcp_clients(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        environment = {
            **os.environ,
            "HISTORY_URL": self.fake.url,
            "HISTORY_USER": "tester",
            "HISTORY_PASSWORD": "test-secret",
            "HISTORY_SESSION": "",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "history_agent.interfaces.mcp",
                "--transport",
                "streamable-http",
                "--port",
                str(port),
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            url = f"http://127.0.0.1:{port}/mcp"
            for _ in range(100):
                if process.poll() is not None:
                    self.fail(process.stderr.read().decode())
                try:
                    with urllib.request.urlopen(url, timeout=0.1):
                        pass
                    break
                except urllib.error.HTTPError:
                    break
                except urllib.error.URLError:
                    await asyncio.sleep(0.05)
            else:
                self.fail("Shared MCP endpoint did not start")
            async with Client(url) as first, Client(url) as second:
                self.assertEqual(len((await first.list_tools()).tools), 4)
                self.assertEqual(len((await second.list_tools()).tools), 4)
                result = await second.call_tool(
                    "history_read",
                    {"action": "agent_source", "arguments": SOURCE},
                )
                self.assertFalse(result.is_error)
                self.assertIn("пример", result.content[0].text)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
