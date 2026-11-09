"""Build and validate command-line arguments."""

from __future__ import annotations

import argparse

from history_agent.configuration.defaults import (
    LIMIT_FIELDS,
    validate_defaults,
)
from history_agent.interfaces.options import (
    DISPLAY_OPTIONS,
    JOB_OPTIONS,
    PARSER_OPTIONS,
)


def parse_args(
    argv: list[str] | None = None,
    *,
    defaults: dict[str, str | bool | int | float | None] | None = None,
) -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Code review harness for local and hosted models"
    )
    for flags, options in PARSER_OPTIONS[0:18]:
        parser.add_argument(*flags, **options)
    display: argparse._MutuallyExclusiveGroup = (
        parser.add_mutually_exclusive_group()
    )
    for flags, options in DISPLAY_OPTIONS[0:3]:
        display.add_argument(*flags, **options)
    parser.set_defaults(ui=None, history_user=None, history_password=None)
    for flags, options in PARSER_OPTIONS[18:22]:
        parser.add_argument(*flags, **options)
    job: argparse._MutuallyExclusiveGroup = (
        parser.add_mutually_exclusive_group()
    )
    for flags, options in JOB_OPTIONS[0:3]:
        job.add_argument(*flags, **options)
    for flags, options in PARSER_OPTIONS[22:30]:
        parser.add_argument(*flags, **options)
    parser.set_defaults(**validate_defaults(defaults or {}))
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        validate_defaults({name: getattr(args, name) for name in LIMIT_FIELDS})
    except ValueError as error:
        parser.error(str(error))
    if args.overwrite_reviews and (not args.allow_write):
        parser.error("--overwrite-reviews requires --allow-write")
    if args.prompt is not None and (args.batch or args.diff or args.resume):
        parser.error(
            (
                "Use --batch-instructions for a batch job, or "
                "--prompt for a chat request"
            )
        )
    if not args.diff and any(
        (
            value is not None
            for value in (args.subset, args.base_project, args.base_branch)
        )
    ):
        parser.error(
            "--subset, --base-project and --base-branch require --diff"
        )
    if args.legacy_ui and args.ui is None:
        args.ui = True
    return args
