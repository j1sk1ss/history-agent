"""Run the cheap classifier before spending any investigator model calls."""

import json

from history_agent.inference.model import compact
from history_agent.prompts import Prompts
from history_agent.review.system_one import SystemOneClient
from history_agent.review.system_one_trace import history_reads
from history_agent.review.timing import ReviewTimer
from history_agent.runtime.messages import tool_exchange


class ReviewPreflight:
    @staticmethod
    async def run(reviewer, item, instructions: str = "") -> str | None:
        transport = reviewer.client
        if not isinstance(transport, SystemOneClient) or not (
            transport.system_one and transport.system_one.available
        ):
            return None
        sources = [
            (arguments, data)
            for action, arguments, data in history_reads(reviewer.messages)
            if action == "agent_source" and data.get("lines")
        ]
        if not sources:
            return None
        code = "\n".join(
            line["text"]
            for _, data in sources
            for line in data["lines"]
            if isinstance(line.get("text"), str)
        )
        if not code.strip():
            return None
        response = await transport.call_tool(
            "ask_system_one",
            {
                "code": code,
                "environment": instructions,
            },
        )
        result = json.loads(response.content[0].text)
        reviewer.messages.extend(
            tool_exchange(
                "host-system-one",
                "host_system_one",
                {},
                compact(result),
            )
        )
        reviewer.messages[0]["content"] += " " + Prompts.get(
            "review.after_preflight"
        )
        transport.preflight_done = True
        reviewer.sync_client_tools()
        reviewer.emit("system_one_result", **result)
        if (
            not result.get("can_skip_investigation")
            or result.get("confidence", 0) < 0.7
            or result.get("classification") not in ("TP", "FP")
        ):
            return None
        evidence = ReviewPreflight.evidence(sources, item.record)
        if not evidence:
            return None
        ReviewTimer.route("system_one")
        comment = Prompts.get("review.fast_verdict").format(
            model=transport.system_one.model,
            classification=result["classification"],
            confidence=f"{result['confidence']:.3f}",
        )
        explanation = result.get("explanation")
        if isinstance(explanation, str) and explanation.strip():
            comment += " " + explanation[:4000]
        return compact(
            {
                "classification": result["classification"],
                "comment": comment,
                "evidence": evidence,
            }
        )

    @staticmethod
    def evidence(sources, observed):
        evidence = []
        for arguments, data in sources:
            address = (
                arguments.get("file"),
                arguments.get("line"),
                arguments.get("snapshot_id"),
            )
            if address in observed:
                evidence.append(
                    dict(zip(("file", "line", "snapshot_id"), address))
                )
        return evidence[:20]
