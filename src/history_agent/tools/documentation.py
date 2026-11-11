"""Search only named third-party APIs on host-approved documentation sites."""

import os
from typing import Literal

from pydantic import Field

from history_agent.configuration.capabilities import DocumentationSettings
from history_agent.contracts import StrictModel
from history_agent.prompts import Prompts
from history_agent.tools.documentation_http import DocumentationHTTP
from history_agent.tools.registry import ToolRegistry


class DocumentationRequest(StrictModel):
    action: Literal["search", "read"] = "search"
    library: str = Field(pattern=r"^[a-z0-9_.+-]{1,80}$")
    symbol: str = Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$.:<>~-]{0,159}$")
    version: str = Field(default="", pattern=r"^[A-Za-z0-9_.+-]{0,40}$")
    url: str | None = Field(default=None, max_length=2000)
    offset: int = Field(default=0, ge=0, le=500000)


class DocumentationTools:
    @staticmethod
    def definition():
        return ToolRegistry.create(
            "library_docs",
            Prompts.get("tools.library_docs"),
            DocumentationRequest,
        )

    @staticmethod
    def execute(
        request: DocumentationRequest, settings: DocumentationSettings
    ):
        hosts = settings.libraries.get(request.library)
        if not settings.enabled or not hosts:
            raise ValueError(
                "Library is not in the configured documentation catalog"
            )
        if request.action == "read":
            if not request.url:
                raise ValueError("Provide a documentation URL")
            url = DocumentationHTTP.validate(request.url, hosts)
            text = DocumentationHTTP.text(DocumentationHTTP.get(url))
            excerpt = text[request.offset : request.offset + 6000]
            return {
                "library": request.library,
                "symbol": request.symbol,
                "url": url,
                "text": excerpt,
                "offset": request.offset,
                "next_offset": request.offset + len(excerpt)
                if request.offset + len(excerpt) < len(text)
                else None,
                "untrusted_documentation": True,
            }
        if request.url or request.offset:
            raise ValueError("Search accepts only library, symbol and version")
        key = os.environ.get(settings.api_key_env)
        if not key:
            return {
                "status": "unavailable",
                "documentation_hosts": hosts,
                "error": f"Set {settings.api_key_env} for search; "
                "known documentation URLs can still be read.",
            }
        sites = " OR ".join("site:" + host for host in hosts)
        query = (
            f"{request.library} {request.symbol} {request.version} ({sites})"
        )
        results = []
        for result in DocumentationHTTP.search(query, key):
            try:
                url = DocumentationHTTP.validate(result.get("url", ""), hosts)
            except (ValueError, OSError):
                continue
            results.append(
                {
                    "url": url,
                    "title": str(result.get("title", ""))[:300],
                    "description": str(result.get("description", ""))[:1500],
                }
            )
        return {"results": results[:5], "untrusted_documentation": True}
