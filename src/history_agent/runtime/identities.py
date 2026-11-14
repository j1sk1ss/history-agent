"""Session-local names for opaque server identities at the model boundary."""

import copy
import json
import re

from history_agent.inference.model import ChatError, compact
from history_agent.runtime.structured import json_fragments

NAMES = (
    "diana",
    "max",
    "chloe",
    "alex",
    "emma",
    "leo",
    "mia",
    "noah",
    "luna",
    "oliver",
    "ava",
    "oscar",
    "ella",
    "felix",
    "ruby",
    "hugo",
    "iris",
    "jack",
    "nora",
    "liam",
    "zoe",
    "milo",
    "alice",
    "theo",
    "bob",
    "lancaster",
    "b17",
    "altman",
    "sam",
)
INSTRUCTIONS = (
    "ID fields, snapshot_override, hash, mti and token_group use host names "
    "(diana, max, chloe). Copy returned names; never guess names or raw IDs. "
    "Resolve scope with history_current or host link results. HEAD is a "
    "snapshot selector. Names survive session reloads."
)


def identity_kind(key: str) -> str | None:
    if key == "selected_warning_id":
        return "warning"
    if key in ("snapshot_override", "snapshotId", "snapshot"):
        return "snapshot"
    if key in ("hash", "mti", "token_group"):
        return key
    if key.endswith("_id") and key != "tool_call_id":
        return key.removeprefix("base_").removesuffix("_id")
    return None


class IdentityError(ChatError):
    """A model supplied an unknown name or an identifier of another kind."""


class IdentityMap:
    def __init__(self) -> None:
        self.forward: dict[tuple[str, type, object], str] = {}
        self.reverse: dict[str, tuple[str, object]] = {}

    def encode_id(self, kind: str, value: object) -> object:
        if (
            value is None
            or value == ""
            or (kind == "snapshot" and value == "HEAD")
        ):
            return value
        if isinstance(value, list):
            return [self.encode_id(kind, entry) for entry in value]
        if type(value) not in (str, int):
            raise IdentityError("History returned an invalid identifier")
        key = (kind, type(value), value)
        if key not in self.forward:
            index = len(self.forward)
            suffix = (
                "" if index < len(NAMES) else f"_{index // len(NAMES) + 1}"
            )
            alias = NAMES[index % len(NAMES)] + suffix
            self.forward[key] = alias
            self.reverse[alias] = (kind, value)
        return self.forward[key]

    def decode_id(self, kind: str, value: object) -> object:
        if value is None or (kind == "snapshot" and value == "HEAD"):
            return value
        if isinstance(value, list):
            return [self.decode_id(kind, entry) for entry in value]
        entry = self.reverse.get(value) if isinstance(value, str) else None
        if not entry or entry[0] != kind:
            if kind == "link":
                raise IdentityError(
                    "Unknown link name. Copy link_id from the host's resolved "
                    "warnings_arguments; do not guess a link or use current "
                    "defaults instead."
                )
            raise IdentityError(
                f"Unknown {kind} name. Copy an existing name from the tool "
                "results or resolve scope with history_current; raw IDs "
                "and names belonging to another entity are rejected."
            )
        return entry[1]

    def encode(self, value: object, key: str = "") -> object:
        if key == "constraints" and isinstance(value, dict):
            return {
                name: "Copy a host name returned by the scope/read tools."
                + (
                    " HEAD is also allowed."
                    if identity_kind(name) == "snapshot"
                    else ""
                )
                if identity_kind(name)
                else entry
                for name, entry in value.items()
            }
        kind = identity_kind(key)
        if kind and not isinstance(value, dict):
            return self.encode_id(kind, value)
        if key in ("trace", "traces", "draft", "raw", "original_raw"):
            if isinstance(value, str):
                return json_fragments(value, self.encode)
        if key in ("error", "catalog_error") and isinstance(value, str):
            return self.redact_error(value)
        if key in ("validation_errors", "issues") and isinstance(value, list):
            return [
                self.redact_error(entry)
                if isinstance(entry, str)
                else self.encode(entry)
                for entry in value
            ]
        if key in ("branches", "streams", "branch_names", "stream_ids"):
            if isinstance(value, list):
                return [
                    self.encode_id("stream", item)
                    if isinstance(item, str)
                    else self.encode(item)
                    for item in value
                ]
        if isinstance(value, list):
            return [self.encode(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        if "properties" in value or "$defs" in value or "anyOf" in value:
            return self.schema(value)
        return {
            name: self.encode(entry, name) for name, entry in value.items()
        }

    def redact_error(self, value: str) -> str:
        try:
            return compact(self.encode(json.loads(value)))
        except ValueError:
            pass
        value = json_fragments(value, self.encode)
        for (_, _, raw), alias in sorted(
            self.forward.items(),
            key=lambda entry: -len(str(entry[0][2])),
        ):
            if str(raw) in {
                "project",
                "stream",
                "branch",
                "snapshot",
                "warning",
                "job",
                "hash",
                "mti",
                "token_group",
                "classification",
            }:
                continue
            value = re.sub(
                r"(?<![\w/.\\])" + re.escape(str(raw)) + r"(?![\w/\\]|\.\w)",
                alias,
                value,
            )
        return value

    def decode(self, value: object, key: str = "") -> object:
        kind = identity_kind(key)
        if kind and not isinstance(value, dict):
            return self.decode_id(kind, value)
        if isinstance(value, list):
            return [self.decode(entry) for entry in value]
        if isinstance(value, dict):
            return {
                name: self.decode(entry, name) for name, entry in value.items()
            }
        return value

    def schema(self, value: dict[str, object]) -> dict[str, object]:
        result = copy.deepcopy(value)
        for key, entry in result.items():
            if key == "properties" and isinstance(entry, dict):
                for name, prop in entry.items():
                    kind = identity_kind(name)
                    if kind and isinstance(prop, dict):
                        nullable = "null" in str(
                            prop.get("anyOf", prop.get("type"))
                        )
                        entry[name] = {
                            "type": ["string", "null"]
                            if nullable
                            else "string",
                            "description": f"{kind} name returned by the host "
                            "(e.g. diana); copy exactly, never a raw ID."
                            + (
                                " HEAD selects the latest snapshot."
                                if kind == "snapshot"
                                else ""
                            ),
                        }
                    else:
                        entry[name] = (
                            self.schema(prop)
                            if isinstance(prop, dict)
                            else prop
                        )
            elif isinstance(entry, dict):
                result[key] = self.schema(entry)
            elif isinstance(entry, list):
                result[key] = [
                    self.schema(item) if isinstance(item, dict) else item
                    for item in entry
                ]
        return result
