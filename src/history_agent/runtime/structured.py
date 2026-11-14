"""Transform embedded JSON while preserving the surrounding prose."""

import json
from collections.abc import Callable

from history_agent.inference.model import compact


def json_fragments(value: str, transform: Callable[[object], object]) -> str:
    decoder = json.JSONDecoder()
    parts = []
    offset = 0
    while offset < len(value):
        starts = [value.find(char, offset) for char in ("{", "[")]
        starts = [index for index in starts if index >= 0]
        if not starts:
            break
        start = min(starts)
        parts.append(value[offset:start])
        try:
            data, length = decoder.raw_decode(value[start:])
        except ValueError:
            parts.append(value[start])
            offset = start + 1
            continue
        parts.append(compact(transform(data)))
        offset = start + length
    return "".join(parts) + value[offset:]
