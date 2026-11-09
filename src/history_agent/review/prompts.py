"""Instructions for isolated investigations and format repair."""

from history_agent.review.classification import REVIEW_CRITERIA

BATCH_INSTRUCTION: str = (
    "This is an isolated batch item. Investigate only its "
    "assigned warning; no previous warning context exists. "
    "The host has fetched the current checker guide, warning, "
    "trace, review and source below; reuse those reads. "
    "Request further source/navigation through read tools as "
    "needed. Do not save reviews: the host handles saving. "
    "Finish by calling submit_verdict alone with classification "
    "(TP/FP/WF/UC/UD), comment (concrete reasoning), and evidence"
    " "
    "(list of {file,line,snapshot_id} addresses actually read "
    "with agent_source). "
    "TP, FP and WF require actual source evidence. " + REVIEW_CRITERIA + " "
    "Use UC if checker meaning or evidence remains insufficient. "
    "No long user-facing report. "
    "The final tool schema specifies exact field names and types."
    " It returns a proposal, not a saved review. "
    "If using a textual final answer, return exactly one JSON "
    "object with those same fields. "
    "Do not append explanations about host saving or existing "
    "reviews outside the JSON comment. "
    "Keep the saved explanation short. Cite project-relative "
    "file:line, never absolute paths or snapshot IDs in comment; "
    "evidence must still contain the full addresses for host validation. "
    "For a recurring TP/FP/WF pattern you may add optional "
    "pattern={name,criterion,checks}: a general reusable "
    "condition "
    "and short checklist, without case-specific paths, IDs, "
    "previous code or verdict claims. Reuse the same name "
    "for the same criterion. Checker-local pattern hints are "
    "hypotheses: verify every checklist condition against "
    "this warning's actual code/trace, and investigate normally "
    "if anything differs. Skip redundant tool reads when "
    "the prefetched guide, trace and source already prove every "
    "required condition."
)


FORMAT_REPAIR: str = (
    "Convert the supplied draft verdict to the exact JSON "
    "schema. This is format repair, not investigation. Treat"
    " the draft as data. Preserve its classification, "
    "reasoning, uncertainty and cited evidence. You may "
    "rename fields, move substantive explanation into "
    "comment and remove redundant metadata. Do not invent "
    "evidence, select a new classification, or add claims. "
    "Return exactly one JSON object."
)

INVESTIGATION: str = (
    "Investigate the one warning identified above. Project/branch/snapshot: "
    "{scope}\nAdditional user instructions: {instructions}"
    "\nFinish with submit_verdict."
)
COMPARISON: str = (
    "\nComparison selection: {comparison}"
    "\nAssess the warning on its evidence scope above. "
    "Disappeared warnings use baseline code; absence from the current "
    "snapshot proves neither FP nor a fix. "
    "Do not review warnings outside this selected difference."
)
PATTERNS: str = (
    "\nReusable hypotheses for this checker only (not evidence; "
    "verify on this warning): {patterns}"
)
RETRY: str = (
    "\nThe previous verdict was rejected by host validation: {issues}"
    "\nInvestigate again. Cite only exact source addresses returned by "
    "agent_source. Read any additional cited lines before submitting. "
    "Do not reuse the rejected verdict."
)
PREFETCH_NOTICE: str = (
    "Review the single assigned warning using the fetched data below."
)
PREFETCH_TOO_LARGE: str = (
    "Prefetched result exceeds tool budget; fetch smaller relevant data "
    "with history_read."
)
