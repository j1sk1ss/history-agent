STATUSES: dict[str, str] = {
    "TP": "Confirmed",
    "FP": "False Positive",
    "WF": "Won't fix",
    "UC": "Unclear",
    "UD": "Undecided",
}

EVIDENCE_REQUIRED: frozenset[str] = frozenset(("TP", "FP", "WF"))
PATTERN_CLASSES: frozenset[str] = frozenset(("TP", "FP", "WF"))


def __getattr__(name: str) -> str:
    from history_agent.prompts import Prompts

    keys = {
        "CORE_CRITERIA": "review.core_criteria",
        "REVIEW_CRITERIA": "review.criteria",
    }
    if name not in keys:
        raise AttributeError(name)
    return Prompts.get(keys[name])
