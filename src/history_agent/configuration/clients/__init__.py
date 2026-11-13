"""Select a configuration builder by the requested client identifier."""

from argparse import Namespace
from collections.abc import Callable

from history_agent.configuration.clients.claude_code import (
    build as claude_code,
)
from history_agent.configuration.clients.codex import build as codex
from history_agent.configuration.clients.copilot import build as copilot
from history_agent.configuration.clients.hermes import build as hermes
from history_agent.configuration.clients.openclaw import build as openclaw
from history_agent.configuration.clients.opencode import build as opencode
from history_agent.configuration.clients.pi import build as pi
from history_agent.configuration.files import Edit

BUILDERS: dict[
    str, Callable[[Namespace, dict[str, object], str | None], list[Edit]]
] = {
    "copilot": copilot,
    "opencode": opencode,
    "openclaw": openclaw,
    "hermes": hermes,
    "claude-code": claude_code,
    "codex": codex,
    "pi": pi,
}
