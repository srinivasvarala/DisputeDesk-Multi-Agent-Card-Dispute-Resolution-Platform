import pytest

from disputedesk.agents.toolbox import ToolBox, ToolCallError, ToolDeniedError, tool_index
from disputedesk.db.models import AuditOutcome
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore


@pytest.fixture
def box(
    store: InMemoryStore,
    txn_server: AuditedMCPServer,
    merchant_server: AuditedMCPServer,
    cases_server: AuditedMCPServer,
) -> ToolBox:
    return ToolBox(
        agent="evidence_agent",
        allowed=frozenset({"get_txn", "get_case"}),
        tools=tool_index([txn_server, merchant_server, cases_server]),
        audit=store,
        timeout=5,
        retries=0,
    )


def test_tool_index_covers_all_servers(
    txn_server: AuditedMCPServer, merchant_server: AuditedMCPServer, cases_server: AuditedMCPServer
) -> None:
    index = tool_index([txn_server, merchant_server, cases_server])
    assert {"get_txn", "get_profile", "get_case", "save_draft", "update_status"} <= set(index)


def test_allowed_call_returns_result_and_audits_the_agent(
    box: ToolBox, store: InMemoryStore
) -> None:
    txn_id = next(iter(store.transactions))
    result = box.call("get_txn", {"txn_id": txn_id})
    assert result["txn_id"] == txn_id
    assert store.audit[-1].actor == "evidence_agent"
    assert store.audit[-1].tool == "get_txn"


def test_call_outside_allowlist_is_denied_and_audited(box: ToolBox, store: InMemoryStore) -> None:
    with pytest.raises(ToolDeniedError):
        box.call("save_draft", {"case_id": "case_0001"})
    entry = store.audit[-1]
    assert (entry.tool, entry.actor, entry.outcome) == (
        "save_draft",
        "evidence_agent",
        AuditOutcome.INVALID_INPUT,
    )
    assert entry.case_id == "case_0001"


def test_server_errors_surface_as_tool_call_errors(box: ToolBox) -> None:
    with pytest.raises(ToolCallError, match="not found"):
        box.call("get_txn", {"txn_id": "txn_999999"})


@pytest.mark.anyio
async def test_tool_index_works_inside_a_running_event_loop(txn_server: AuditedMCPServer) -> None:
    """API startup builds the graph inside uvicorn's loop; this used to crash there."""
    assert "get_txn" in tool_index([txn_server])
