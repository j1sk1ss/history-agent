"""Compatibility access to the central, configurable prompt catalog."""

from history_agent.prompts import Prompts


def __getattr__(name: str) -> str:
    if not name.isupper():
        raise AttributeError(name)
    return Prompts.get("interfaces." + name.lower())
