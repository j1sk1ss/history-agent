from history_agent.inference.model import ChatError, Model
from history_agent.interfaces.cli import main, model_base, parse_args, run
from history_agent.runtime.agent import (
    Harness,
)
from history_agent.runtime.completion import complete_model
from history_agent.runtime.conversation import pretty_json_text
from history_agent.runtime.prompts import BATCH_HINT, SYSTEM
from history_agent.runtime.session import converse

__all__: list[str] = [
    "BATCH_HINT",
    "SYSTEM",
    "Harness",
    "complete_model",
    "main",
    "model_base",
    "parse_args",
    "run",
    "ChatError",
    "Model",
    "converse",
    "pretty_json_text",
]

if __name__ == "__main__":
    main()
