"""Deterministic stand-in for the model, for `LLM_PROVIDER=fake` (tests, CI and offline demos).

It recognises which agent is calling from the system prompt and answers with a valid,
sensible reply derived from the user message. It is not a quality benchmark; real answer
quality is measured by the evals (M7) with a real model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from disputedesk.agents import evidence, policy, resolution, reviewer, router
from disputedesk.llm import Message

AGENT_PROMPTS = {
    "router": router.SYSTEM_PROMPT,
    "evidence": evidence.SYSTEM_PROMPT,
    "policy": policy.SYSTEM_PROMPT,
    "resolution": resolution.SYSTEM_PROMPT,
    "reviewer": reviewer.SYSTEM_PROMPT,
}
_REASON = re.compile(r"<claimed_reason>(\w+)</claimed_reason>")
_EXCERPT_1 = re.compile(r'<excerpt id="1" [^>]*>\n(.*?)\n</excerpt>', re.DOTALL)
_AMOUNT = re.compile(r'amount_cents="(\d+)"')


def agent_of(system: str) -> str:
    """Which agent a system prompt belongs to ("router", "evidence", ...)."""
    for name, prompt in AGENT_PROMPTS.items():
        if system == prompt:
            return name
    raise ValueError("unrecognised system prompt")


def demo_responder(system: str, messages: Sequence[Message]) -> str:
    agent = agent_of(system)
    user = messages[-1].content
    match agent:
        case "router":
            found = _REASON.search(user)
            reason = found.group(1) if found else "fraud"
            return json.dumps({"reason_code": reason, "rationale": "Matches the claimed reason."})
        case "evidence":
            merchant_wins = "&quot;status&quot;: &quot;delivered&quot;" in user
            leaning = "supports_merchant" if merchant_wins else "supports_customer"
            return json.dumps({"summary": f"The facts point one way ({leaning}).", "leaning": leaning})
        case "policy":
            excerpt = _EXCERPT_1.search(user)
            quote = excerpt.group(1)[:200] if excerpt else ""
            citation = {"excerpt_id": 1, "quote": quote}
            return json.dumps({"summary": "See the cited rule.", "citations": [citation]})
        case "resolution":
            reject = "&quot;leaning&quot;: &quot;supports_merchant&quot;" in user
            decision = "reject" if reject else "accept"
            amount = _AMOUNT.search(user)
            cents = int(amount.group(1)) if amount else 0
            letter = (
                f"Dear merchant, we have decided to {decision} this dispute for "
                f"${cents / 100:,.2f}, based on the evidence on file and the DisputeDesk Rulebook."
            )
            return json.dumps(
                {"decision": decision, "rationale": "Based on the evidence.", "letter": letter}
            )
        case _:
            return json.dumps({"verdict": "approve", "feedback": ""})
