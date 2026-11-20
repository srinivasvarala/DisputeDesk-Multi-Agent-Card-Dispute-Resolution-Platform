"""The reviewer UI's API client, against a mock transport."""

import json

import httpx2 as httpx
import pytest

from disputedesk.db.models import CaseStatus
from disputedesk.ui.client import ApiError, ReviewerClient, format_path, money


def client_for(handler: httpx.MockTransport) -> ReviewerClient:
    return ReviewerClient("http://api", "tok", transport=handler)


def test_list_cases_sends_token_and_status_filter() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "case_id": "case_0001",
                    "reason_code": "fraud",
                    "amount_cents": 12345,
                    "case_status": "pending_approval",
                    "opened_at": "2026-11-01T00:00:00Z",
                }
            ],
        )

    cases = client_for(httpx.MockTransport(handle)).list_cases(CaseStatus.PENDING_APPROVAL)

    assert cases[0].case_id == "case_0001"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert seen[0].url.params["case_status"] == "pending_approval"


def test_decide_posts_decision_and_note() -> None:
    bodies: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        assert request.url.path == "/disputes/case_0001/approve"
        return httpx.Response(202, json={"case_id": "case_0001", "workflow": "running"})

    client_for(httpx.MockTransport(handle)).decide("case_0001", approved=False, note="no")

    assert bodies == [{"approved": False, "note": "no"}]


def test_api_errors_become_api_error_with_detail() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "case is not waiting"})

    with pytest.raises(ApiError, match="409: case is not waiting"):
        client_for(httpx.MockTransport(handle)).start("case_0001")


def test_unreachable_api_is_api_error() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiError, match="cannot reach"):
        client_for(httpx.MockTransport(handle)).list_cases()


def test_format_helpers() -> None:
    assert format_path(["router", "evidence", "resolution", "resolution", "finalize"]) == (
        "router → evidence → resolution → finalize"
    )
    assert format_path([]) == "(not started)"
    assert money(123456) == "$1,234.56"
