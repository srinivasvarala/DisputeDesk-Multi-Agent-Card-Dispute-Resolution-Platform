"""Per-agent tool scopes enforced by the MCP servers, not only by the client's ToolBox."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from mcp import Client
from mcp_types import CallToolResult, Implementation, RequestParamsMeta

from disputedesk.agents import allowlists
from disputedesk.agents.toolbox import ToolBox, ToolDeniedError, tool_index
from disputedesk.config import Settings
from disputedesk.db.models import AuditEntry, AuditOutcome
from disputedesk.mcp_servers.common.store import InMemoryStore
from disputedesk.mcp_servers.merchant import server as merchant
from disputedesk.mcp_servers.transactions import server as transactions
from disputedesk.security.agent_tokens import META_KEY, CredentialIssuer, CredentialVerifier
from disputedesk.security.jwt import issue_token
from disputedesk.security.scopes import Role
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET
from tests.unit.conftest import fixed_clock
from tests.unit.mcp_helpers import error_text, ok


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return make_settings(jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
def servers(store: InMemoryStore, settings: Settings) -> list[Any]:
    v = CredentialVerifier(settings)
    return [
        transactions.build_server(store, store, clock=fixed_clock, verifier=v),
        merchant.build_server(store, store, clock=fixed_clock, verifier=v),
    ]


async def call_with(
    server: Any, tool: str, args: dict[str, Any], *, name: str, token: str | None
) -> CallToolResult:
    meta = cast(RequestParamsMeta, {META_KEY: token}) if token is not None else None
    async with Client(server, client_info=Implementation(name=name, version="0")) as client:
        return await client.call_tool(tool, args, meta=meta)


def last(store: InMemoryStore) -> AuditEntry:
    return store.audit[-1]


@pytest.mark.anyio
async def test_granted_tool_runs_and_audit_actor_is_the_verified_agent(
    servers: list[Any], store: InMemoryStore, settings: Settings
) -> None:
    token = CredentialIssuer(settings).issue("evidence_agent", ["get_txn"])
    # The client claims a different name; the audit row must show the verified agent.
    result = await call_with(
        servers[0], "get_txn", {"txn_id": "txn_000001"}, name="spoofed-name", token=token
    )
    ok(result)
    assert last(store).actor == "evidence_agent"
    assert last(store).outcome == AuditOutcome.OK


@pytest.mark.anyio
async def test_call_without_credential_is_denied_and_audited(
    servers: list[Any], store: InMemoryStore
) -> None:
    result = await call_with(
        servers[0], "get_txn", {"txn_id": "txn_000001"}, name="evidence_agent", token=None
    )
    assert "credential" in error_text(result)
    entry = last(store)
    assert entry.outcome == AuditOutcome.DENIED
    assert entry.actor == "unauthenticated:evidence_agent"  # the claim is logged, marked unverified


@pytest.mark.anyio
async def test_tool_outside_the_credential_is_denied_even_without_a_toolbox(
    servers: list[Any], store: InMemoryStore, settings: Settings
) -> None:
    """The reviewer has no tools. Calling a server directly with its credential still fails."""
    token = CredentialIssuer(settings).issue(
        allowlists.REVIEWER, allowlists.ALLOWLISTS[allowlists.REVIEWER]
    )
    result = await call_with(
        servers[0], "get_txn", {"txn_id": "txn_000001"}, name="reviewer_agent", token=token
    )
    assert "not granted" in error_text(result)
    assert (last(store).outcome, last(store).actor) == (AuditOutcome.DENIED, "reviewer_agent")


@pytest.mark.anyio
async def test_credential_for_one_tool_does_not_grant_another(
    servers: list[Any], store: InMemoryStore, settings: Settings
) -> None:
    token = CredentialIssuer(settings).issue("router_agent", ["get_txn"])
    result = await call_with(
        servers[1], "get_profile", {"merchant_id": "mer_001"}, name="router_agent", token=token
    )
    assert result.is_error
    assert last(store).outcome == AuditOutcome.DENIED


@pytest.mark.anyio
async def test_expired_tampered_and_user_tokens_are_denied(
    servers: list[Any], store: InMemoryStore, settings: Settings
) -> None:
    issuer = CredentialIssuer(settings)
    old = datetime.now(UTC) - timedelta(hours=1)
    good = issuer.issue("evidence_agent", ["get_txn"])
    other = CredentialIssuer(make_settings(jwt_secret="forged-" + "y" * 40))
    bad_tokens = [
        issuer.issue("evidence_agent", ["get_txn"], now=old),  # expired
        good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB"),  # tampered signature
        other.issue("evidence_agent", ["get_txn"]),  # signed with another secret
        issue_token(settings, sub="ana", role=Role.ADMIN),  # a user token: wrong audience
    ]
    for token in bad_tokens:
        before = len(store.audit)
        result = await call_with(
            servers[0], "get_txn", {"txn_id": "txn_000001"}, name="evidence_agent", token=token
        )
        assert result.is_error
        assert len(store.audit) == before + 1
        assert last(store).outcome == AuditOutcome.DENIED


def test_toolbox_attaches_a_credential_the_servers_accept(
    servers: list[Any], store: InMemoryStore, settings: Settings
) -> None:
    box = ToolBox(
        agent=allowlists.ROUTER,
        allowed=allowlists.ALLOWLISTS[allowlists.ROUTER],
        tools=tool_index(servers),
        audit=store,
        timeout=5,
        retries=0,
        issuer=CredentialIssuer(settings),
    )
    assert box.call("get_txn", {"txn_id": "txn_000001"})["txn_id"] == "txn_000001"
    assert last(store).actor == "router_agent"
    with pytest.raises(ToolDeniedError):  # still refused client-side before reaching a server
        box.call("get_dispute_rate", {"merchant_id": "mer_001"})
