"""Shared inference errors, limits and JSON encoding."""

import json

MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024


class ChatError(Exception):
    pass


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
