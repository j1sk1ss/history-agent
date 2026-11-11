"""Bounded HTTPS access to public documentation without redirects."""

import ipaddress
import json
import socket
import urllib.parse
from html.parser import HTMLParser
from http.client import HTTPSConnection


class DocumentationHTTP:
    @staticmethod
    def validate(url: str, hosts: list[str]) -> str:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in hosts
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError(
                "URL must belong to this library's HTTPS documentation"
            )
        addresses = socket.getaddrinfo(
            parsed.hostname, 443, type=socket.SOCK_STREAM
        )
        if not addresses or any(
            not ipaddress.ip_address(entry[4][0]).is_global
            for entry in addresses
        ):
            raise ValueError(
                "Documentation host resolved to a nonpublic address"
            )
        return url

    @staticmethod
    def get(url: str, headers: dict | None = None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        DocumentationHTTP.validate(url, [parsed.hostname])
        addresses = socket.getaddrinfo(
            parsed.hostname, 443, type=socket.SOCK_STREAM
        )
        address = addresses[0][4][0]
        if not all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError(
                "Documentation host resolved to a private address"
            )
        connection = HTTPSConnection(parsed.hostname, timeout=10)
        # Pin the validated address; TLS still verifies the documentation host.
        connection._create_connection = (
            lambda target, timeout, source_address=None: (
                socket.create_connection(
                    (address, 443), timeout, source_address
                )
            )
        )
        try:
            path = urllib.parse.urlunsplit(
                ("", "", parsed.path or "/", parsed.query, "")
            )
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": "history-agent/0.1",
                    **(headers or {}),
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(
                    f"Documentation HTTP status {response.status}"
                )
            data = response.read(524289)
            if len(data) > 524288:
                raise ValueError("Documentation response exceeds 512 KiB")
            return data
        finally:
            connection.close()

    @staticmethod
    def search(query: str, key: str) -> list[dict]:
        url = "https://api.search.brave.com/res/v1/web/search?" + (
            urllib.parse.urlencode({"q": query, "count": 5})
        )
        DocumentationHTTP.validate(url, ["api.search.brave.com"])
        data = json.loads(
            DocumentationHTTP.get(
                url,
                {
                    "Accept": "application/json",
                    "X-Subscription-Token": key,
                },
            )
        )
        return data.get("web", {}).get("results", [])

    @staticmethod
    def text(data: bytes) -> str:
        parser = DocumentationText()
        parser.feed(data.decode("utf-8", errors="replace"))
        return " ".join(parser.parts)


class DocumentationText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())
