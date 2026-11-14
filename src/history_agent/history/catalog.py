"""Translate legacy and extension catalogs into validation schemas."""

from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from history_agent.contracts import ActionMetadata
from history_agent.history.constants import READ_ACTIONS
from history_agent.history.settings import HistoryError


def normalize_schema(value: object) -> object:
    """Translate the history server's enum dialect into JSON Schema."""
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_schema(item) for key, item in value.items()}
    if result.get("type") == "choose":
        choices = result.get("enum")
        if (
            not isinstance(choices, list)
            or not choices
            or not all(isinstance(item, str) for item in choices)
        ):
            raise ValueError("Expected nonempty string choices")
        result["type"] = "string"
    return result


def extension_action(name: str, metadata: dict[str, object]) -> ActionMetadata:
    try:
        schema: dict[str, object] = normalize_schema(metadata["input_schema"])
        if not isinstance(schema, dict):
            raise ValueError("Expected an object input schema")
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object" or not isinstance(
            schema.get("properties"), dict
        ):
            raise ValueError("Expected an object input schema")
        schema["additionalProperties"] = False
        return ActionMetadata(
            name=name,
            description=metadata["description"],
            input_schema=schema,
            read_only=name in READ_ACTIONS,
            session_required=metadata.get("session_required"),
            constraints=metadata.get("constraints"),
        )
    except (KeyError, TypeError, ValueError, SchemaError):
        raise HistoryError(
            "History returned invalid extension metadata for " + name
        ) from None


def legacy_action(name: str, metadata: dict[str, object]) -> ActionMetadata:
    properties: dict[str, dict[str, object]]
    required: list[str]
    properties, required = ({}, [])
    for key, parameter in metadata.get("params", {}).items():
        if name == "get_tokens" and key == "path":
            key: str = "file"
        prop: dict[str, object] = {
            "type": "string",
            "description": parameter.get("desc", ""),
            "x-http-parameter-location": parameter.get("type", "get"),
        }
        if key in {
            "project_id",
            "project_group_id",
            "start_line",
            "end_line",
            "number",
            "count",
        }:
            prop["type"] = "integer"
            prop["minimum"] = (
                0 if key in {"project_id", "project_group_id"} else 1
            )
        if key == "from" and name == "get_warnings":
            prop.update(type="integer", minimum=0)
        if key == "format":
            prop["enum"] = ["json"]
        if key == "extended":
            prop["type"] = "boolean"
        properties[key] = prop
        if parameter.get("is_req"):
            required.append(key)
    for count_key in ("count", "number"):
        if count_key in properties:
            properties[count_key].update(default=20, maximum=100)
    if name == "get_warnings" and "count" not in properties:
        properties["count"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
            "x-http-parameter-location": "post",
        }
    return ActionMetadata(
        name=name,
        description=metadata.get("desc", ""),
        read_only=True,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )
