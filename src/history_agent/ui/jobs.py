"""A compact, scrollable launcher for unfinished review jobs."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static

from ..jobs import UnfinishedJob
from ..terminal import safe_text


class UnfinishedJobs(Vertical):
    def __init__(self) -> None:
        super().__init__(id="unfinished-jobs", disabled=True)

    def compose(self) -> ComposeResult:
        yield Static("Unfinished jobs", id="jobs-title")
        yield VerticalScroll(id="jobs-list")
        yield Static("", id="jobs-error", markup=False)

    async def show_jobs(self, jobs: list[UnfinishedJob]) -> None:
        listing: VerticalScroll = self.query_one("#jobs-list", VerticalScroll)
        await listing.remove_children()
        self.query_one("#jobs-error", Static).update("")
        self.query_one("#jobs-error").display = False
        rows: list[Horizontal] = []
        for job in jobs:
            scope: dict[str, str | int] = job.scope
            progress: str = f"{job.done}/{job.total}"
            if not job.queue_complete:
                progress += "+ · collecting warnings"
            mode: str = "write" if job.write else "read only"
            title: str = (
                f"{job.job_id[:8]} · {progress} · "
                f"project {scope['project_id']} · {scope['stream_id']}"
            )
            details: str = f"{job.model} · {mode} · {scope['snapshot_id']}"
            description: Static = Static(
                Text(
                    safe_text(title + "\n" + details),
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                classes="job-description",
            )
            description.tooltip = safe_text(
                f"{job.job_id}\n{title}\n{details}"
            )
            rows.append(
                Horizontal(
                    description,
                    Button(
                        "Continue",
                        id="resume-" + job.job_id,
                        classes="resume-job",
                    ),
                    Button(
                        "Delete",
                        id="delete-" + job.job_id,
                        classes="delete-job",
                    ),
                    classes="job-row",
                )
            )
        if rows:
            await listing.mount(*rows)

    def show_error(self, message: str) -> None:
        self.query_one("#jobs-error", Static).update(safe_text(message))
        self.query_one("#jobs-error").display = True
