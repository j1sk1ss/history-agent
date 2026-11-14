"""Explain transport failures without exposing URLs or credentials."""

import http.client
import socket
import ssl
import urllib.error

from history_agent.history.constants import WRITE_ACTIONS


def request_failure(action: str, error: Exception, timeout: float) -> str:
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, TimeoutError):
        detail = f"timed out after {timeout:g} seconds"
    elif isinstance(cause, socket.gaierror):
        detail = "cannot resolve the history server hostname"
    elif isinstance(cause, ConnectionRefusedError):
        detail = (
            "connection refused; check the history server address and port"
        )
    elif isinstance(cause, ssl.SSLCertVerificationError):
        detail = "TLS certificate verification failed"
    elif isinstance(cause, (ConnectionError, http.client.HTTPException)):
        detail = "connection interrupted while reading the server response"
    else:
        detail = "connection failed; check the history server and network"
    message = f"History request failed for {action}: {detail}."
    if action in WRITE_ACTIONS:
        message += (
            " Write outcome may be unknown. Inspect review history before "
            "retrying a write."
        )
    return message
