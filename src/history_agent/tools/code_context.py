"""Language-server navigation over source obtained from the history API."""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from history_agent.configuration.capabilities import LanguageServerSettings
from history_agent.contracts import StrictModel
from history_agent.prompts import Prompts
from history_agent.tools.lsp_transport import LanguageServer
from history_agent.tools.registry import ToolRegistry

LANGUAGES = {
    ".lua": "lua",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
}
METHODS = {
    "symbols": "textDocument/documentSymbol",
    "hover": "textDocument/hover",
    "definition": "textDocument/definition",
    "references": "textDocument/references",
}


class CodeContextRequest(StrictModel):
    action: Literal["symbols", "hover", "definition", "references"] = "symbols"
    project_id: int = Field(ge=0)
    snapshot_id: str = Field(min_length=1, max_length=200)
    file: str = Field(min_length=1, max_length=2000)
    line: int = Field(ge=1, le=200000)
    column: int = Field(default=1, ge=1, le=10000)
    before: int = Field(default=40, ge=0, le=200)
    after: int = Field(default=40, ge=0, le=200)
    language: (
        Literal["lua", "c", "cpp", "python", "java", "javascript"] | None
    ) = None


class CodeContextTools:
    @staticmethod
    def definition():
        return ToolRegistry.create(
            "code_context",
            Prompts.get("tools.code_context"),
            CodeContextRequest,
        )

    @staticmethod
    async def execute(request, client, settings: LanguageServerSettings):
        language = request.language or LANGUAGES.get(
            Path(request.file).suffix.lower()
        )
        command = list(settings.commands.get(language, []))
        if not settings.enabled or not command or not shutil.which(command[0]):
            return {
                "status": "unavailable",
                "language": language,
                "guidance": "Install/configure this language server or use "
                "agent_source and agent_resolve_symbol through history_read.",
            }
        args = request.model_dump(
            include={
                "project_id",
                "snapshot_id",
                "file",
                "line",
                "before",
                "after",
            }
        )
        source = await client.call_tool(
            "history_read",
            {
                "action": "agent_source",
                "arguments": args,
            },
        )
        if source.is_error:
            return {"error": "History source request failed"}
        data = json.loads(
            "".join(p.text for p in source.content if p.type == "text")
        )
        text, lines = CodeContextTools.document(data, request)
        with tempfile.TemporaryDirectory(prefix="history-lsp-") as root:
            path = Path(root) / ("document" + Path(request.file).suffix)
            if language == "java":
                path = Path(root) / Path(request.file).name
                command += ["-data", str(Path(root) / ".jdtls")]
            path.write_text(text, encoding="utf-8")
            result = await asyncio.wait_for(
                CodeContextTools.query(
                    command, root, path, language, text, request
                ),
                timeout=settings.timeout,
            )
            result = CodeContextTools.clean(
                result, root, path.as_uri(), request
            )
        return {
            "language": language,
            "action": request.action,
            "result": result,
            "source": {**args, "lines": lines},
            "partial_document": True,
            "limitations": "Only the fetched source window is available. "
            "Missing imports, build flags and files limit semantic accuracy. "
            "An empty LSP result does not prove absence of a defect.",
        }

    @staticmethod
    def document(data, request):
        lines = [
            entry
            for entry in data.get("lines", [])
            if type(entry.get("number")) is int
            and max(1, request.line - request.before)
            <= entry["number"]
            <= min(200000, request.line + request.after)
            and isinstance(entry.get("text"), str)
        ]
        if not any(entry["number"] == request.line for entry in lines):
            raise ValueError(
                "History did not return the requested source line"
            )
        # Blank padding preserves original, one-based source addresses.
        parts = [""] * max(entry["number"] for entry in lines)
        for entry in lines:
            parts[entry["number"] - 1] = entry["text"]
        text = "\n".join(parts) + "\n"
        if len(text.encode("utf-8")) > 1048576:
            raise ValueError("LSP source document exceeds 1 MiB")
        return text, lines

    @staticmethod
    async def query(command, root, path, language, text, request):
        server = await LanguageServer.start(command, root)
        try:
            await server.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": Path(root).as_uri(),
                    "capabilities": {
                        "general": {"positionEncodings": ["utf-16"]},
                        "textDocument": {
                            "documentSymbol": {
                                "hierarchicalDocumentSymbolSupport": True,
                            }
                        },
                    },
                    "workspaceFolders": [
                        {"uri": Path(root).as_uri(), "name": "snippet"}
                    ],
                },
            )
            await server.notify("initialized", {})
            await server.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": path.as_uri(),
                        "languageId": language,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            params = {"textDocument": {"uri": path.as_uri()}}
            if request.action != "symbols":
                params["position"] = {
                    "line": request.line - 1,
                    "character": request.column - 1,
                }
            if request.action == "references":
                params["context"] = {"includeDeclaration": True}
            return await server.request(METHODS[request.action], params)
        finally:
            await server.close()

    @staticmethod
    def clean(value, root, uri, request):
        if isinstance(value, list):
            return [
                CodeContextTools.clean(v, root, uri, request)
                for v in value[:100]
            ]
        if isinstance(value, dict):
            return {
                k: CodeContextTools.clean(v, root, uri, request)
                for k, v in value.items()
            }
        if isinstance(value, str):
            return value.replace(uri, request.file).replace(root, "<snippet>")
        return value
