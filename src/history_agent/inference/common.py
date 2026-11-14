"""Shared inference errors, limits and JSON encoding."""

import json
import socket
import ssl
import urllib.error

from history_agent.inference.rejections import rejected_parameter

MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024


class ChatError(Exception):
    pass


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def request_failure(error: BaseException) -> str:
    """Explain failures without exposing response bodies or credentials."""
    if isinstance(error, urllib.error.HTTPError):
        detail = {
            400: "request rejected; check model and supported parameters",
            401: "authentication failed; check the API key",
            403: "access denied; check key permissions for this model",
            404: "endpoint or model not found; check URL and model ID",
            429: "rate limit or quota exceeded",
        }.get(error.code, "server rejected the request")
        hint = rejected_parameter(error) if error.code == 400 else ""
        return f"Model API returned HTTP {error.code}: {detail}." + hint
    reason = (
        error.reason if isinstance(error, urllib.error.URLError) else error
    )
    if isinstance(reason, TimeoutError):
        return "Model request timed out; increase Timeout in Advanced."
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            "TLS certificate verification failed; check the server "
            "certificate and trusted CA configuration."
        )
    if isinstance(reason, socket.gaierror):
        return "Cannot resolve model server hostname; check the model URL."
    if isinstance(reason, ConnectionRefusedError):
        return "Model connection refused; check the server address and port."
    return (
        "Model request failed or stream was interrupted; "
        "check endpoint/authentication/timeout."
    )
