"""Host-only UD precondition for every review write, including direct MCP."""

import threading

from history_agent.history.cache_lease import CacheLease
from history_agent.history.cache_policy import digest
from history_agent.history.settings import HistoryError

LOCKS = [threading.RLock() for _ in range(256)]


class ReviewWriteRejected(HistoryError):
    """No POST was sent: the review failed the write precondition."""


class ReviewStateConflict(ReviewWriteRejected):
    """Fresh state confirms an existing review that must be preserved."""


class ReviewWriter:
    def __init__(self, transport) -> None:
        self.transport = transport

    def save(self, values, send):
        identity = digest(
            [
                self.transport.endpoint,
                values["project_id"],
                values["stream_id"],
                values["warning_id"],
            ]
        )
        with LOCKS[int(identity[:8], 16) % len(LOCKS)]:
            lease = CacheLease(
                self.transport.cache.backend,
                self.transport.cache.prefix + "write:" + identity,
            )
            if lease.backend.available and not lease.acquire():
                if lease.backend.available:
                    raise ReviewWriteRejected(
                        "Another harness is reviewing this warning; "
                        "no write sent"
                    )
            try:
                self.verify(values)
                if lease.thread and (
                    lease.lost.is_set()
                    or lease.backend.call("get", lease.key)
                    not in (lease.token, lease.token.encode())
                ):
                    raise ReviewWriteRejected(
                        "Review lease lost; no write sent"
                    )
                try:
                    return send()
                finally:
                    # Invalidate even when the server may have committed a
                    # write whose HTTP response was lost.
                    self.transport.cache.invalidate()
            finally:
                lease.close()

    def verify(self, values):
        scope = {key: values[key] for key in ("project_id", "stream_id")}
        try:
            statuses = self.transport._call(
                "get_value_filter",
                {
                    **scope,
                    "snapshot_id": "HEAD",
                    "field": "status",
                },
            )
            undecided = [
                str(entry["id"])
                for entry in statuses.get("classification", [])
                if entry.get("name") == "Undecided"
            ]
            result = self.transport._uncached_call(
                "get_warning",
                {
                    **scope,
                    "snapshot_id": "HEAD",
                    "warning_id": values["warning_id"],
                },
            )
            warning = result.get("warning", {})
            attributes = warning.get("attributes", {})
            if (
                warning.get("warning_id") != values["warning_id"]
                or len(undecided) != 1
                or attributes.get("classification_id") is None
            ):
                raise ReviewWriteRejected(
                    "Cannot confirm the current warning and UD classification;"
                    " no write sent"
                )
            if str(attributes["classification_id"]) != undecided[
                0
            ] or attributes.get("comment"):
                raise ReviewStateConflict(
                    "Review protected: only an unreviewed UD warning can be "
                    "saved; existing classifications/comments are preserved"
                )
        except ReviewWriteRejected:
            raise
        except (HistoryError, KeyError, TypeError, AttributeError) as error:
            raise ReviewWriteRejected(
                "Cannot confirm a fresh UD review state; no write sent. "
                + str(error)
            ) from error
