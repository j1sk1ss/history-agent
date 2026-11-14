"""Actionable HTTP rejection details without echoing provider payloads."""

import http.client
import json
import re
from urllib.error import HTTPError

HINTS = {
    "stream_options": "Disable streaming usage reporting in settings.",
    "stream": "Disable streaming in settings.",
    "max_tokens": "Lower Output tokens per request in settings.",
    "max_completion_tokens": "Lower Output tokens per request in settings.",
    "max_output_tokens": "Lower Output tokens per request in settings.",
    "tools": "This model or endpoint must support function tools.",
    "messages": "Check the selected provider and conversation format.",
    "model": "Check the model ID and access to this model.",
    "chat_template_kwargs": "Disable No thinking in settings.",
}
TOKEN_LIMIT = re.compile(
    r"\b(max_tokens|max_completion_tokens|max_output_tokens)\s*=\s*(\d{1,12})"
    r"\s+cannot be greater than\s+"
    r"(max_model_len|max_output_tokens|max_tokens)\s*=\s*(\d{1,12})\b"
)
GENERIC_HINT = (
    " Check tool support, streaming/usage options and output tokens; "
    "the settings connection check uses these same parameters."
)


def rejection_records(value: object):
    """Follow bounded error envelopes, including LiteLLM's embedded JSON."""
    pending = [value]
    decoder = json.JSONDecoder()
    for _ in range(64):
        if not pending:
            return
        entry = pending.pop()
        if isinstance(entry, dict):
            yield entry
            pending.extend(
                entry[key]
                for key in ("error", "detail", "message")
                if key in entry
            )
        elif isinstance(entry, list):
            pending.extend(entry[:16])
        elif isinstance(entry, str):
            yield entry
            for start in list(re.finditer(r"\{", entry))[:16]:
                try:
                    nested, _ = decoder.raw_decode(entry[start.start() :])
                except ValueError:
                    continue
                if isinstance(nested, dict):
                    pending.append(nested)


def rejected_parameter(error: HTTPError) -> str:
    """Expose only known parameter names and numeric constraints."""
    try:
        records = list(rejection_records(json.loads(error.read(16384))))
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        http.client.HTTPException,
    ):
        return GENERIC_HINT
    for record in records:
        if isinstance(record, str) and (match := TOKEN_LIMIT.search(record)):
            parameter, requested, bound, limit = match.groups()
            return (
                f" Requested {parameter}={requested} exceeds the model limit "
                f"{bound}={limit}. Lower Output tokens per request; "
                "input and output must fit the model context."
            )
    for record in reversed(records):
        parameter = record.get("param") if isinstance(record, dict) else None
        if isinstance(parameter, str) and parameter in HINTS:
            return f" Rejected parameter: {parameter}. {HINTS[parameter]}"
    return GENERIC_HINT
