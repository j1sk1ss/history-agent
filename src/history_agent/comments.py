"""
Compact human-facing source references; full evidence stays in the
checkpoint.
"""

import re


def short_path(path):
    path = str(path).replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    if not path.startswith("/") and not re.match(r"^[A-Za-z]:/", path):
        return path
    for index, part in enumerate(parts[:-1]):
        if part in (
            "src",
            "include",
            "lib",
            "tests",
            "test",
            "source",
            "sources",
            "app",
            "packages",
        ):
            return "/".join(parts[index:])
    return "/".join(parts[-2:])


def review_comment(text, evidence=(), snapshots=()):
    text = text.strip()
    for address in sorted(
        evidence, key=lambda value: len(value["file"]), reverse=True
    ):
        text = text.replace(address["file"], short_path(address["file"]))
    # Also handle source references written directly in a model's explanation.
    text = re.sub(
        r'(?<![:/\w])(?:/[\w.\-+@]+)+\.[A-Za-z0-9_]{1,12}(?=[:\s`"\),;]|$)',
        lambda match: short_path(match.group()),
        text,
    )
    for snapshot in set(snapshots) | {
        address["snapshot_id"] for address in evidence
    }:
        if not snapshot:
            continue
        token = re.escape(snapshot)
        text = re.sub(
            r"\s*\[(?:snapshot|снимок)\s*[:=]?\s*" + token + r"\]",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"\s*\((?:snapshot|снимок)\s*[:=]?\s*" + token + r"\)",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"\b(?:snapshot_id|snapshot|снимок)\s*[:=]?\s*" + token + r"\b",
            "",
            text,
            flags=re.I,
        )
        if len(snapshot) >= 16:
            text = re.sub(r"(?<![\w])" + token + r"(?![\w])", "", text)
    references = list(
        dict.fromkeys(f"{short_path(a['file'])}:{a['line']}" for a in evidence)
    )
    missing = [reference for reference in references if reference not in text]
    if missing:
        text = text.rstrip() + "\n" + "; ".join(missing)
    return text.strip()
