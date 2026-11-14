"""Private, bounded event logs for diagnosing model and tool failures."""

import json
import os
import re
import threading
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path


class DiagnosticLog:
    def __init__(
        self,
        root: str | Path,
        *,
        secrets: tuple[str, ...] = (),
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = Path(root) / "logs" / f"{stamp}-{uuid.uuid4().hex}.jsonl"
        self.secrets = tuple(value for value in secrets if value)
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.disabled = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(fd)
        except OSError as error:
            self.disable(error)

    def disable(self, error: OSError) -> None:
        self.disabled = True
        warnings.warn(
            f"Diagnostic logging unavailable: {error}",
            RuntimeWarning,
            stacklevel=2,
        )

    def redact(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: "[redacted]"
                if key.casefold()
                in {
                    "authorization",
                    "api_key",
                    "password",
                    "session",
                    "access_token",
                    "refresh_token",
                }
                else self.redact(entry)
                for key, entry in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact(entry) for entry in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[redacted]")
            return re.sub(
                r"(\w+://)[^\s/@]+:[^\s/@]+@",
                r"\1[redacted]@",
                value,
            )
        return value

    def event(self, kind: str, **data: object) -> None:
        if self.disabled or kind in ("content", "reasoning", "tool_delta"):
            return
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": kind,
            **self.redact(data),
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        if len(line.encode()) > self.max_bytes:
            line = (
                json.dumps(
                    {
                        "time": record["time"],
                        "event": kind,
                        "detail_omitted": "Record exceeds log byte limit",
                    }
                )
                + "\n"
            )
        with self.lock:
            try:
                size = self.path.stat().st_size + len(line.encode())
                if size > self.max_bytes:
                    self.rotate()
                fd = os.open(self.path, os.O_APPEND | os.O_WRONLY)
                with os.fdopen(fd, "a", encoding="utf-8") as target:
                    target.write(line)
            except OSError as error:
                self.disable(error)

    def rotate(self) -> None:
        oldest = self.path.with_suffix(".jsonl.2")
        previous = self.path.with_suffix(".jsonl.1")
        oldest.unlink(missing_ok=True)
        if previous.exists():
            previous.replace(oldest)
        self.path.replace(previous)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
