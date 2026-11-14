"""Local actions with input schemas generated from request models."""

from pydantic import BaseModel

from history_agent.contracts import ActionDefinition
from history_agent.history.constants import (
    COMPARE_ACTION,
    CURRENT_ACTION,
    DOC_ACTION,
    LINK_WARNINGS_ACTION,
    URL_ACTION,
)
from history_agent.history.descriptions import (
    COMPARE_DESCRIPTION,
    CURRENT_DESCRIPTION,
    DOC_DESCRIPTION,
    LINK_WARNINGS_DESCRIPTION,
    URL_DESCRIPTION,
)
from history_agent.history.requests import (
    CheckerDocumentationRequest,
    CompareWarningsRequest,
    CurrentRequest,
    LinkWarningsRequest,
    ResolveUrlRequest,
)

CURRENT: ActionDefinition = ActionDefinition(
    name=CURRENT_ACTION,
    description=CURRENT_DESCRIPTION,
    request_model=CurrentRequest,
)
RESOLVE_URL: ActionDefinition = ActionDefinition(
    name=URL_ACTION,
    description=URL_DESCRIPTION,
    request_model=ResolveUrlRequest,
)
LINK_WARNINGS: ActionDefinition = ActionDefinition(
    name=LINK_WARNINGS_ACTION,
    description=LINK_WARNINGS_DESCRIPTION,
    request_model=LinkWarningsRequest,
)
CHECKER_DOCUMENTATION: ActionDefinition = ActionDefinition(
    name=DOC_ACTION,
    description=DOC_DESCRIPTION,
    request_model=CheckerDocumentationRequest,
)
COMPARE_WARNINGS: ActionDefinition = ActionDefinition(
    name=COMPARE_ACTION,
    description=COMPARE_DESCRIPTION,
    request_model=CompareWarningsRequest,
)
LOCAL_ACTIONS: tuple[ActionDefinition, ...] = (
    CURRENT,
    RESOLVE_URL,
    LINK_WARNINGS,
    CHECKER_DOCUMENTATION,
)
LOCAL_REQUESTS: dict[str, type[BaseModel]] = {
    action.name: action.request_model
    for action in (*LOCAL_ACTIONS, COMPARE_WARNINGS)
}
