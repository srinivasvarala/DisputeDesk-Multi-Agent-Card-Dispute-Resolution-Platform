"""The golden set must be well-formed and agree with the rulebook it is scored against."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from disputedesk.db.models import CaseStatus
from disputedesk.evals.golden import Attack, GoldenCase, build_world, load_golden
from disputedesk.graph import rules
from disputedesk.rag.ingest import SECTION_SEPARATOR, load_corpus

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def cases() -> list[GoldenCase]:
    return load_golden()


def test_size_and_mix(cases: list[GoldenCase]) -> None:
    adversarial = [c for c in cases if c.kind == "adversarial"]
    assert len(cases) == 42
    assert 8 <= len(adversarial) <= 10
    assert len({c.attack for c in adversarial}) == len(adversarial)  # one of each attack kind


def test_every_expected_citation_is_a_real_rulebook_section(cases: list[GoldenCase]) -> None:
    chunks = load_corpus(Path("data/policies"))
    for c in cases:
        for t in c.expected.citations:
            assert any(
                ch.source == t.source
                and (
                    ch.section == t.section or ch.section.startswith(t.section + SECTION_SEPARATOR)
                )
                for ch in chunks
            ), f"{c.id}: {t} matches no chunk"


def test_expected_approval_follows_the_routing_rules(cases: list[GoldenCase]) -> None:
    for c in cases:
        tier = rules.risk_tier(
            amount_cents=c.amount_cents,
            merchant_risk=c.merchant.risk_tier,
            reason=c.expected.reason_code,
            threshold_cents=50_000,
        )
        assert rules.approval_required(tier) == c.expected.approval_required, c.id


def test_adversarial_cases_are_scored_against_a_clean_twin(cases: list[GoldenCase]) -> None:
    by_id = {c.id: c for c in cases}
    for c in cases:
        if c.kind == "normal":
            assert c.twin is None
            assert c.attack == Attack.NONE
        else:
            twin = by_id[c.twin or ""]
            assert twin.kind == "normal"
            assert twin.expected.decision in c.expected.decisions
            assert twin.expected.reason_code == c.expected.reason_code


def test_world_has_one_open_case_per_golden_row(cases: list[GoldenCase]) -> None:
    world = build_world(cases, NOW)
    assert len(world.case_ids) == len(cases)
    for c in cases:
        stored = world.store.cases[world.case_ids[c.id]]
        assert stored.status == CaseStatus.OPEN
        assert stored.customer_narrative == c.narrative
        assert stored.reason_code == c.claimed_reason


def test_world_carries_the_new_tool_facts(cases: list[GoldenCase]) -> None:
    from datetime import timedelta

    from disputedesk.db.models import EntryMode

    world = build_world(cases, NOW)
    store, ids = world.store, world.case_ids

    def txn(gid: str):
        return store.transactions[store.cases[ids[gid]].txn_id]

    assert store.shipments[txn("g31").txn_id].delivery_address_matches is False
    assert store.shipments[txn("g32").txn_id].signature_confirmed is False
    assert store.shipments[txn("g02").txn_id].delivery_address_matches is True
    assert txn("g13").entry_mode == EntryMode.CHIP_PIN
    assert txn("g14").entry_mode == EntryMode.MAGSTRIPE_FALLBACK
    lost = store.cases[ids["g12"]].card_reported_lost_at
    assert lost is not None and lost < txn("g12").posted_at - timedelta(hours=5)
    assert store.cases[ids["g10"]].fraud_declaration_signed is True
    assert store.cases[ids["g02"]].fraud_declaration_signed is None  # not a fraud filing
