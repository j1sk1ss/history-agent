from __future__ import annotations

import json

from history_agent.ui.terminal import safe_text


def pretty(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return safe_text(value)
    return safe_text(json.dumps(value, ensure_ascii=False, indent=2))


def tool_preview(response: str) -> str:
    try:
        value: object = json.loads(response)
    except (ValueError, TypeError):
        return safe_text(response)[:300]
    if isinstance(value, dict):
        if isinstance(value.get("lines"), list):
            return "\n".join(
                f"{line.get('number', '')}: {line.get('text', '')}"
                for line in value["lines"][:3]
                if isinstance(line, dict)
            )[:360]
        if isinstance(value.get("warnings"), list):
            names: str = ", ".join(
                str(w.get("checker_name", w.get("warning_id", "")))
                for w in value["warnings"][:3]
                if isinstance(w, dict)
            )
            total: int = value.get("total", len(value["warnings"]))
            return f"Warnings: {total} · {names}"
        doc: dict[str, object] = value.get("documentation")
        if isinstance(doc, dict):
            return (
                str(value.get("checker_name", ""))
                + " · "
                + str(doc.get("text", doc.get("status", "")))
            )[:360]
        if "text" in value:
            return str(value["text"])[:360]
        return pretty(value)[:300]
    return pretty(value)[:300]
