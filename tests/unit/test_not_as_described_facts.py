"""Not-as-described filing items (rulebook 07) as data: contact date, return tracking, refusal."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from disputedesk.agents.resolution import filing_attributes
from disputedesk.db.models import ReasonCode, RiskTier
from disputedesk.evals.golden import build_world, load_golden
from disputedesk.graph import rules
from disputedesk.graph.state import CaseFacts, Route
from disputedesk.graph.stubs import _stub_evidence

POSTED = datetime(2026, 7, 1, tzinfo=UTC)
FILED = POSTED + timedelta(days=40)
NAD = ReasonCode.NOT_AS_DESCRIBED
ROUTE = Route(
    reason_code=NAD,
    risk_tier=RiskTier.LOW,
    approval_required=False,
    rationale="Matches the claimed reason.",
)


def facts(**kw: object) -> CaseFacts:
    base: dict[str, object] = {
        "txn_id": "txn_000001",
        "customer_id": "cust_0001",
        "amount_cents": 5_000,
        "claimed_reason": NAD,
        "narrative": "It is not as described.",
        "filed_at": FILED,
    }
    return CaseFacts.model_validate({**base, **kw})


# --- timing computed in code -------------------------------------------------------------------


def test_the_merchant_wait_is_fifteen_days_from_contact() -> None:
    contacted = FILED - timedelta(days=21)

    timing = rules.filing_timing(
        NAD, posted_at=POSTED, filed_at=FILED, merchant_contacted_at=contacted
    )

    assert timing["merchant_wait_elapsed"] is True
    assert timing["days_since_merchant_contact"] == 21
    assert timing["within_filing_window"] is True


def test_the_wait_has_not_elapsed_when_the_contact_was_recent() -> None:
    timing = rules.filing_timing(
        NAD, posted_at=POSTED, filed_at=FILED, merchant_contacted_at=FILED - timedelta(days=14)
    )

    assert timing["merchant_wait_elapsed"] is False


def test_the_boundary_is_exactly_fifteen_days() -> None:
    timing = rules.filing_timing(
        NAD, posted_at=POSTED, filed_at=FILED, merchant_contacted_at=FILED - timedelta(days=15)
    )

    assert timing["merchant_wait_elapsed"] is True


def test_no_recorded_contact_means_no_wait_verdict() -> None:
    timing = rules.filing_timing(NAD, posted_at=POSTED, filed_at=FILED)

    assert "merchant_wait_elapsed" not in timing  # not recorded is not "no"
    assert timing["within_filing_window"] is True


# --- what the Resolution agent is shown --------------------------------------------------------


def test_the_attributes_carry_the_recorded_facts_and_the_computed_wait() -> None:
    evidence = _stub_evidence()
    f = facts(
        merchant_contacted_at=FILED - timedelta(days=20),
        return_tracking_provided=True,
        merchant_refused_return=False,
    )

    text = filing_attributes(f, ROUTE, evidence)

    assert ' return_tracking_provided="true"' in text
    assert ' merchant_refused_return="false"' in text
    assert " merchant_contacted_at=" in text
    assert ' merchant_wait_elapsed="true"' in text


def test_unrecorded_facts_are_left_out_not_shown_as_false() -> None:
    text = filing_attributes(facts(), ROUTE, _stub_evidence())

    assert "return_tracking_provided" not in text
    assert "merchant_refused_return" not in text
    assert "merchant_contacted_at" not in text


def test_other_dispute_types_do_not_get_these_attributes() -> None:
    other = ROUTE.model_copy(update={"reason_code": ReasonCode.DUPLICATE})

    text = filing_attributes(facts(return_tracking_provided=True), other, _stub_evidence())

    assert "return_tracking_provided" not in text


# --- the golden fixtures match what each narrative says ----------------------------------------

GOLDEN = [c for c in load_golden() if c.claimed_reason == NAD]
CONTACT = re.compile(r"contacted the merchant (\d+) days ago|emailed the merchant (\d+) days ago")


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c.id)
def test_the_fixture_contact_date_matches_the_narrative(case: object) -> None:
    narrative = case.narrative  # type: ignore[attr-defined]
    stated = CONTACT.search(narrative)
    recorded = case.merchant_contacted_days_before_filing  # type: ignore[attr-defined]
    if stated is None:
        assert recorded is None, f"{case.id} records a contact the narrative never mentions"  # type: ignore[attr-defined]
    else:
        assert recorded == int(stated.group(1) or stated.group(2))


def test_the_world_builder_turns_those_facts_into_the_case_record() -> None:
    cases = [c for c in GOLDEN if c.id == "g25"]
    now = datetime(2026, 11, 20, tzinfo=UTC)

    world = build_world(cases, now)
    case = next(iter(world.store.cases.values()))

    filed = case.opened_at
    assert case.merchant_contacted_at == filed - timedelta(days=21)
    assert case.return_tracking_provided is True
    assert rules.filing_timing(
        NAD, posted_at=POSTED, filed_at=filed, merchant_contacted_at=case.merchant_contacted_at
    )["merchant_wait_elapsed"]
