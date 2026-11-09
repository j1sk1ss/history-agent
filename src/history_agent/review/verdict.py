"""
Read a single verdict object without accepting ambiguous or nested
examples.
"""

from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator

from history_agent.inference.model import ChatError
from history_agent.review.comments import short_path


class VerdictFormatError(ChatError):
    pass


FINAL_TOOL: str = "submit_verdict"


def normalize_verdict(
    value: dict[str, object],
    observed: set[tuple[str, int, str]],
    scope: dict[str, str | int],
    warning_id: str,
    checker: str,
    statuses: dict[str, str],
) -> dict[str, object]:
    """
    Normalize representation only; never choose a verdict or fabricate a
    source address.
    """
    result: dict[str, object] = copy.deepcopy(value)
    aliases(result, "classification", ("verdict", "status"))
    aliases(result, "comment", ("reason", "rationale", "explanation"))
    aliases(result, "evidence", ("source_evidence", "locations"))
    if isinstance(result.get("classification"), str):
        names: dict[str, str] = {key.casefold(): key for key in statuses}
        names.update({name.casefold(): key for key, name in statuses.items()})
        name: str = result["classification"].strip()
        result["classification"] = names.get(name.casefold(), name)
    if isinstance(result.get("comment"), str):
        result["comment"] = result["comment"].strip()
    for key, expected in {
        **scope,
        "warning_id": warning_id,
        "checker_name": checker,
    }.items():
        if key in result:
            actual: object = result[key]
            matches: bool = (
                isinstance(actual, str)
                and actual.casefold() == checker.casefold()
                if key == "checker_name"
                else actual == expected
            )
            if not matches:
                raise ChatError(
                    "Verdict metadata does not match the assigned " + key
                )
            result.pop(key)
    normalize_evidence(result, observed)
    return result


def schema_issues(
    value: dict[str, object], validator: Draft202012Validator
) -> list[str]:
    issues: list[str] = []
    for error in validator.iter_errors(value):
        path: str = ".".join(map(str, error.absolute_path)) or "verdict"
        issues.append(path + ": " + error.message[:300])
        if len(issues) == 5:
            break
    if (
        not issues
        and isinstance(value.get("comment"), str)
        and (not value["comment"].strip())
    ):
        issues.append("comment: explanation must not be blank")
    return issues


def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def extract_verdict(raw: str) -> dict[str, object]:
    text: str = verdict_text(raw)
    decoder: json.JSONDecoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    candidates: list[dict[str, object]] = []
    start: int | None = None
    depth: int = 0
    quoted: bool
    escaped: bool
    quoted = escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start, depth = (index, 1)
                quoted = escaped = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                fragment: str = text[start:end]
                start = None
                try:
                    value: dict[str, object] = decoder.decode(fragment)
                except ValueError:
                    continue
                if (
                    isinstance(value, dict)
                    and {
                        "classification",
                        "comment",
                        "evidence",
                        "verdict",
                        "status",
                        "reason",
                        "rationale",
                        "explanation",
                        "source_evidence",
                        "locations",
                    }
                    & value.keys()
                ):
                    candidates.append(value)
    if len(candidates) > 1:
        raise ChatError(
            (
                "Batch response has multiple verdicts; no review "
                "was saved. The item remains resumable"
            )
        )
    if not candidates or start is not None:
        raise VerdictFormatError(
            ("Batch model did not return one complete JSON verdict")
        )
    return candidates[0]


def aliases(
    mapping: dict[str, object], target: str, alternatives: tuple[str, ...]
) -> None:
    for key in alternatives:
        if key in mapping:
            if target not in mapping:
                mapping[target] = mapping.pop(key)
            elif mapping[key] == mapping[target]:
                mapping.pop(key)


def normalize_evidence(
    result: dict[str, object], observed: set[tuple[str, int, str]]
) -> None:
    if isinstance(result.get("evidence"), dict):
        result["evidence"] = [result["evidence"]]
    if isinstance(result.get("evidence"), list):
        for address in result["evidence"]:
            if not isinstance(address, dict):
                continue
            aliases(address, "file", ("file_path", "path"))
            aliases(address, "line", ("line_number",))
            aliases(address, "snapshot_id", ("snapshot", "snapshotId"))
            line: str | int | None = address.get("line")
            if isinstance(line, str) and line.isascii() and line.isdigit():
                address["line"] = int(line)
            file: str | None = address.get("file")
            if isinstance(file, str) and isinstance(address.get("line"), int):
                candidates: set[tuple[str, str]] = {
                    (path, snapshot)
                    for path, number, snapshot in observed
                    if number == address["line"]
                    and (path == file or short_path(path) == file)
                    and (
                        not address.get("snapshot_id")
                        or address["snapshot_id"] == snapshot
                    )
                }
                if len(candidates) == 1:
                    address["file"], address["snapshot_id"] = next(
                        iter(candidates)
                    )
            if address.get("snapshot_id") in (None, "") and isinstance(
                address.get("line"), int
            ):
                snapshots: set[str] = {
                    snapshot
                    for file, line, snapshot in observed
                    if file == address.get("file") and line == address["line"]
                }
                if len(snapshots) == 1:
                    address["snapshot_id"] = next(iter(snapshots))


def verdict_text(raw: str) -> str:
    text: str = raw.strip()
    if text.startswith("<think>"):
        end: int = text.find("</think>")
        if end < 0:
            raise VerdictFormatError(
                ("Batch response contains unfinished reasoning, not a verdict")
            )
        reasoning_end: int = end + len("</think>")
        text = text[reasoning_end:].lstrip()
    return text
