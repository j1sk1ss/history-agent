"""Translate the legacy action catalog into validation schemas."""

from __future__ import annotations


def legacy_action(name: str, metadata: dict[str, object]) -> dict[str, object]:
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
    return {
        "name": name,
        "description": metadata.get("desc", ""),
        "read_only": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }
