"""HTTP client the reviewer UI uses. All case state and every decision go through the API, so
JWT scopes and the audit log apply exactly as they do for any other caller."""

from __future__ import annotations

from collections.abc import Callable

import httpx2 as httpx
from pydantic import TypeAdapter

from disputedesk.api.schemas import Accepted, CaseSummary, DisputeView
from disputedesk.db.models import CaseStatus, Decision

_CASES = TypeAdapter(list[CaseSummary])


class ApiError(Exception):
    """The API refused or failed. `str(exc)` is safe to show the reviewer."""


class ReviewerClient:
    def __init__(
        self,
        base_url: str,
        token: str | Callable[[], str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # A callable is asked for a fresh token on every request, so a UI process that outlives
        # one token's lifetime keeps working.
        self._token = token if callable(token) else (lambda: token)
        self._http = httpx.Client(base_url=base_url, timeout=15, transport=transport)

    def list_cases(self, status: CaseStatus | None = None) -> list[CaseSummary]:
        params = {"case_status": status.value} if status else {}
        return _CASES.validate_python(self._call("GET", "/disputes", params=params))

    def get_case(self, case_id: str) -> DisputeView:
        return DisputeView.model_validate(self._call("GET", f"/disputes/{case_id}"))

    def start(self, case_id: str) -> Accepted:
        return Accepted.model_validate(self._call("POST", "/disputes", json={"case_id": case_id}))

    def decide(self, case_id: str, *, approved: bool, note: str = "") -> Accepted:
        body = {"approved": approved, "note": note}
        return Accepted.model_validate(
            self._call("POST", f"/disputes/{case_id}/approve", json=body)
        )

    def _call(self, method: str, url: str, **kwargs: object) -> object:
        try:
            headers = {"Authorization": f"Bearer {self._token()}"}
            resp = self._http.request(method, url, headers=headers, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            raise ApiError(f"cannot reach the API: {exc}") from exc
        if resp.is_error:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ApiError(f"{resp.status_code}: {detail}")
        return resp.json()


def format_path(path: list[str]) -> str:
    """Graph nodes visited, in order, collapsing a node that repeats back to back."""
    out: list[str] = []
    for node in path:
        if not out or out[-1] != node:
            out.append(node)
    return " → ".join(out) if out else "(not started)"


def money(amount_cents: int) -> str:
    return f"${amount_cents / 100:,.2f}"


def pretty(value: str) -> str:
    """`pending_approval` -> `Pending approval`."""
    return value.replace("_", " ").capitalize()


def badge(text: str, color: str) -> str:
    """Streamlit badge markup. `text` must be one of our own constants, never model output."""
    return f":{color}-badge[{text}]"


_STATUS_COLORS = {
    CaseStatus.OPEN: "gray",
    CaseStatus.IN_REVIEW: "blue",
    CaseStatus.PENDING_APPROVAL: "orange",
    CaseStatus.ESCALATED: "red",
    CaseStatus.RESOLVED_ACCEPTED: "green",
    CaseStatus.RESOLVED_REJECTED: "violet",
}
_DECISION_COLORS = {Decision.ACCEPT: "green", Decision.REJECT: "red", Decision.ESCALATE: "orange"}
_NODE_COLORS = {"escalate": "red", "finalize": "green", "human_gate": "orange"}


def status_color(status: CaseStatus) -> str:
    return _STATUS_COLORS[status]


def decision_color(decision: Decision) -> str:
    return _DECISION_COLORS[decision]


def node_color(node: str) -> str:
    return _NODE_COLORS.get(node, "blue")
