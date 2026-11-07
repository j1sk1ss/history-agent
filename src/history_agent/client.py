"""
Discover history actions and translate validated arguments to the existing
HTTP API.
"""

from __future__ import annotations

import copy
import http.cookiejar
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from .comments import review_comment
from .comparison import COMPARE_ACTION, COMPARE_META, legacy_compare
from .current import CURRENT_ACTION, CURRENT_META, resolve_current
from .guide import DOC_ACTION, DOC_METADATA, Guide, GuideError
from .links import URL_ACTION, URL_META, resolve_history_url

READ_ACTIONS = frozenset(
    {
        "whoami",
        "get_projects",
        "get_project_group_info",
        "get_project_info",
        "branches",
        "get_snapshots",
        "get_warnings",
        "get_warning",
        "get_warning_trace",
        "get_warning_trace_and_role",
        "get_all_reviews",
        "get_file_tree",
        "get_tokens",
        "get_ref_tokens",
        "get_value_filter",
        "agent_source",
        "agent_resolve_symbol",
        "agent_find_warnings",
        "agent_checker_info",
        DOC_ACTION,
        COMPARE_ACTION,
        URL_ACTION,
        CURRENT_ACTION,
    }
)
WRITE_ACTIONS = frozenset({"agent_set_review"})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class HistoryError(Exception):
    def __init__(self, message: str, *, auth_error: bool = False):
        super().__init__(message)
        self.auth_error = auth_error


@dataclass(frozen=True)
class Settings:
    url: str
    user: str | None = None
    password: str | None = None
    session: str | None = None
    timeout: float = 20

    @classmethod
    def from_env(cls) -> Settings:
        config = {}
        path = os.environ.get("HISTORY_CONFIG")
        if path:
            try:
                with open(path, encoding="utf-8") as source:
                    config = json.load(source)
                allowed = {
                    "HISTORY_URL",
                    "HISTORY_USER",
                    "HISTORY_PASSWORD",
                    "HISTORY_SESSION",
                }
                if (
                    not isinstance(config, dict)
                    or set(config) - allowed
                    or any(
                        value is not None and not isinstance(value, str)
                        for value in config.values()
                    )
                ):
                    raise ValueError()
            except (OSError, ValueError) as error:
                raise ValueError(
                    "Cannot read valid history settings from HISTORY_CONFIG"
                ) from error

        def value(name, default=None):
            return (
                os.environ[name]
                if name in os.environ
                else config.get(name, default)
            )

        return cls(
            url=value("HISTORY_URL", "http://127.0.0.1:8060"),
            user=value("HISTORY_USER"),
            password=value("HISTORY_PASSWORD"),
            session=value("HISTORY_SESSION"),
        )


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials and review requests must remain on the configured
        # endpoint.
        return None


