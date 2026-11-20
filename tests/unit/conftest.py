from datetime import datetime

import pytest

from disputedesk.db.seed import REFERENCE_TIME, generate
from disputedesk.mcp_servers.cases import server as cases
from disputedesk.mcp_servers.common.audit import AuditedMCPServer
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.mcp_servers.merchant import server as merchant
from disputedesk.mcp_servers.transactions import server as transactions
from disputedesk.security.agent_tokens import NO_AUTH


def fixed_clock() -> datetime:
    return REFERENCE_TIME


@pytest.fixture
def store() -> InMemoryStore:
    """Fresh synthetic data per test, so write tools never leak between tests."""
    return InMemoryStore(generate())


@pytest.fixture
def txn_server(store: InMemoryStore) -> AuditedMCPServer:
    return transactions.build_server(store, store, clock=fixed_clock, verifier=NO_AUTH)


@pytest.fixture
def merchant_server(store: InMemoryStore) -> AuditedMCPServer:
    return merchant.build_server(store, store, clock=fixed_clock, verifier=NO_AUTH)


@pytest.fixture
def cases_server(store: InMemoryStore) -> AuditedMCPServer:
    return cases.build_server(store, store, store, clock=fixed_clock, verifier=NO_AUTH)
