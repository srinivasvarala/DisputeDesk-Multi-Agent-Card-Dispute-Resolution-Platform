"""The Streamlit page, driven by Streamlit's own test runner. Skipped where the `ui` group is
not installed (CI installs only the default groups); run locally with `uv run --group ui pytest`."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from disputedesk.api.schemas import CaseSummary, DisputeView, WorkflowState
from disputedesk.db.models import CaseStatus, ReasonCode
from disputedesk.ui.client import ApiError, ReviewerClient

APP = str(Path(__file__).resolve().parents[2] / "src/disputedesk/ui/app.py")


def summary(case_id: str, status: CaseStatus) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        reason_code=ReasonCode.FRAUD,
        amount_cents=12_345,
        case_status=status,
        opened_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


def awaiting(_self: ReviewerClient, case_id: str) -> DisputeView:
    return DisputeView(
        case_id=case_id,
        case_status=CaseStatus.PENDING_APPROVAL,
        workflow=WorkflowState.AWAITING_APPROVAL,
    )


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "x" * 40)


def test_an_api_error_while_rendering_a_case_is_shown_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = [summary("case_0001", CaseStatus.PENDING_APPROVAL)]
    monkeypatch.setattr(ReviewerClient, "list_cases", lambda *_, **__: pending)

    def boom(*_: object, **__: object) -> DisputeView:
        raise ApiError("409: case is not waiting")

    monkeypatch.setattr(ReviewerClient, "get_case", boom)

    at = AppTest.from_file(APP).run()

    assert not at.exception
    assert [e.value for e in at.error] == ["409: case is not waiting"]


def test_a_failed_decision_is_shown_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Button clicks rerun only the fragment, outside the page-level error handler."""
    pending = [summary("case_0001", CaseStatus.PENDING_APPROVAL)]
    monkeypatch.setattr(ReviewerClient, "list_cases", lambda *_, **__: pending)
    monkeypatch.setattr(ReviewerClient, "get_case", awaiting)

    def refuse(*_: object, **__: object) -> None:
        raise ApiError("409: case is not waiting for approval")

    monkeypatch.setattr(ReviewerClient, "decide", refuse)

    at = AppTest.from_file(APP)
    at.session_state["persona"] = "Ravi (approver)"
    at.run()
    at.button[1].click().run()  # buttons: Refresh queue, Approve, Reject

    assert not at.exception
    assert [e.value for e in at.error] == ["409: case is not waiting for approval"]


def test_a_note_typed_for_one_case_does_not_follow_you_to_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        summary("case_0001", CaseStatus.PENDING_APPROVAL),
        summary("case_0002", CaseStatus.PENDING_APPROVAL),
    ]
    monkeypatch.setattr(ReviewerClient, "list_cases", lambda *_, **__: cases)
    monkeypatch.setattr(ReviewerClient, "get_case", awaiting)

    at = AppTest.from_file(APP)
    at.session_state["persona"] = "Ravi (approver)"
    at.run()
    at.text_input(key="note-case_0001").set_value("fraud confirmed").run()
    at.radio[0].set_value("case_0002").run()

    assert at.text_input(key="note-case_0002").value == ""


def started_by(who: str):  # type: ignore[no-untyped-def]
    def get(_self: ReviewerClient, case_id: str) -> DisputeView:
        return awaiting(_self, case_id).model_copy(update={"started_by": who})

    return get


def open_as(persona: str, monkeypatch: pytest.MonkeyPatch, who_started: str | None) -> AppTest:
    pending = [summary("case_0001", CaseStatus.PENDING_APPROVAL)]
    monkeypatch.setattr(ReviewerClient, "list_cases", lambda *_, **__: pending)
    monkeypatch.setattr(ReviewerClient, "get_case", started_by(who_started or ""))
    at = AppTest.from_file(APP)
    at.session_state["persona"] = persona
    return at.run()


def labels(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


def test_the_analyst_is_told_an_approver_must_decide(monkeypatch: pytest.MonkeyPatch) -> None:
    at = open_as("Maya (analyst)", monkeypatch, "someone-else")
    assert "Approve" not in labels(at)
    assert any("An approver has to decide" in i.value for i in at.info)


def test_the_person_who_started_a_case_cannot_decide_it_in_the_ui_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = open_as("Ravi (approver)", monkeypatch, "demo-approver-ravi")
    assert "Approve" not in labels(at)
    assert any("maker-checker" in i.value for i in at.info)


def test_a_different_approver_gets_the_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    at = open_as("Ravi (approver)", monkeypatch, "demo-analyst-maya")
    assert {"Approve", "Reject"} <= set(labels(at))
