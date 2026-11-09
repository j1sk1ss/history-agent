"""
Index the connected server's guide outside model context; return one
checker article.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import quote

DOC_ACTION: str = "agent_checker_documentation"
DOC_METADATA: dict[str, object] = {
    "name": DOC_ACTION,
    "description": (
        "Read the exact checker's description and examples from this "
        "history server's /guide. Paginate long articles."
    ),
    "read_only": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["checker_name"],
        "properties": {
            "checker_name": {"type": "string", "minLength": 1},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8000,
                "default": 3000,
            },
        },
    },
}
CHECKER: re.Pattern[str] = re.compile(r"[A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)*\Z")


class GuideError(Exception):
    pass


class GuideParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.in_title: bool = False
        self.ignored: list[str] = []
        self.heading: tuple[int, str, list[str]] | None = None
        self.active: tuple[int, str, str] | None = None
        self.parts: list[str] = []
        self.sections: dict[str, dict[str, str]] = {}
        self.in_pre: bool = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes: dict[str, str | None] = dict(attrs)
        if tag == "title":
            self.in_title = True
        if self.ignored:
            if tag not in ("meta", "link", "br", "hr", "img", "input", "wbr"):
                self.ignored.append(tag)
            return
        if (
            tag in ("head", "nav", "script", "style")
            or attributes.get("id") == "TOC"
        ):
            self.ignored.append(tag)
            return
        if re.fullmatch(r"h[1-6]", tag):
            level: int = int(tag[1])
            if self.active and level <= self.active[0]:
                self.finish_section()
            self.heading = (level, attributes.get("id", ""), [])
        elif tag == "pre":
            self.in_pre = True
            self.add("\n\n```\n")
        elif tag in ("p", "table", "ul", "ol", "dl", "blockquote"):
            self.add("\n\n")
        elif tag in ("tr", "br"):
            self.add("\n")
        elif tag == "li":
            self.add("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if self.heading and tag == "h" + str(self.heading[0]):
            level: int
            anchor: str
            parts: list[str]
            level, anchor, parts = self.heading
            title: str = " ".join("".join(parts).split())
            self.heading = None
            name: str | None = (
                title
                if CHECKER.fullmatch(title)
                else anchor
                if CHECKER.fullmatch(anchor)
                else None
            )
            if not self.active and name:
                self.active = (level, name, anchor or name)
                self.parts = []
            self.add("\n\n" + "#" * level + " " + title + "\n\n")
        elif tag == "pre":
            self.add("\n```\n\n")
            self.in_pre = False
        elif tag in ("p", "table", "li", "blockquote"):
            self.add("\n")
        elif tag in ("th", "td"):
            self.add(" | ")

    def handle_data(self, text: str) -> None:
        if self.in_title:
            self.title += text
        if self.ignored:
            return
        if self.heading:
            self.heading[2].append(text)
        else:
            self.add(text if self.in_pre else re.sub(r"\s+", " ", text))

    def add(self, text: str) -> None:
        if self.active:
            self.parts.append(text)

    def finish_section(self) -> None:
        if self.active:
            name: str
            anchor: str
            _, name, anchor = self.active
            # Keep code indentation and blank lines intact, especially in
            # Python examples.
            text: str = "".join(self.parts).strip()
            self.sections.setdefault(
                name.casefold(),
                {"checker_name": name, "anchor": anchor, "text": text},
            )
            self.active, self.parts = None, []

    def close(self) -> None:
        super().close()
        self.finish_section()


class Guide:
    def __init__(
        self, url: str, load: Callable[[], str], ttl: float = 300,
    ) -> None:
        self.url: str
        self.load: Callable[[], str]
        self.ttl: float
        self.url, self.load, self.ttl = url, load, ttl
        self.parser: GuideParser | None
        self.error: str | None
        self.expires: float
        self.parser, self.error, self.expires = None, None, 0

    def checker(
        self, name: str, offset: int = 0, limit: int = 3000,
    ) -> dict[str, str | int | bool]:
        if time.monotonic() >= self.expires:
            try:
                parser: GuideParser = GuideParser()
                parser.feed(self.load())
                parser.close()
                if not parser.sections:
                    raise GuideError(
                        "The server guide contains no indexed checker articles"
                    )
                self.parser, self.error = parser, None
            except GuideError as error:
                self.parser, self.error = None, str(error)
            self.expires = time.monotonic() + (self.ttl if self.parser else 30)
        if not self.parser:
            raise GuideError(self.error or "Server guide is unavailable")
        section: dict[str, str] | None = self.parser.sections.get(
            name.casefold()
        )
        if not section:
            raise GuideError(
                "No exact checker article in the server guide: " + name
            )
        text: str = section["text"]
        end: int = min(len(text), offset + limit)
        result: dict[str, str | int | bool] = {
            "status": "available",
            "checker_name": section["checker_name"],
            "source_url": self.url
            + "#"
            + quote(section["anchor"], safe="._-"),
            "guide_title": self.parser.title.strip(),
            "text": text[offset:end],
            "offset": offset,
            "total_characters": len(text),
            "has_more": end < len(text),
        }
        if result["has_more"]:
            result["next_offset"] = end
        return result
