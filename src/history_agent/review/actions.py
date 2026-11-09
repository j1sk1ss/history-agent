"""Review action definitions instantiated from typed request models."""

from history_agent.contracts import ActionDefinition
from history_agent.review.constants import BATCH_ACTION, DIFF_ACTION
from history_agent.review.descriptions import (
    BATCH_DESCRIPTION,
    DIFF_DESCRIPTION,
)
from history_agent.review.requests import BatchCommand, DifferenceCommand

BATCH_REVIEW: ActionDefinition = ActionDefinition(
    name=BATCH_ACTION,
    description=BATCH_DESCRIPTION,
    request_model=BatchCommand,
    read_only=False,
)
DIFFERENCE_REVIEW: ActionDefinition = ActionDefinition(
    name=DIFF_ACTION,
    description=DIFF_DESCRIPTION,
    request_model=DifferenceCommand,
    read_only=False,
)
