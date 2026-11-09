"""Read, merge and atomically update configuration files."""

from __future__ import annotations

import copy
import io
import json
import os
import re
import tempfile
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def merge(
    original: MutableMapping[str, object], patch: dict[str, object],
) -> MutableMapping[str, object]:
    for name, value in patch.items():
        if (
            name in ("env", "environment")
            and isinstance(value, dict)
            and "HISTORY_CONFIG" in value
        ):
            # Migrate old inline history settings: leaving them here would
            # override the new config file.
            old_env: dict[str, str] = dict(original.get(name, {}))
            for key in (
                "HISTORY_URL",
                "HISTORY_USER",
                "HISTORY_PASSWORD",
                "HISTORY_SESSION",
                "HISTORY_CONFIG",
            ):
                old_env.pop(key, None)
            original[name] = {**old_env, **value}
            continue
        if name == "HISTORY_URL" and original.get(name) not in (None, value):
            # Credentials for one history service must not leak to a different
            # configured service.
            for key in ("HISTORY_USER", "HISTORY_PASSWORD", "HISTORY_SESSION"):
                original.pop(key, None)
        if isinstance(value, dict) and isinstance(
            original.get(name), MutableMapping
        ):
            merge(original[name], value)
        else:
            original[name] = copy.deepcopy(value)
    return original


@dataclass
class Edit:
    path: Path
    format: str
    patch: dict[str, object]


def config_path(root: Path, name: str) -> Path:
    # An explicitly supplied root is self-contained; do not accidentally touch
    # the real user profile.
    return root / name


def read_data(
    path: Path, format: str,
) -> MutableMapping[str, object] | list[dict[str, object]]:
    if not path.exists():
        return [] if format == "providers" else {}
    text: str = path.read_text(encoding="utf-8")
    try:
        if format in ("json", "providers"):
            try:
                return json.loads(text)
            except ValueError:
                import json5

                return json5.loads(text, allow_duplicate_keys=False)
        if format == "toml":
            import tomlkit

            return tomlkit.parse(text)
        if format == "yaml":
            from ruamel.yaml import YAML

            return YAML().load(text) or {}
    except Exception:
        raise ValueError(
            f"Cannot parse existing configuration: {path}; "
            "no files were changed"
        ) from None
    raise ValueError("Unsupported configuration format")


def render(edit: Edit) -> str:
    if edit.format == "dotenv":
        text: str = edit.path.read_text() if edit.path.exists() else ""
        for name, value in edit.patch.items():
            line: str = name + "=" + json.dumps(value, ensure_ascii=False)
            pattern: re.Pattern[str] = re.compile(
                r"^(?:export\s+)?" + re.escape(name) + r"=.*$", re.MULTILINE
            )
            text = (
                pattern.sub(lambda _: line, text)
                if pattern.search(text)
                else text.rstrip("\n") + "\n" + line + "\n"
            )
        return text
    data: MutableMapping[str, object] | list[dict[str, object]] = read_data(
        edit.path, edit.format
    )
    if edit.format == "providers":
        if not isinstance(data, list):
            raise ValueError(f"Expected a provider array: {edit.path}")
        for i, provider in enumerate(data):
            if (
                provider.get("name") == edit.patch["name"]
                and provider.get("vendor") == edit.patch["vendor"]
            ):
                data[i] = merge(provider, edit.patch)
                break
        else:
            data.append(copy.deepcopy(edit.patch))
    else:
        if not isinstance(data, MutableMapping):
            raise ValueError(f"Expected a configuration object: {edit.path}")
        data = merge(data, edit.patch)
    if edit.format == "toml":
        import tomlkit

        return tomlkit.dumps(data)
    if edit.format == "yaml":
        from ruamel.yaml import YAML

        stream: io.StringIO = io.StringIO()
        YAML().dump(data, stream)
        return stream.getvalue()
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_plan(plan: list[Edit]) -> tuple[list[Path], list[Path]]:
    # Render every file first: a malformed existing config must not leave a
    # partial setup.
    rendered: list[tuple[Path, str]] = [
        (edit.path, render(edit)) for edit in plan
    ]
    stamp: str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    changed: list[tuple[Path, bytes | None]] = []
    backups: list[Path] = []
    try:
        for path, text in rendered:
            old: bytes | None = path.read_bytes() if path.exists() else None
            if old == text.encode("utf-8"):
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if old is not None:
                backup = path.with_name(
                    path.name + ".history-agent-" + stamp + ".bak"
                )
                private_write(backup, old)
                backups.append(backup)
            private_write(path, text.encode("utf-8"))
            changed.append((path, old))
    except BaseException:
        for path, old in reversed(changed):
            if old is None:
                path.unlink()
            else:
                private_write(path, old)
        raise
    return [path for path, _ in changed], backups


def private_write(path: Path, content: bytes) -> None:
    # Replace atomically; neither an incomplete config nor credentials are
    # exposed through broad permissions.
    fd: int
    temporary: str
    fd, temporary = tempfile.mkstemp(prefix=".history-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
