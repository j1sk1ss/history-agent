from __future__ import annotations

import asyncio
import json
import shlex
import sys
from argparse import Namespace
from collections.abc import Callable, Coroutine
from functools import partial

from mcp import Client
from mcp.types import Tool

from history_agent.history.client import HistoryClient, HistoryError
from history_agent.history.links import URL_ACTION, find_history_urls
from history_agent.inference.model import ChatError, Model, compact
from history_agent.review.batch import ItemClient
from history_agent.runtime.agent import BATCH_HINT, Harness
from history_agent.ui.bridge import UIAdapter
from history_agent.ui.terminal import TerminalUI


def pretty_json_text(value: str) -> str:
    """
    Pretty-print complete JSON on display; keep compact JSON inside model
    context.
    """
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return value


async def converse(
    client: Client, model: Model, args: Namespace, history: HistoryClient, *,
    ui_override: TerminalUI | UIAdapter | None = None,
) -> None:
    from history_agent.review.batch import BatchRouter, BatchRunner, Journal
    from history_agent.runtime.commands import HELP, install_completion

    use_ui: bool = (
        args.ui
        if args.ui is not None
        else sys.stdin.isatty() and sys.stdout.isatty()
    )
    ui: TerminalUI | UIAdapter | None = ui_override
    if use_ui and ui is None:
        ui = TerminalUI(
            model.name, allow_write=args.allow_write, show_thoughts=True
        )
    tools: list[Tool] = (await client.list_tools()).tools

    def make_reviewer(item_client: ItemClient) -> Harness:
        return Harness(
            item_client,
            model,
            tools,
            allow_write=False,
            max_steps=args.max_steps,
            max_context_bytes=args.max_context_bytes,
            max_tool_result_bytes=args.max_tool_result_bytes,
            stats=args.stats,
            on_event=ui.event if ui else None,
        )

    def batch_event(kind: str, **data: object) -> None:
        if ui:
            ui.event(kind, **data)
        elif kind in ("batch_started", "batch_progress"):
            if kind == "batch_started":
                print("batch job:", data["job_id"], file=sys.stderr)
            else:
                print(
                    f"batch {data['done']}/{data['total']}: "
                    f"{data['checker']} {data['warning_id']}",
                    file=sys.stderr,
                )

    runner: BatchRunner = BatchRunner(
        history,
        client,
        make_reviewer,
        args.batch_state_dir,
        {"base": model.base, "model": model.name},
        allow_write=args.allow_write,
        allow_overwrite=args.overwrite_reviews,
        on_event=batch_event,
        reuse_patterns=not args.no_patterns,
    )
    router: BatchRouter = BatchRouter(client, runner)
    harness: Harness = Harness(
        router,
        model,
        tools,
        allow_write=args.allow_write,
        max_steps=args.max_steps,
        max_context_bytes=args.max_context_bytes,
        max_tool_result_bytes=args.max_tool_result_bytes,
        stats=args.stats,
        on_event=ui.event if ui else None,
    )
    harness.messages[0]["content"] += BATCH_HINT
    retry: Callable[[], Coroutine[None, None, None]] | None = None

    async def perform(operation: Coroutine[None, None, None]) -> None:
        if ui and getattr(ui, "async_prompt", False):
            return await ui.execute(operation)
        return await operation

    def retain_summary(summary: dict[str, object]) -> None:
        harness.reset()
        harness.messages[0]["content"] += BATCH_HINT

        small: dict[str, object] = {
            key: summary[key]
            for key in (
                "job_id",
                "scope",
                "write",
                "total",
                "states",
                "classifications",
            )
        }
        if summary.get("comparison"):
            small["comparison"] = summary["comparison"]
        harness.messages.append(
            {
                "role": "assistant",
                "content": "Previous batch summary: " + compact(small),
            }
        )
        router.completed = None

    async def respond(text: str) -> None:
        nonlocal retry
        retry = partial(respond, text)
        runner.last_job_id = None
        router.user_request = text
        if ui:
            ui.begin(text)
        result: str | None = None
        try:
            prefetched: list[
                tuple[str, dict[str, object], dict[str, object]]
            ] = []
            urls: list[str] = find_history_urls(text)
            if len(urls) > 4:
                raise ChatError("Send at most four history links per request")
            for url in urls:
                try:
                    resolved: dict[str, object] = await asyncio.to_thread(
                        history.execute, URL_ACTION, {"url": url}
                    )
                except HistoryError as error:
                    raise ChatError(str(error)) from None
                prefetched.append((URL_ACTION, {"url": url}, resolved))
                if ui:
                    ui.event(
                        "history_link",
                        kind_name=resolved["kind"],
                        scope=resolved["scope"],
                        subset=resolved.get("subset"),
                    )
            result = await harness.ask(text, prefetched=prefetched)
            if not ui:
                print(pretty_json_text(result))
            if router.completed:
                retain_summary(router.completed)
        finally:
            if runner.last_job_id:
                retry = partial(
                    run_batch, {"job_id": runner.last_job_id}, resume=True
                )
            if ui:
                ui.finish(result)

    async def run_batch(
        arguments: dict[str, object], resume: bool = False,
    ) -> None:
        nonlocal retry
        retry = partial(run_batch, dict(arguments), resume=resume)
        runner.last_job_id = None
        if ui:
            ui.begin("Resume review job" if resume else "Review all warnings")
        summary: dict[str, object] | None = None
        try:
            write: bool = args.allow_write
            if resume:
                journal: Journal = Journal(
                    args.batch_state_dir, arguments["job_id"]
                )
                try:
                    write = write or journal.get("write")
                finally:
                    journal.db.close()
            summary = await runner.run(arguments, write=write)
            retain_summary(summary)
            text: str = compact(
                {
                    key: summary[key]
                    for key in ("job_id", "total", "states", "classifications")
                }
            )
            if summary.get("comparison"):
                text = compact(
                    {**json.loads(text), "comparison": summary["comparison"]}
                )
            if ui:
                ui.event("batch_summary", text=text)
            else:
                print(pretty_json_text(text))
        except BaseException as error:
            if ui:
                ui.event(
                    "failure",
                    message=str(error)
                    if isinstance(error, (ChatError, HistoryError))
                    else "Batch interrupted; use its checkpoint to resume "
                    "and inspect "
                    "any pending write.",
                )
            raise
        finally:
            if runner.last_job_id:
                retry = partial(
                    run_batch, {"job_id": runner.last_job_id}, resume=True
                )
            if ui:
                ui.finish()

    async def start(operation: Coroutine[None, None, None]) -> None:
        try:
            await perform(operation)
        except (ChatError, HistoryError, ValueError, OSError) as error:
            if not (ui and getattr(ui, "async_prompt", False)):
                raise
            ui.error_message(str(error))

    if ui:
        ui.welcome(harness.context_bytes())
    if args.batch or args.diff or args.resume:
        if args.resume:
            await start(run_batch({"job_id": args.resume}, resume=True))
        else:
            selected: list[str] = args.diff or args.batch
            try:
                project: int = int(selected[0])
            except ValueError:
                raise ChatError(
                    "Batch project ID must be an integer"
                ) from None
            values: dict[str, object] = {
                "project_id": project,
                "stream_id": selected[1],
                "snapshot_id": selected[2],
                "instructions": args.batch_instructions,
                "overwrite": args.overwrite_reviews,
            }
            if args.diff:
                values.update(
                    base_snapshot_id=selected[3],
                    subset=args.subset or "appeared",
                )
                if args.base_project is not None:
                    values["base_project_id"] = args.base_project
                if args.base_branch is not None:
                    values["base_stream_id"] = args.base_branch
            await start(run_batch(values))
        if not (ui and getattr(ui, "async_prompt", False)):
            return
    elif args.prompt is not None:
        await start(respond(args.prompt))
        if not (ui and getattr(ui, "async_prompt", False)):
            return
    if sys.stdin.isatty() and not (ui and getattr(ui, "async_prompt", False)):
        install_completion(args.batch_state_dir)
    if not ui:
        write_mode: str = (
            "review writes enabled" if args.allow_write else "read-only"
        )
        print(
            f"history-chat: {model.name}; {write_mode}; "
            f"initial context={harness.context_bytes()} bytes; "
            "/help, Tab completes commands",
            file=sys.stderr,
        )
    while True:
        try:
            text: str = (
                await ui.prompt_async()
                if getattr(ui, "async_prompt", False)
                else ui.prompt()
                if ui
                else input("history> ")
            )
        except EOFError:
            return
        text = text.strip()
        if text in ("/quit", "/quite"):
            return
        if text == "/help":
            if ui:
                ui.console.print(HELP, markup=False)
            else:
                print(HELP)
            continue
        if text == "/reset":
            if runner.last_summary:
                retain_summary(runner.last_summary)
            else:
                harness.reset()
                harness.messages[0]["content"] += BATCH_HINT
            if ui:
                ui.reset()
            else:
                print(
                    "Fresh context. Review history is unchanged.",
                    file=sys.stderr,
                )
            continue
        if not text.strip():
            continue
        try:
            if text == "/reload":
                if retry is None:
                    raise ChatError("No request to reload yet")
                harness.reset()
                harness.messages[0]["content"] += BATCH_HINT
                router.completed = None
                await perform(retry())
                continue
            if text.split()[0] in ("/batch", "/diff", "/resume"):
                parts: list[str] = shlex.split(text)
                if parts[0] == "/resume" and len(parts) == 2:
                    await perform(run_batch({"job_id": parts[1]}, resume=True))
                elif parts[0] == "/batch" and len(parts) == 1:
                    default_scope: dict[str, object] = await asyncio.to_thread(
                        history.execute, "current", {}
                    )
                    await perform(
                        run_batch(
                            {
                                **default_scope["scope"],
                                "instructions": args.batch_instructions,
                                "overwrite": args.overwrite_reviews,
                            }
                        )
                    )
                elif parts[0] == "/batch" and len(parts) in (4, 5):
                    values = {
                        "project_id": int(parts[1]),
                        "stream_id": parts[2],
                        "snapshot_id": parts[3],
                        "instructions": args.batch_instructions,
                        "overwrite": args.overwrite_reviews,
                    }
                    if len(parts) == 5:
                        values["checker_name"] = parts[4]
                    await perform(run_batch(values))
                elif parts[0] == "/diff" and len(parts) in (5, 6, 7):
                    values = {
                        "project_id": int(parts[1]),
                        "stream_id": parts[2],
                        "snapshot_id": parts[3],
                        "base_snapshot_id": parts[4],
                        "subset": parts[5] if len(parts) > 5 else "appeared",
                        "instructions": args.batch_instructions,
                        "overwrite": args.overwrite_reviews,
                    }
                    if len(parts) == 7:
                        values["checker_name"] = parts[6]
                    await perform(run_batch(values))
                else:
                    raise ChatError(
                        "Use /batch [PROJECT BRANCH SNAPSHOT [CHECKER]], "
                        "/diff PROJECT BRANCH CURRENT BASE "
                        "[appeared|disappeared|both] [CHECKER], "
                        "or /resume JOB_ID"
                    )
                continue
            if text.startswith("/"):
                raise ChatError(
                    "Unknown command. Use /help or Tab to see "
                    "available commands."
                )
            await perform(respond(text))
        except (ChatError, HistoryError, ValueError, OSError) as error:
            if ui:
                ui.error_message(str(error))
            else:
                print(str(error), file=sys.stderr)
