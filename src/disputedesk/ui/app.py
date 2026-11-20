"""Reviewer UI: pick a case, read the agents' trace summary and draft, approve or reject.

    make ui     (or: uv run --group ui streamlit run src/disputedesk/ui/app.py)

Needs the API running (`DISPUTEDESK_API_URL`, default http://127.0.0.1:8000). For this local demo
the UI mints its own approver token from JWT_SECRET; the AWS deployment replaces that with Cognito.
Everything from the case (letter, rationale) is model output built from customer text, so it is
shown as plain text and never rendered as markdown or HTML. Only our own constants (node names,
statuses) go into badge markup.
"""

from __future__ import annotations

import os

import streamlit as st

from disputedesk.api.schemas import CaseSummary, DisputeView, WorkflowState
from disputedesk.config import get_settings
from disputedesk.db.models import CaseStatus
from disputedesk.graph.state import Verdict
from disputedesk.security.jwt import issue_token
from disputedesk.security.scopes import Role
from disputedesk.ui.client import (
    ApiError,
    ReviewerClient,
    badge,
    decision_color,
    money,
    node_color,
    pretty,
    status_color,
)

API_URL = os.environ.get("DISPUTEDESK_API_URL", "http://127.0.0.1:8000")
POLL_SECONDS = 3


@st.cache_resource
def get_client() -> ReviewerClient:
    token = issue_token(get_settings(), sub="reviewer-ui", role=Role.APPROVER)
    return ReviewerClient(API_URL, token)


def act_and_rerun(case_id: str) -> None:
    st.session_state["keep"] = case_id  # the case changes status and leaves the filtered list
    st.rerun()


def render_header(view: DisputeView, summary: CaseSummary | None) -> None:
    left, right = st.columns([3, 2], vertical_alignment="center")
    left.markdown(f"## {view.case_id}")
    right.markdown(
        badge(pretty(view.case_status.value), status_color(view.case_status))
        + "  "
        + badge(pretty(view.workflow.value), "gray"),
    )
    with st.container(border=True):
        cols = st.columns(4)
        cols[0].metric("Reason", pretty(summary.reason_code.value) if summary else "n/a")
        cols[1].metric("Amount", money(summary.amount_cents) if summary else "n/a")
        if view.route:
            cols[2].metric("Risk tier", view.route.risk_tier.value.title())
            cols[3].metric("Human approval", "Required" if view.route.approval_required else "No")
        else:
            cols[2].metric("Risk tier", "n/a")
            cols[3].metric("Human approval", "n/a")


def render_trace(view: DisputeView) -> None:
    with st.container(border=True):
        st.markdown("##### Agent trace")
        if view.path:
            chips = "  →  ".join(badge(n.replace("_", " "), node_color(n)) for n in view.path)
            st.markdown(chips)
        else:
            st.caption("Running, no steps recorded yet.")
        if view.route:
            st.caption(f"Router: {view.route.rationale}")
        if view.revision_count:
            st.caption(f"The reviewer sent the draft back {view.revision_count} time(s).")
        if view.review:
            ok = view.review.verdict is Verdict.APPROVE
            st.markdown(
                "**Reviewer agent** "
                + badge(view.review.verdict.value, "green" if ok else "orange")
            )
            if view.review.feedback:
                st.text(view.review.feedback)


def render_draft(view: DisputeView) -> None:
    if not view.draft:
        return
    d = view.draft
    with st.container(border=True):
        top, conf = st.columns([3, 2], vertical_alignment="center")
        top.markdown(
            f"##### Draft v{d.version} " + badge(d.decision.value, decision_color(d.decision))
        )
        if d.confidence is not None:
            conf.progress(d.confidence, text=f"Analyst confidence {d.confidence:.0%}")
        st.markdown("**Rationale**")
        st.text(d.rationale)
        with st.expander("Letter to the cardholder"):
            st.text(d.letter)


