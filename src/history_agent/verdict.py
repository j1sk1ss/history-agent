"""
Read a single verdict object without accepting ambiguous or nested
examples.
"""

import copy
import json

from .comments import short_path
from .model import ChatError


class VerdictFormatError(ChatError):
    pass


FINAL_TOOL = "submit_verdict"


def normalize_verdict(value, observed, scope, warning_id, checker, statuses):
    """
    Normalize representation only; never choose a verdict or fabricate a
    source address.
    """
    result = copy.deepcopy(value)

    def aliases(mapping, target, alternatives):
        for key in alternatives:
            if key in mapping:
                if target not in mapping:
                    mapping[target] = mapping.pop(key)
                elif mapping[key] == mapping[target]:
                    mapping.pop(key)

    aliases(result, "classification", ("verdict", "status"))
    aliases(result, "comment", ("reason", "rationale", "explanation"))
    aliases(result, "evidence", ("source_evidence", "locations"))
    if isinstance(result.get("classification"), str):
        names = {key.casefold(): key for key in statuses}
        names.update({name.casefold(): key for key, name in statuses.items()})
        name = result["classification"].strip()
        result["classification"] = names.get(name.casefold(), name)
    if isinstance(result.get("comment"), str):
        result["comment"] = result["comment"].strip()
    for key, expected in {
        **scope,
        "warning_id": warning_id,
        "checker_name": checker,
    }.items():
        if key in result:
            actual = result[key]
            matches = (
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
    if isinstance(result.get("evidence"), dict):
        result["evidence"] = [result["evidence"]]
    if isinstance(result.get("evidence"), list):
        for address in result["evidence"]:
            if not isinstance(address, dict):
                continue
            aliases(address, "file", ("file_path", "path"))
            aliases(address, "line", ("line_number",))
            aliases(address, "snapshot_id", ("snapshot", "snapshotId"))
            line = address.get("line")
            if isinstance(line, str) and line.isascii() and line.isdigit():
                address["line"] = int(line)
            file = address.get("file")
            if isinstance(file, str) and isinstance(address.get("line"), int):
                candidates = {
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
                snapshots = {
                    snapshot
                    for file, line, snapshot in observed
                    if file == address.get("file") and line == address["line"]
                }
                if len(snapshots) == 1:
                    address["snapshot_id"] = next(iter(snapshots))
    return result


def schema_issues(value, validator):
    issues = []
    for error in validator.iter_errors(value):
        path = ".".join(map(str, error.absolute_path)) or "verdict"
        issues.append(path + ": " + error.message[:300])
        if len(issues) == 5:
            break
    if (
        not issues
        and isinstance(value.get("comment"), str)
        and not value["comment"].strip()
    ):
        issues.append("comment: explanation must not be blank")
    return issues


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def extract_verdict(raw):
    text = raw.strip()
    # Some models emit reasoning tags in content instead of reasoning_content.
    if text.startswith("<think>"):
        end = text.find("</think>")
        if end < 0:
            raise VerdictFormatError(
                "Batch response contains unfinished reasoning, not a verdict"
            )
        reasoning_end = end + len("</think>")
        text = text[reasoning_end:].lstrip()
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    candidates = []
    start = None
    depth = 0
    quoted = escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start, depth = index, 1
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
                fragment = text[start:end]
                start = None
                try:
                    value = decoder.decode(fragment)
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
            "Batch response has multiple verdicts; no review was saved. "
            "The item remains resumable"
        )
    if not candidates or start is not None:
        raise VerdictFormatError(
            "Batch model did not return one complete JSON verdict"
        )
    return candidates[0]
