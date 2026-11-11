"""Small bounded stdio JSON-RPC connection for one language-server query."""

import asyncio
import json
import os
import signal


class LanguageServer:
    def __init__(self, process):
        self.process = process
        self.sequence = 0

    @staticmethod
    async def start(command: list[str], root: str):
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=root,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=1048576,
        )
        return LanguageServer(process)

    async def send(self, message):
        data = json.dumps({"jsonrpc": "2.0", **message}).encode("utf-8")
        self.process.stdin.write(
            f"Content-Length: {len(data)}\r\n\r\n".encode() + data
        )
        await self.process.stdin.drain()

    async def notify(self, method, params):
        await self.send({"method": method, "params": params})

    async def read(self):
        header = await self.process.stdout.readuntil(b"\r\n\r\n")
        lengths = [
            line.split(b":", 1)[1].strip()
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        ]
        if len(lengths) != 1:
            raise ValueError("Language server sent invalid Content-Length")
        length = int(lengths[0])
        if not 0 < length <= 1048576:
            raise ValueError("Language server message exceeds 1 MiB")
        value = json.loads(await self.process.stdout.readexactly(length))
        if not isinstance(value, dict):
            raise ValueError("Language server message must be an object")
        return value

    async def request(self, method, params):
        self.sequence += 1
        identity = self.sequence
        await self.send({"id": identity, "method": method, "params": params})
        for _ in range(256):
            message = await self.read()
            if "method" in message:
                if "id" in message:
                    await self.respond(message)
                continue
            if message.get("id") == identity:
                if "error" in message:
                    raise ValueError(f"Language server: {message['error']}")
                return message.get("result")
        raise ValueError("Language server notification limit exceeded")

    async def respond(self, message):
        method = message["method"]
        if method == "workspace/configuration":
            items = message.get("params", {}).get("items", [])
            result = [{} for _ in items]
        elif method in (
            "client/registerCapability",
            "client/unregisterCapability",
            "window/workDoneProgress/create",
        ):
            result = None
        else:
            await self.send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": "Unsupported client method",
                    },
                }
            )
            return
        await self.send({"id": message["id"], "result": result})

    async def close(self):
        if self.process.returncode is None:
            try:
                await asyncio.wait_for(self.request("shutdown", None), 0.3)
                await self.notify("exit", None)
                await asyncio.wait_for(self.process.wait(), 0.3)
            except (
                TimeoutError,
                ValueError,
                OSError,
                asyncio.IncompleteReadError,
            ):
                pass
            finally:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
