"""Singleton factory for immutable, statically declared tool definitions."""

from __future__ import annotations

from threading import RLock

from mcp.types import Tool
from pydantic import BaseModel


class ToolRegistry:
    _instance: ToolRegistry | None = None
    _lock = RLock()

    def __new__(cls) -> ToolRegistry:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._definitions = {}
            return cls._instance

    @staticmethod
    def create(name: str, description: str, request: type[BaseModel]) -> Tool:
        registry = ToolRegistry()
        key = (name, description, request)
        with registry._lock:
            if key not in registry._definitions:
                registry._definitions[key] = Tool(
                    name=name,
                    description=description,
                    input_schema=request.model_json_schema(),
                )
            return registry._definitions[key].model_copy(deep=True)
