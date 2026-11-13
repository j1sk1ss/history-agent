"""Render the classifier's question and source evidence as readable text."""

import json

from history_agent.prompts import Prompts


def classifier_prompt(state: str) -> tuple[str, str]:
    instructions = Prompts.get("system_one.instructions")
    try:
        context = json.loads(state)
    except (TypeError, ValueError):
        return state, instructions
    if not isinstance(context, dict) or not isinstance(
        context.get("code"), str
    ):
        return state, instructions

    warning = context.get("warning_description") or "Warning not specified."
    documentation = context.get("tp_conditions") or (
        "Checker documentation is unavailable; do not infer its conditions."
    )
    question = "\n\n".join((
        instructions,
        "Reported warning:\n" + warning,
        "Checker documentation from the history guide:\n" + documentation,
    ))
    sections = ["Reported warning:\n" + warning]
    environment = context.get("environment")
    if environment:
        sections.append("Environment and assumptions:\n" + environment)
    sections.append("Initial source code:\n" + context["code"])
    trace = context.get("trace", [])
    if not trace:
        sections.append("Trace: no trace points are available.")
    for index, point in enumerate(trace, 1):
        title = f"Trace point {index}"
        if point.get("file") and point.get("line"):
            title += f" — {point['file']}:{point['line']}"
        parts = [title]
        if point.get("message"):
            parts.append("Event: " + point["message"])
        lines = point.get("code", [])
        if lines:
            parts.append(
                "Source context: 5 lines before and 5 lines after the trace "
                "line (file boundaries may shorten the window). "
                "The '>' marker identifies the trace line."
            )
            parts.extend(
                f"{'>' if line['number'] == point.get('line') else ' '} "
                f"{line['number']:5d} | {line['text']}"
                for line in lines
            )
        elif point.get("file") or point.get("source_unavailable"):
            parts.append("Source context unavailable for this trace point.")
        sections.append("\n".join(parts))
    return "\n\n".join(sections), question
