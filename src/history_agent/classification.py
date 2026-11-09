STATUSES = {
    "TP": "Confirmed",
    "FP": "False Positive",
    "WF": "Won't fix",
    "UC": "Unclear",
    "UD": "Undecided",
}

EVIDENCE_REQUIRED = frozenset(("TP", "FP", "WF"))
PATTERN_CLASSES = frozenset(("TP", "FP", "WF"))

CORE_CRITERIA = (
    "TP: checker condition is true and relevant. FP: checker "
    "condition is false. "
    "WF: Technically is a correct work of the checker "
    "and it has found the defect, but in this context it's "
    "FP"
    "UC: evidence is insufficient. UD: undecided."
)

REVIEW_CRITERIA = (
    "TP: the checker-reported condition actually holds and is "
    "meaningful or actionable in this project. "
    "FP: the checker-reported condition does not hold; prove the "
    "relevant infeasible path, invariant, "
    "incorrect assumption or other reason the detection is wrong. "
    "WF: the checker-reported condition does hold, but is not "
    "useful to fix or report in this project "
    "because of verified code context, intentional behavior, test"
    " fixtures, generated code, "
    "accepted design trade-offs or project policy. WF does not "
    "mean the detector is wrong. "
    "Do not turn a technically correct but irrelevant warning into FP. "
    "Do not assume WF solely from a file name, low severity, "
    "absence from a later snapshot, "
    "or an unsupported claim that the code is intentional. "
    "For WF, explain both why the detection is technically valid "
    "and why it is non-actionable here. "
    "UC: insufficient documentation or evidence to decide; state "
    "what is missing. "
    "UD: leave undecided when requested. "
    "Establish checker semantics from its documentation before "
    "classifying, and support TP/FP/WF "
    "with actual source evidence and project-specific context."
)
