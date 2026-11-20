"""HTTP client the reviewer UI uses. All case state and every decision go through the API, so
JWT scopes and the audit log apply exactly as they do for any other caller."""

from __future__ import annotations

import httpx2 as httpx
from pydantic import TypeAdapter

from disputedesk.api.schemas import Accepted, CaseSummary, DisputeView
from disputedesk.db.models import CaseStatus

_CASES = TypeAdapter(list[CaseSummary])


class ApiError(Exception):
    """The API refused or failed. `str(exc)` is safe to show the reviewer."""


class ReviewerClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            transport=transport,
        )

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
            resp = self._http.request(method, url, **kwargs)  # type: ignore[arg-type]
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
