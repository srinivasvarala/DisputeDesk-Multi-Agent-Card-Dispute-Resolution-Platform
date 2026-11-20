import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.runtime import Runtime, build_runtime
from disputedesk.db.models import ReasonCode
from disputedesk.graph.serde import checkpoint_serde, checkpoint_types
from disputedesk.graph.state import Evidence, Route
from disputedesk.mcp_servers.common.store import InMemoryStore
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET, StubRetriever, fake_llm, needs_approval


def test_registered_types_cover_nested_models_and_enums() -> None:
    names = {c.__name__ for c in checkpoint_types()}
    assert {"DisputeState", "Evidence", "Route", "PolicyAnswer", "Citation", "Merchant"} <= names
    assert {"Leaning", "ReasonCode", "RiskTier", "Decision", "Verdict"} <= names
    assert {"DeliveryStatus", "CustomerHistory", "MerchantSpend", "Transaction"} <= names


def test_full_state_round_trips_through_a_strict_serializer(
    store: InMemoryStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Run a case with the strict serializer: every checkpoint must read back as real models,
    and nothing may be reported as an unregistered type."""
    settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    llm, _ = fake_llm()
    runtime: Runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=store,
        checkpointer=InMemorySaver(serde=checkpoint_serde()),
    )
    case = next(c for c in store.list_cases() if needs_approval(c, store, settings))
    caplog.set_level(logging.WARNING)
    runtime.claim_start(case.case_id)
    runtime.run(case.case_id)

    state = runtime.graph.get_state({"configurable": {"thread_id": case.case_id}}).values
    assert isinstance(state["evidence"], Evidence)
    assert isinstance(state["route"], Route)
    assert state["route"].reason_code in ReasonCode
    assert not [r for r in caplog.records if "unregistered" in r.getMessage()]
