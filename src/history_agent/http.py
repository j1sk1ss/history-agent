"""HTTP policy shared by history and model transports."""

from __future__ import annotations

import urllib.request
from email.message import Message
from http.client import HTTPResponse


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: HTTPResponse, code: int,
        msg: str, headers: Message, newurl: str,
    ) -> None:
        # Credentials and review requests must remain on the configured
        # endpoint.
        return None
