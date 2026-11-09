"""Cookie authentication and bounded HTTP access to history."""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPResponse

from history_agent.history.guide import GuideError
from history_agent.history.settings import HistoryError, Settings
from history_agent.http import NoRedirects

MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024


class HistoryTransport:
    def __init__(self, settings: Settings) -> None:
        parsed: urllib.parse.SplitResult = urllib.parse.urlsplit(settings.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("HISTORY_URL must be an http(s) URL")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                (
                    "Use credential environment variables, not "
                    "credentials or query parameters in HISTORY_URL"
                )
            )
        if (settings.user is None) != (settings.password is None):
            raise ValueError(
                (
                    "Set both HISTORY_USER and HISTORY_PASSWORD, or "
                    "use HISTORY_SESSION"
                )
            )
        self.settings: Settings = settings
        base: str = settings.url.rstrip("/")
        self.endpoint: str = (
            base if base.endswith("/history/api") else base + "/history/api"
        )
        self._cookies: http.cookiejar.CookieJar = http.cookiejar.CookieJar()
        self._opener: urllib.request.OpenerDirector = (
            urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(self._cookies),
                NoRedirects(),
            )
        )
        self._session: str | None = settings.session
        self._authenticated: bool = bool(settings.session)

    def load_guide(self, url: str) -> str:
        headers: dict[str, str] = {"Accept": "text/html"}
        if self._session:
            headers["Cookie"] = "sid=" + self._session
        request: urllib.request.Request = urllib.request.Request(
            url, headers=headers
        )
        try:
            with self._opener.open(
                request, timeout=self.settings.timeout
            ) as response:
                raw: bytes = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise GuideError("Server guide exceeds 16 MiB")
                if response.headers.get_content_type() != "text/html":
                    raise GuideError("Server /guide did not return HTML")
                return raw.decode(
                    response.headers.get_content_charset() or "utf-8"
                )
        except (
            OSError,
            urllib.error.URLError,
            UnicodeError,
            LookupError,
        ) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise GuideError(
                "Cannot read the configured history server's /guide"
            ) from None

    def _request(
        self, action: str, query: dict[str, str | int | None] | None = None,
        body: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | None] = {**(query or {}), "act": action}
        url: str = self.endpoint + "?" + urllib.parse.urlencode(params)
        encoded: bytes | None = (
            None
            if body is None
            else urllib.parse.urlencode(body).encode("utf-8")
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded; charset=utf-8"
            )
        if self._session:
            headers["Cookie"] = "sid=" + self._session
        request: urllib.request.Request = urllib.request.Request(
            url, data=encoded, headers=headers
        )
        try:
            try:
                response: HTTPResponse | urllib.error.HTTPError = (
                    self._opener.open(request, timeout=self.settings.timeout)
                )
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status: int = response.code
                raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError) as error:
            raise HistoryError(
                (
                    "History request failed; outcome may be unknown. "
                    "Inspect review history before retrying a write."
                )
            ) from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HistoryError(
                (
                    "History response exceeds 2 MiB; request a "
                    "smaller page or source window"
                )
            )
        try:
            result: dict[str, object] = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise HistoryError(
                f"History returned a non-JSON response (HTTP {status})"
            ) from error
        if not isinstance(result, dict):
            raise HistoryError("History returned an unexpected JSON response")
        if status >= 400 or result.get("status") != "OK":
            message: str = str(
                result.get("error", f"History request failed (HTTP {status})")
            )
            for secret in (self.settings.password, self._session):
                if secret:
                    message = message.replace(secret, "[redacted]")
            raise HistoryError(
                message, auth_error=str(result.get("authError")) == "1"
            )
        return result

    def _login(self) -> None:
        if self.settings.user is None:
            return
        self._session = None
        self._request(
            "auth",
            body={"user": self.settings.user, "key": self.settings.password},
        )
        if not any((cookie.name == "sid" for cookie in self._cookies)):
            raise HistoryError(
                "History authentication did not return a sid cookie"
            )
        self._authenticated = True

    def _call(
        self, action: str, query: dict[str, str | int | None] | None = None,
        body: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        if not self._authenticated and self.settings.user is not None:
            self._login()
        try:
            return self._request(action, query, body)
        except HistoryError as error:
            if not error.auth_error or self.settings.user is None:
                raise
            self._login()
            return self._request(action, query, body)
