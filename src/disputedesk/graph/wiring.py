"""Connects the real agents to the graph: builds each agent with its own allowlisted ToolBox,
a resilient LLM and its audit helper, and adapts it to a graph `Node`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from disputedesk.agents import allowlists
from disputedesk.agents.common import DecisionAuditor
from disputedesk.agents.evidence import EvidenceAgent
from disputedesk.agents.policy import PolicyAgent, PolicySearch
from disputedesk.agents.policy_question import policy_question
from disputedesk.agents.resilience import ResilientLLM, ResilientSearch
from disputedesk.agents.resolution import ResolutionAgent
from disputedesk.agents.reviewer import ReviewerAgent
from disputedesk.agents.router import RouterAgent
from disputedesk.agents.toolbox import ToolBox, tool_index
from disputedesk.config import Settings
from disputedesk.graph.builder import Nodes, Update
from disputedesk.graph.state import DisputeState, Verdict
from disputedesk.graph.system_nodes import WorkflowSteps
from disputedesk.llm import LLMClient
from disputedesk.mcp_servers.common.store import AuditSink


@dataclass(frozen=True)
class Dependencies:
    settings: Settings
    llm: LLMClient
    retriever: PolicySearch
    servers: Sequence[MCPServer]  # transactions, merchant, cases
    audit: AuditSink
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


def build_nodes(deps: Dependencies) -> Nodes:
    s = deps.settings
    llm = ResilientLLM(deps.llm, timeout=s.llm_timeout_seconds, retries=s.llm_max_retries)
    index = tool_index(deps.servers)

    def box(agent: str) -> ToolBox:
        return ToolBox(
            agent=agent,
            allowed=allowlists.ALLOWLISTS[agent],
            tools=index,
            audit=deps.audit,
            timeout=s.tool_timeout_seconds,
            retries=s.tool_max_retries,
        )

    def auditor(agent: str) -> DecisionAuditor:
        return DecisionAuditor(deps.audit, agent, deps.clock)

    steps = WorkflowSteps(box(allowlists.SYSTEM))
    router = RouterAgent(
        llm,
        box(allowlists.ROUTER),
        auditor(allowlists.ROUTER),
        threshold_cents=s.human_approval_threshold_cents,
    )
    evidence = EvidenceAgent(llm, box(allowlists.EVIDENCE), auditor(allowlists.EVIDENCE))
    policy = PolicyAgent(
        ResilientSearch(deps.retriever, timeout=s.tool_timeout_seconds, retries=s.tool_max_retries),
        llm,
        deps.audit,
        clock=deps.clock,
    )
    resolution = ResolutionAgent(llm, box(allowlists.RESOLUTION), auditor(allowlists.RESOLUTION))
    reviewer = ReviewerAgent(llm, auditor(allowlists.REVIEWER))

    def route_node(state: DisputeState) -> Update:
        assert state.facts is not None
        return {"route": router.route(state.case_id, state.facts)}

    def evidence_node(state: DisputeState) -> Update:
        assert state.facts is not None and state.route is not None
        return {"evidence": evidence.gather(state.case_id, state.facts, state.route)}

    def policy_node(state: DisputeState) -> Update:
        assert state.facts is not None and state.route is not None and state.evidence is not None
        question = policy_question(state.facts, state.route, state.evidence)
        return {"policy": policy.answer(question, case_id=state.case_id)}

    def resolution_node(state: DisputeState) -> Update:
        assert state.facts and state.route and state.evidence and state.policy
        revising = state.draft is not None and state.review is not None
        draft = resolution.decide(
            state.case_id,
            state.facts,
            state.route,
            state.evidence,
            state.policy,
            previous=state.draft if revising else None,
            feedback=state.review if revising else None,
        )
        return {"draft": draft}

    def reviewer_node(state: DisputeState) -> Update:
        assert state.facts and state.evidence and state.policy and state.draft
        review = reviewer.review(
            state.case_id, state.facts, state.evidence, state.policy, state.draft
        )
        update: Update = {"review": review}
        if review.verdict == Verdict.REVISE:
            update["revision_count"] = state.revision_count + 1
        return update

    return Nodes(
        intake=steps.intake,
        router=route_node,
        evidence=evidence_node,
        policy=policy_node,
        resolution=resolution_node,
        reviewer=reviewer_node,
        request_approval=steps.request_approval,
        finalize=steps.finalize,
        escalate=steps.escalate,
    )
