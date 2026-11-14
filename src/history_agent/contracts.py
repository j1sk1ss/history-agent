"""Typed models and action descriptions shared by application modules."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True
    )


class ActionMetadata(StrictModel):
    name: str
    description: str
    read_only: bool
    input_schema: dict[str, object]
    session_required: bool | None = None
    constraints: dict[str, str] | None = None


class ActionDefinition(StrictModel):
    name: str
    description: str
    request_model: type[BaseModel]
    read_only: bool = True

    def metadata(self) -> ActionMetadata:
        return ActionMetadata(
            name=self.name,
            description=self.description,
            read_only=self.read_only,
            input_schema=self.request_model.model_json_schema(),
        )
