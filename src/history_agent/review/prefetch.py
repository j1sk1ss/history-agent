"""Attach bounded, scope-validated evidence before a warning investigation."""

from history_agent.history.client import HistoryError
from history_agent.inference.model import ChatError, compact
from history_agent.review import prompts
from history_agent.runtime.agent import Harness
from history_agent.runtime.messages import tool_exchange


class ReviewPrefetch:
    async def prefetch(
        self,
        reviewer: Harness,
        checker: dict[str, object],
    ) -> None:
        detail_args: dict[str, object] = {
            **self.scope,
            "warning_id": self.row.warning_id,
        }
        detail: dict[str, object] = await self.read("get_warning", detail_args)
        if detail.get("warning", {}).get("warning_id") != self.row.warning_id:
            raise ChatError("Snapshot warning identity mismatch")
        packets: list[tuple[str, dict[str, object], dict[str, object]]] = [
            (
                "agent_checker_info",
                {"checker_name": self.row.checker},
                checker,
            ),
            (
                "get_warning",
                detail_args,
                {
                    **detail,
                    "host_current_review": self.baseline,
                },
            ),
        ]
        review_args: dict[str, object] = {
            key: detail_args[key]
            for key in ("project_id", "stream_id", "warning_id")
        }
        packets.append(
            (
                "get_all_reviews",
                review_args,
                await self.read("get_all_reviews", review_args),
            )
        )
        trace: dict[str, object] = await self.read(
            "get_warning_trace_and_role", detail_args
        )
        packets.append(("get_warning_trace_and_role", detail_args, trace))
        self.client.observe("get_warning_trace_and_role", detail_args, trace)
        source: (
            tuple[str, dict[str, object], dict[str, object]] | None
        ) = await self.prefetch_source()
        if source:
            packets.append(source)
        for index, (action, arguments, data) in enumerate(packets):
            self.attach_packet(reviewer, index, action, arguments, data)

    async def prefetch_source(
        self,
    ) -> tuple[str, dict[str, object], dict[str, object]] | None:
        location: dict[str, object] = self.warning.get("location", {})
        if not location.get("file_path") or not isinstance(
            location.get("line"), int
        ):
            return None
        arguments: dict[str, object] = {
            "project_id": self.scope["project_id"],
            "snapshot_id": location.get("snapshot_override")
            or self.scope["snapshot_id"],
            "file": location["file_path"],
            "line": location["line"],
            "before": 5,
            "after": 5,
        }
        self.client.snapshots.add(arguments["snapshot_id"])
        try:
            data: dict[str, object] = await self.read(
                "agent_source", arguments
            )
        except HistoryError as error:
            if error.auth_error or str(error).startswith(
                "History request failed"
            ):
                raise
            data = {"error": str(error)}
        return "agent_source", arguments, data

    def attach_packet(
        self,
        reviewer: Harness,
        index: int,
        action: str,
        arguments: dict[str, object],
        data: dict[str, object],
    ) -> None:
        text: str = compact(data)
        if len(text.encode()) > reviewer.max_tool_result_bytes:
            text = compact({"error": prompts.PREFETCH_TOO_LARGE})
        else:
            self.client.observe(action, arguments, data)
        reviewer.messages.extend(
            tool_exchange("batch-read-" + str(index), action, arguments, text)
        )
        self.on_event(
            "tool_prefetch",
            action=action,
            arguments=arguments,
            response=text,
        )
