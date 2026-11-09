"""Build a stable, paginated warning queue for a review job."""

from collections.abc import Awaitable, Callable

from history_agent.history.schemas import COMPARE_ACTION
from history_agent.inference.model import ChatError
from history_agent.review.journal import Journal
from history_agent.review.storage.models import ReviewItem


class ReviewQueue:
    def __init__(
        self,
        read: Callable[[str, dict[str, object]], Awaitable[dict[str, object]]],
        on_event: Callable[..., None],
    ) -> None:
        self.read: Callable[
            [str, dict[str, object]], Awaitable[dict[str, object]]
        ] = read
        self.on_event: Callable[..., None] = on_event

    async def collect(self, job: Journal) -> None:
        job.clear_items()
        if job.get("comparison"):
            await self.collect_difference(job)
        else:
            scope: dict[str, str | int] = job.get("scope")
            ordinal: int = 0
            for checker in await self.checkers(job):
                ordinal = await self.scan(
                    job,
                    "agent_find_warnings",
                    {
                        **scope,
                        "checker_name": checker,
                        "limit": 100,
                    },
                    ordinal,
                    checker,
                )
        job.set("queue_complete", True)

    async def checkers(self, job: Journal) -> list[str]:
        checker: str | None = job.get("checker_name")
        if checker:
            return [checker]
        catalog: dict[str, object] = await self.read(
            "get_value_filter", {**job.get("scope"), "field": "warn_types"}
        )
        return sorted(
            {
                item["checker_name"]
                for group in catalog["checker_groups"]
                for item in group["checkers"]
            },
            key=str.casefold,
        )

    async def collect_difference(self, job: Journal) -> None:
        comparison: dict[str, object] = job.get("comparison")
        values: dict[str, object] = {
            **comparison["current"],
            **{
                "base_" + key: value
                for key, value in comparison["base"].items()
            },
            "subset": comparison["subset"],
            "limit": 100,
        }
        if job.get("checker_name"):
            values["checker_name"] = job.get("checker_name")
        await self.scan(job, COMPARE_ACTION, values, 0, None)

    async def scan(
        self, job: Journal, action: str, values: dict[str, object],
        ordinal: int, checker: str | None,
    ) -> int:
        offset: int = 0
        while True:
            page: dict[str, object] = await self.read(
                action, {**values, "offset": offset}
            )
            if action == COMPARE_ACTION:
                self.verify_comparison(job, page)
            warnings: list[dict[str, object]] = page.get("warnings")
            if not isinstance(warnings, list):
                raise ChatError("Invalid warning page")
            ordinal = self.store(job, warnings, ordinal, checker)
            label: str = checker or "diff: " + values["subset"]
            self.on_event(
                "batch_queue",
                job_id=job.id,
                total=ordinal,
                checker=label,
            )
            if not page.get("has_more"):
                return ordinal
            next_offset: int = page.get("next_offset")
            if (
                not warnings
                or not isinstance(next_offset, int)
                or next_offset <= offset
            ):
                raise ChatError("Warning pagination did not advance")
            offset = next_offset

    def store(
        self, job: Journal, warnings: list[dict[str, object]], ordinal: int,
        checker: str | None,
    ) -> int:
        items: list[ReviewItem] = []
        for warning in warnings:
            name: str = warning.get("checker_name", "")
            if not isinstance(name, str) or not name:
                raise ChatError("Comparison warning has no checker name")
            expected: str | None = checker or job.get("checker_name")
            if expected and name.casefold() != expected.casefold():
                raise ChatError("Warning page returned a different checker")
            items.append(
                ReviewItem(
                    warning_id=warning["warning_id"],
                    checker=name,
                    ordinal=ordinal,
                    warning=warning,
                )
            )
            ordinal += 1
        job.add_items(items)
        return ordinal

    def verify_comparison(self, job: Journal, page: dict[str, object]) -> None:
        comparison: dict[str, object] = job.get("comparison")
        expected: dict[str, object] = {**comparison, "scope": job.get("scope")}
        if any(
            page.get(key) != expected[key]
            for key in ("current", "base", "scope", "subset")
        ):
            raise ChatError(
                "Comparison page returned a different scope/subset"
            )