def render_actions(client: ReviewerClient, view: DisputeView) -> None:
    if view.workflow is WorkflowState.NOT_STARTED:
        if view.case_status is CaseStatus.OPEN:
            st.info("This case has not been worked yet.")
            if st.button("Start dispute", type="primary"):
                client.start(view.case_id)
                act_and_rerun(view.case_id)
    elif view.workflow is WorkflowState.RUNNING:
        st.info("The agents are working on this case. This page updates by itself.")
    elif view.workflow is WorkflowState.AWAITING_APPROVAL:
        with st.container(border=True):
            st.markdown("##### Your decision")
            st.warning("Policy requires a human to approve this case before it is resolved.")
            note = st.text_input("Note (optional)", max_chars=1_000)
            approve, reject, _ = st.columns([1, 1, 3])
            if approve.button("Approve", type="primary", use_container_width=True):
                client.decide(view.case_id, approved=True, note=note)
                act_and_rerun(view.case_id)
            if reject.button("Reject", use_container_width=True):
                client.decide(view.case_id, approved=False, note=note)
                act_and_rerun(view.case_id)
    elif view.workflow is WorkflowState.FAILED:
        st.error("The workflow failed and the case was escalated to a human.")
    elif view.case_status is CaseStatus.ESCALATED:
        st.warning("Escalated: the agents did not reach a decision they could stand behind.")
    else:
        st.success("Resolved.")


def render_case(client: ReviewerClient, case_id: str, summary: CaseSummary | None) -> None:
    view = client.get_case(case_id)
    render_header(view, summary)
    if view.workflow is not WorkflowState.NOT_STARTED:
        render_trace(view)
        render_draft(view)
    render_actions(client, view)
    if view.workflow is WorkflowState.RUNNING:
        st.session_state["was_running"] = case_id
    elif st.session_state.pop("was_running", None) == case_id:
        st.rerun()  # leave the polling fragment now that the run has stopped


def render_live(client: ReviewerClient, case_id: str, summary: CaseSummary | None) -> None:
    """Poll while the workflow runs, so nobody has to press refresh."""
    running = client.get_case(case_id).workflow is WorkflowState.RUNNING
    panel = st.fragment(run_every=POLL_SECONDS if running else None)(render_case)
    panel(client, case_id, summary)


def row_label(case_id: str, by_id: dict[str, CaseSummary]) -> str:
    c = by_id.get(case_id)
    if c is None:
        return f"{case_id}  ·  status changed"
    return f"{case_id}  ·  {pretty(c.reason_code.value)}  ·  {money(c.amount_cents)}"


def sidebar(client: ReviewerClient) -> tuple[str | None, dict[str, CaseSummary]]:
    with st.sidebar:
        st.markdown("### DisputeDesk")
        st.caption("Card dispute review")
        statuses = [CaseStatus.PENDING_APPROVAL]
        statuses += [s for s in CaseStatus if s is not CaseStatus.PENDING_APPROVAL]
        choice = st.selectbox(
            "Queue",
            statuses,
            format_func=lambda s: pretty(s.value),
            on_change=lambda: st.session_state.pop("keep", None),
        )
        cases = client.list_cases(choice)
        by_id = {c.case_id: c for c in client.list_cases()}  # all statuses, for labels
        st.caption(f"{len(cases)} case(s)")
        ids = [c.case_id for c in cases]
        keep = st.session_state.get("keep")
        if keep and keep not in ids:
            ids.insert(0, keep)  # keep the case we just acted on in view
        selected = st.radio(
            "Cases",
            ids,
            format_func=lambda cid: row_label(cid, by_id),
            label_visibility="collapsed",
        )
        st.button("Refresh queue", use_container_width=True)
    return selected, by_id


def main() -> None:
    st.set_page_config(page_title="DisputeDesk", page_icon="⚖️", layout="wide")
    client = get_client()
    selected, by_id = sidebar(client)
    if selected:
        render_live(client, selected, by_id.get(selected))
    else:
        st.markdown("## Nothing in this queue")
        st.caption("Pick another status in the sidebar.")


try:
    main()
except ApiError as exc:
    st.error(str(exc))
