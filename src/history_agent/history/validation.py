"""Validate local models and schemas discovered from a history server."""

import copy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ValidationError

from history_agent.history.settings import HistoryError


def validate_arguments(
    arguments: dict[str, object], schema: dict[str, object],
    request_model: type[BaseModel] | None = None,
) -> dict[str, object]:
    if request_model is not None:
        try:
            return request_model.model_validate(arguments).model_dump(
                exclude_none=True
            )
        except ValidationError as error:
            path: str = ".".join(map(str, error.errors()[0]["loc"]))
            raise HistoryError(
                f"Invalid {path or 'arguments'}; see history_discover"
            ) from None
    errors: list[SchemaValidationError] = sorted(
        Draft202012Validator(schema).iter_errors(arguments),
        key=lambda error: str(error.path),
    )
    if errors:
        first: SchemaValidationError = errors[0]
        path = ".".join(map(str, first.path)) or "arguments"
        raise HistoryError(
            f"Invalid {path}: failed {first.validator} validation; "
            "see history_discover"
        )
    values: dict[str, object] = copy.deepcopy(arguments)
    for key, prop in schema["properties"].items():
        if key not in values and "default" in prop:
            values[key] = prop["default"]
    return values
