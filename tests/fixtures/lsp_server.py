"""Deterministic stdio peer for actual subprocess protocol tests."""

import json
import sys


def read():
    size = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        if line == b"\r\n":
            break
        if line.lower().startswith(b"content-length:"):
            size = int(line.split(b":", 1)[1])
    return json.loads(sys.stdin.buffer.read(size))


def write(value):
    data = json.dumps({"jsonrpc": "2.0", **value}).encode()
    sys.stdout.buffer.write(
        f"Content-Length: {len(data)}\r\n\r\n".encode() + data
    )
    sys.stdout.buffer.flush()


document = None
while True:
    try:
        message = read()
    except EOFError:
        break
    method = message.get("method")
    if method == "initialize":
        write({"id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        document = message["params"]["textDocument"]
    elif method == "textDocument/documentSymbol":
        write(
            {
                "id": "server-config",
                "method": "workspace/configuration",
                "params": {"items": [{"section": "test"}]},
            }
        )
        config = read()
        assert config["result"] == [{}]
        lines = document["text"].splitlines()
        index = next(i for i, text in enumerate(lines) if text)
        write(
            {
                "id": message["id"],
                "result": [
                    {
                        "name": "sample",
                        "kind": 12,
                        "location": {
                            "uri": document["uri"],
                            "range": {
                                "start": {"line": index, "character": 0},
                                "end": {
                                    "line": index,
                                    "character": len(lines[index]),
                                },
                            },
                        },
                    }
                ],
            }
        )
    elif method == "textDocument/hover":
        write(
            {
                "id": message["id"],
                "result": {
                    "contents": "int sample()",
                    "position": message["params"]["position"],
                },
            }
        )
    elif method == "shutdown":
        write({"id": message["id"], "result": None})
    elif method == "exit":
        break
