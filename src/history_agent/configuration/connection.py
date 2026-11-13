"""Validate endpoints and discover an explicitly compatible model."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


def valid_url(value: str) -> str:
    url: urllib.parse.SplitResult = urllib.parse.urlsplit(value)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Use an HTTP(S) URL without credentials, query or fragment; "
            "put credentials in environment variables"
        )
    return value.rstrip("/")


def history_credentials() -> None:
    user: str | None
    password: str | None
    user, password = (
        os.environ.get("HISTORY_USER"),
        os.environ.get("HISTORY_PASSWORD"),
    )
    if (user is None) != (password is None):
        raise ValueError(
            "Set both HISTORY_USER and HISTORY_PASSWORD, or only "
            "HISTORY_SESSION"
        )


def prepare(args: argparse.Namespace) -> None:
    if getattr(args, "prepared", False):
        return
    args.history_url = valid_url(args.history_url)
    history_credentials()
    if args.client == "pi":
        # Pi manages its own models and authentication; the setup only
        # connects this project's history MCP server.
        args.api = None
        args.model = None
        args.model_base = None
        args.model_endpoint = None
        args.prepared = True
        return
    if not args.model_url:
        raise ValueError(
            "--model-url is required for this client; pi does not need it"
        )
    url: str = valid_url(args.model_url)
    expected: str = {"codex": "responses", "claude-code": "messages"}.get(
        args.client, "chat-completions"
    )
    args.api = expected if args.model_api == "auto" else args.model_api
    if args.client in ("codex", "claude-code") and args.api != expected:
        raise ValueError(
            f"{args.client} requires the {expected} API; "
            "provide a compatible endpoint/gateway"
        )
    if (
        args.client in ("opencode", "openclaw", "hermes")
        and args.api != "chat-completions"
    ):
        raise ValueError(
            f"This {args.client} setup supports chat-completions endpoints"
        )
    paths: dict[str, str] = {
        "chat-completions": "/chat/completions",
        "responses": "/responses",
        "messages": "/messages",
    }
    explicit: str | None = next(
        (kind for kind, suffix in paths.items() if url.endswith(suffix)), None
    )
    if explicit and explicit != args.api:
        raise ValueError(
            f"The supplied URL uses {explicit}, but {args.client} is "
            f"configured for {args.api}; supply a compatible endpoint"
        )
    base: str = url[: -len(paths[explicit])] if explicit else url
    if not re.search(r"/v\d+$", base):
        base += "/v1"
    args.model_base = base
    args.model_endpoint = base + paths[args.api]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.model_key_env):
        raise ValueError(
            "--model-key-env must be an environment variable name"
        )
    if (
        args.context_window <= args.max_output_tokens
        or args.max_output_tokens < 1
    ):
        raise ValueError(
            "The context window must be larger than the positive output "
            "token budget"
        )
    if not args.model:
        args.model = discover_model(base, args.model_key_env, args.api)
    if not args.model.strip() or any(ord(c) < 32 for c in args.model):
        raise ValueError(
            "Model ID must be non-empty and contain no control characters"
        )
    args.prepared = True


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def discover_model(
    base: str, key_env: str, api: str = "chat-completions",
    *, timeout: float = 15,
) -> str:
    headers: dict[str, str] = {"Accept": "application/json"}
    key: str | None = os.environ.get(key_env)
    if api == "messages":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["Authorization"] = "Bearer " + key
    request: urllib.request.Request = urllib.request.Request(
        base + "/models", headers=headers
    )
    try:
        opener: urllib.request.OpenerDirector = urllib.request.build_opener(
            NoRedirects()
        )
        with opener.open(request, timeout=timeout) as response:
            raw: bytes = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        data: dict[str, object] = json.loads(raw)
        models: list[str] = sorted(
            {
                m["id"]
                for m in data["data"]
                if isinstance(m.get("id"), str) and m["id"]
            }
        )
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        raise ValueError(
            "Cannot discover a model from /models; pass --model "
            "explicitly and check endpoint/authentication"
        ) from None
    if len(models) != 1:
        raise ValueError(
            "The endpoint must list exactly one model for automatic "
            "selection; pass --model explicitly"
        )
    return models[0]
