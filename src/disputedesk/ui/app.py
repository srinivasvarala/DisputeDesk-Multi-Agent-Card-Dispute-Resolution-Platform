"""Reviewer UI: pick a case, read the agents' trace summary and draft, approve or reject.

    uv run --group ui streamlit run src/disputedesk/ui/app.py

Needs the API running (`DISPUTEDESK_API_URL`, default http://127.0.0.1:8000). For this local demo
the UI mints its own approver token from JWT_SECRET; the AWS deployment replaces that with Cognito.
Everything from the case (letter, rationale) is model output built from customer text, so it is
shown as plain text and never rendered as markdown or HTML.
"""

from __future__ import annotations

import os

import streamlit as st

from disputedesk.api.schemas import DisputeView, WorkflowState
from disputedesk.config import get_settings
from disputedesk.db.models import CaseStatus
from disputedesk.security.jwt import issue_token
from disputedesk.security.scopes import Role
from disputedesk.ui.client import ApiError, ReviewerClient, format_path, money

API_URL = os.environ.get("DISPUTEDESK_API_URL", "http://127.0.0.1:8000")


@st.cache_resource
def get_client() -> ReviewerClient:
    token = issue_token(get_settings(), sub="reviewer-ui", role=Role.APPROVER)
    return ReviewerClient(API_URL, token)


def render_case(client: ReviewerClient, view: DisputeView) -> None:
    st.subheader(view.case_id)
    cols = st.columns(3)
    cols[0].metric("Case status", view.case_status.value)
    cols[1].metric("Workflow", view.workflow.value)
    cols[2].metric("Revisions", view.revision_count)

    if view.workflow is WorkflowState.NOT_STARTED:
        if view.case_status is CaseStatus.OPEN and st.button("Start dispute"):
            client.start(view.case_id)
            st.session_state["keep"] = view.case_id
            st.rerun()
        return

    st.markdown("**Trace summary**")
    st.text(format_path(view.path))
    if view.route:
        st.text(
            f"Route: {view.route.reason_code.value} · risk {view.route.risk_tier.value} · "
            f"human approval {'required' if view.route.approval_required else 'not required'}\n"
            f"{view.route.rationale}"
        )
    if view.review:
        st.text(f"Reviewer agent: {view.review.verdict.value}. {view.review.feedback}")

    if view.draft:
        d = view.draft
        conf = f", confidence {d.confidence:.2f}" if d.confidence is not None else ""
        st.markdown(f"**Draft v{d.version}: {d.decision.value}**{conf}")
        st.text(d.rationale)
        with st.expander("Letter to the cardholder"):
            st.text(d.letter)

    if view.workflow is WorkflowState.RUNNING:
        st.info("The workflow is running. Refresh in a moment.")
        st.button("Refresh")
    elif view.workflow is WorkflowState.AWAITING_APPROVAL:
        note = st.text_input("Note (optional)", max_chars=1_000)
        approve, reject = st.columns(2)
        if approve.button("Approve", type="primary"):
            client.decide(view.case_id, approved=True, note=note)
            st.session_state["keep"] = view.case_id
            st.rerun()
        if reject.button("Reject"):
            client.decide(view.case_id, approved=False, note=note)
            st.session_state["keep"] = view.case_id
            st.rerun()
    elif view.workflow is WorkflowState.FAILED:
        st.error("The workflow failed and the case was escalated to a human.")


def main() -> None:
    st.set_page_config(page_title="DisputeDesk reviewer", layout="wide")
    st.title("DisputeDesk reviewer")
    client = get_client()

    with st.sidebar:
        choice = st.selectbox(
            "Show cases",
            [
                CaseStatus.PENDING_APPROVAL,
                *[s for s in CaseStatus if s is not CaseStatus.PENDING_APPROVAL],
            ],
            format_func=lambda s: s.value,
        )
        if st.button("Refresh list"):
            st.rerun()
        cases = client.list_cases(choice)
        st.caption(f"{len(cases)} case(s)")
        labels = {
            c.case_id: f"{c.case_id} · {c.reason_code.value} · {money(c.amount_cents)}"
            for c in cases
        }
        ids = list(labels)
        # A case we just acted on changes status and falls out of this filter; keep it
        # selected so the reviewer sees the outcome instead of jumping to another case.
        keep = st.session_state.get("keep")
        if keep and keep not in ids:
            ids.insert(0, keep)
        selected = st.radio(
            "Cases",
            ids,
            format_func=lambda cid: labels.get(cid, f"{cid} · (status changed)"),
            label_visibility="collapsed",
        )

    if selected:
        render_case(client, client.get_case(selected))
    else:
        st.write("No cases with this status.")


try:
    main()
except ApiError as exc:
    st.error(str(exc))