class HistoryClient:
    def __init__(self, settings: Settings):
        parsed = urllib.parse.urlsplit(settings.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("HISTORY_URL must be an http(s) URL")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use credential environment variables, not credentials or "
                "query parameters in HISTORY_URL"
            )
        if (settings.user is None) != (settings.password is None):
            raise ValueError(
                "Set both HISTORY_USER and HISTORY_PASSWORD, or use "
                "HISTORY_SESSION"
            )
        self.settings = settings
        base = settings.url.rstrip("/")
        self.endpoint = (
            base if base.endswith("/history/api") else base + "/history/api"
        )
        self._guide = Guide(
            self.endpoint.removesuffix("/history/api") + "/guide",
            self._load_guide,
        )
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self._cookies),
            NoRedirects(),
        )
        self._session = settings.session
        self._authenticated = bool(settings.session)
        self._catalog: dict[str, dict[str, Any]] | None = None
        self._legacy_comparison = False
        self._lock = threading.RLock()

    def _load_guide(self):
        headers = {"Accept": "text/html"}
        if self._session:
            headers["Cookie"] = "sid=" + self._session
        request = urllib.request.Request(self._guide.url, headers=headers)
        try:
            with self._opener.open(
                request, timeout=self.settings.timeout
            ) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
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
        self, action: str, query: dict | None = None, body: dict | None = None
    ) -> dict:
        params = {**(query or {}), "act": action}
        url = self.endpoint + "?" + urllib.parse.urlencode(params)
        encoded = (
            None
            if body is None
            else urllib.parse.urlencode(body).encode("utf-8")
        )
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded; charset=utf-8"
            )
        if self._session:
            headers["Cookie"] = "sid=" + self._session
        request = urllib.request.Request(url, data=encoded, headers=headers)
        try:
            try:
                response = self._opener.open(
                    request, timeout=self.settings.timeout
                )
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.code
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError) as error:
            # Do not echo URLs, request bodies, cookies or credentials in tool
            # errors.
            raise HistoryError(
                "History request failed; outcome may be unknown. Inspect "
                "review history before retrying a write."
            ) from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HistoryError(
                "History response exceeds 2 MiB; request a smaller page or "
                "source window"
            )
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise HistoryError(
                f"History returned a non-JSON response (HTTP {status})"
            ) from error
        if not isinstance(result, dict):
            raise HistoryError("History returned an unexpected JSON response")
        if status >= 400 or result.get("status") != "OK":
            message = str(
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
        if not any(cookie.name == "sid" for cookie in self._cookies):
            raise HistoryError(
                "History authentication did not return a sid cookie"
            )
        self._authenticated = True

    def _call(
        self, action: str, query: dict | None = None, body: dict | None = None
    ) -> dict:
        if not self._authenticated and self.settings.user is not None:
            self._login()
        try:
            return self._request(action, query, body)
        except HistoryError as error:
            if not error.auth_error or self.settings.user is None:
                raise
            # Only explicit authentication rejection is retried: the action has
            # not run.
            self._login()
            return self._request(action, query, body)

    def _load_catalog(self) -> dict[str, dict[str, Any]]:
        if self._catalog is not None:
            return self._catalog
        legacy = self._call("get_actions").get("actions", {})
        if not isinstance(legacy, dict):
            raise HistoryError(
                "History get_actions returned an invalid catalog"
            )
        catalog = {
            name: self._legacy_action(name, metadata)
            for name, metadata in legacy.items()
            if name in READ_ACTIONS
        }
        if "agent_actions" in legacy:
            extension = (
                self._call("agent_actions").get("data", {}).get("actions", {})
            )
            for name, metadata in extension.items():
                if name not in READ_ACTIONS | WRITE_ACTIONS:
                    continue
                schema = copy.deepcopy(metadata["input_schema"])
                schema["additionalProperties"] = False
                Draft202012Validator.check_schema(schema)
                catalog[name] = {
                    "name": name,
                    "description": metadata["description"],
                    "input_schema": schema,
                    "read_only": name in READ_ACTIONS,
                }
        catalog[DOC_ACTION] = copy.deepcopy(DOC_METADATA)
        catalog[URL_ACTION] = copy.deepcopy(URL_META)
        catalog[CURRENT_ACTION] = copy.deepcopy(CURRENT_META)
        if (
            COMPARE_ACTION not in catalog
            and "get_warnings_compare_svace2svace" in legacy
        ):
            catalog[COMPARE_ACTION] = copy.deepcopy(COMPARE_META)
            self._legacy_comparison = True
        self._catalog = catalog
        return catalog

    @staticmethod
    def _legacy_action(name: str, metadata: dict) -> dict:
        properties, required = {}, []
        for key, parameter in metadata.get("params", {}).items():
            # The legacy get_tokens documentation says 'path', while run()
            # reads 'file'.
            if name == "get_tokens" and key == "path":
                key = "file"
            prop: dict[str, Any] = {
                "type": "string",
                "description": parameter.get("desc", ""),
                "x-http-parameter-location": parameter.get("type", "get"),
            }
            if key in {
                "project_id",
                "project_group_id",
                "start_line",
                "end_line",
                "number",
                "count",
            }:
                prop["type"] = "integer"
                prop["minimum"] = (
                    0 if key in {"project_id", "project_group_id"} else 1
                )
            if key == "from" and name == "get_warnings":
                prop.update(type="integer", minimum=0)
            if key == "format":
                prop["enum"] = ["json"]
            if key == "extended":
                prop["type"] = "boolean"
            properties[key] = prop
            if parameter.get("is_req"):
                required.append(key)
        for count_key in ("count", "number"):
            if count_key in properties:
                properties[count_key].update(default=20, maximum=100)
        if name == "get_warnings" and "count" not in properties:
            properties["count"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "x-http-parameter-location": "post",
            }
        return {
            "name": name,
            "description": metadata.get("desc", ""),
            "read_only": True,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }

    def discover(self, action: str | None = None) -> dict:
        with self._lock:
            catalog = self._load_catalog()
            if action:
                if action not in catalog:
                    raise HistoryError(
                        "Action is unavailable through this adapter: " + action
                    )
                return copy.deepcopy(catalog[action])
            return {
                "actions": [
                    {
                        "name": name,
                        "description": info["description"][:180],
                        "tool": "history_read"
                        if info["read_only"]
                        else "history_write",
                    }
                    for name, info in sorted(catalog.items())
                ],
                "workflow": "history://workflow",
                "next": "Call history_discover(action=...) for an action's "
                "input "
                "schema",
            }

    def execute(
        self, action: str, arguments: dict[str, Any], *, write: bool = False
    ) -> dict:
        # Enforce the separation before network access, independent of server
        # metadata.
        if action not in (WRITE_ACTIONS if write else READ_ACTIONS):
            raise HistoryError(
                "Action is not allowed through this tool: " + action
            )
        with self._lock:
            info = self.discover(action)
            schema = info["input_schema"]
            errors = sorted(
                Draft202012Validator(schema).iter_errors(arguments),
                key=lambda e: str(e.path),
            )
            if errors:
                # Schema errors can contain the input value: avoid echoing full
                # arguments.
                error = errors[0]
                path = ".".join(map(str, error.path)) or "arguments"
                raise HistoryError(
                    f"Invalid {path}: failed {error.validator} validation; "
                    "see history_discover"
                )
            values = copy.deepcopy(arguments)
            if action == "agent_set_review":
                values["comment"] = review_comment(values["comment"])
                if not values["comment"]:
                    raise HistoryError(
                        "A non-empty review explanation is required"
                    )
            for key, prop in schema["properties"].items():
                if key not in values and "default" in prop:
                    values[key] = prop["default"]
            if action == DOC_ACTION:
                try:
                    return self._guide.checker(
                        values["checker_name"],
                        values["offset"],
                        values["limit"],
                    )
                except GuideError as error:
                    raise HistoryError(str(error)) from None
            if action == COMPARE_ACTION and self._legacy_comparison:
                return legacy_compare(self, values, HistoryError)
            if action == URL_ACTION:
                return resolve_history_url(self, values["url"], HistoryError)
            if action == CURRENT_ACTION:
                return resolve_current(self, values, HistoryError)
            query, body = {}, {}
            for key, value in values.items():
                if isinstance(value, bool):
                    value = str(value).lower()
                target = (
                    body
                    if schema["properties"][key].get(
                        "x-http-parameter-location"
                    )
                    == "post"
                    else query
                )
                target[key] = str(value)
            result = self._call(action, query, body or None)
            # Preserve legacy response fields; unwrap only the extension's
            # known envelope.
            if action.startswith("agent_"):
                data = result["data"]
                if action == "agent_checker_info":
                    try:
                        documentation = self._guide.checker(
                            values["checker_name"]
                        )
                    except GuideError as error:
                        documentation = {
                            "status": "unavailable",
                            "error": str(error),
                        }
                    return {**data, "documentation": documentation}
                return data
            return {
                key: value for key, value in result.items() if key != "status"
            }
