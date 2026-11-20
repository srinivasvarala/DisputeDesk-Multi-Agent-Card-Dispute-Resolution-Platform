"""Eval scoring, the threshold gate, and the runner's per-case plumbing (fake model, no network)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from disputedesk.api.runtime import build_runtime
from disputedesk.config import Settings
from disputedesk.db.models import Decision, ReasonCode
from disputedesk.evals import metrics as m
from disputedesk.evals.golden import GoldenCase, build_world, load_golden
from disputedesk.evals.runner import expectation_misses, main, run_case
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.guardrails.interface import Action
from tests.conftest import make_settings
from tests.graph_helpers import TEST_JWT_SECRET, StubRetriever, fake_llm

CASES = load_golden()
BY_ID = {c.id: c for c in CASES}


def outcome(case: GoldenCase, **kw: object) -> m.CaseOutcome:
    base: dict[str, object] = {
        "id": case.id,
        "workflow": "completed",
        "reason_code": case.expected.reason_code,
        "decision": case.expected.decision,
        "approval_required": case.expected.approval_required,
        "letter": "Dear merchant, we have decided.",
        "rationale": "Per the rulebook.",
    }
    return m.CaseOutcome.model_validate({**base, **kw})


def test_router_and_decision_accuracy_count_misses() -> None:
    g01, g02 = BY_ID["g01"], BY_ID["g02"]
    outcomes = {
        "g01": outcome(g01),
        "g02": outcome(g02, reason_code=ReasonCode.FRAUD, decision=Decision.ACCEPT),
    }
    pair = [g01, g02]
    assert m.router_accuracy(pair, outcomes).value == 0.5
    assert m.decision_accuracy(pair, outcomes).value == 0.5


def test_a_case_with_no_outcome_counts_as_wrong_not_skipped() -> None:
    assert m.router_accuracy([BY_ID["g01"]], {}).value == 0.0


def test_injection_passes_only_when_nothing_leaks_and_the_decision_holds() -> None:
    a05 = BY_ID["a05"]  # fake policy 99.4: decision reject/escalate, "99.4" must not appear
    assert m.injection_passed(a05, outcome(a05))
    assert m.injection_passed(a05, outcome(a05, decision=Decision.ESCALATE))
    assert not m.injection_passed(a05, outcome(a05, decision=Decision.ACCEPT))
    assert not m.injection_passed(a05, outcome(a05, letter="As section 99.4 says, accepted."))
    assert not m.injection_passed(a05, None)


def test_approval_bypass_fails_if_the_human_gate_is_lost() -> None:
    a10 = BY_ID["a10"]
    assert m.injection_passed(a10, outcome(a10))
    assert not m.injection_passed(a10, outcome(a10, approval_required=False))


def test_guardrail_metrics() -> None:
    actions = {c.id: Action.ALLOW for c in CASES}
    actions["a01"] = Action.BLOCK  # caught
    actions["a07"] = Action.REDACT  # caught (redact expected)
    actions["g01"] = Action.BLOCK  # a false positive
    catch, fp = m.guardrail_metrics(CASES, actions)
    expecting_action = [c for c in CASES if c.kind == "adversarial" and c.guardrail != "allow"]
    assert catch.denominator == len(expecting_action)
    assert catch.numerator == 2
    assert fp.denominator == 30
    assert fp.numerator == 1


def test_gate_fails_on_low_high_and_missing_metrics() -> None:
    results = [
        m.Metric(name="a", value=0.5),
        m.Metric(name="b", value=0.2),
        m.Metric(name="c", value=None, note="judge crashed"),
        m.Metric(name="ok", value=0.99),
    ]
    limits = {
        "a": m.Threshold(min=0.9),
        "b": m.Threshold(max=0.1),
        "c": m.Threshold(min=0.1),
        "ok": m.Threshold(min=0.9),
        "absent": m.Threshold(min=0.1),
    }
    failed = {f.metric for f in m.check_thresholds(results, limits)}
    assert failed == {"a", "b", "c", "absent"}


def test_gate_boundary_value_passes() -> None:
    limits = {"a": m.Threshold(min=0.9)}
    assert m.check_thresholds([m.Metric(name="a", value=0.9)], limits) == []


def test_committed_thresholds_cover_every_metric_the_runner_reports() -> None:
    limits = m.load_thresholds(Path("evals/thresholds.yml"))
    assert set(limits) == {
        "router_accuracy",
        "decision_accuracy",
        "approval_routing_accuracy",
        "citation_hit_rate",
        "citation_faithfulness",
        "letter_quality_mean",
        "guardrail_catch_rate",
        "guardrail_false_positive_rate",
        "injection_pass_rate",
    }


def test_runner_refuses_the_fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("disputedesk.evals.runner.get_settings", lambda: make_settings())
    assert main([]) == 2


def test_run_case_drives_the_real_graph_and_reports_the_draft() -> None:
    settings: Settings = make_settings(jwt_secret=TEST_JWT_SECRET)
    cases = [BY_ID["g06"], BY_ID["g02"]]  # g06 pauses for approval ($640), g02 completes
    world = build_world(cases, datetime(2026, 11, 20, tzinfo=UTC))
    llm, _ = fake_llm()
    runtime = build_runtime(
        settings=settings,
        llm=llm,
        retriever=StubRetriever(),
        store=world.store,
        checkpointer=InMemorySaver(serde=checkpoint_serde()),
    )
    paused = run_case(runtime, world, BY_ID["g06"])
    done = run_case(runtime, world, BY_ID["g02"])
    assert paused.workflow == "awaiting_approval"
    assert paused.approval_required is True
    assert paused.decision is not None
    assert paused.citations  # the policy agent cited the stub excerpt
    assert done.workflow == "completed"
    assert done.letter
    assert expectation_misses(cases, {"g06": paused, "g02": done}) == []


def test_a_rationale_that_quotes_the_attack_while_refusing_it_is_not_a_leak() -> None:
    a05 = BY_ID["a05"]
    refusing = outcome(a05, rationale="Section 99.4 is not in the rulebook, so it was ignored.")
    assert m.injection_passed(a05, refusing)


def test_lenient_decision_accuracy_allows_labelled_alternatives_but_strict_does_not() -> None:
    g06 = BY_ID["g06"]  # $640: accept + human approval, but the rulebook also says escalate
    escalated = {"g06": outcome(g06, decision=Decision.ESCALATE)}
    assert m.decision_accuracy([g06], escalated).value == 0.0
    assert m.decision_accuracy_lenient([g06], escalated).value == 1.0


def test_faithfulness_judge_retries_malformed_json() -> None:
    pytest.importorskip("deepeval")  # optional `evals` dependency group
    from pydantic import BaseModel

    from disputedesk.evals.deepeval_faithfulness import JudgeModel
    from disputedesk.llm import FakeClient

    class Verdict(BaseModel):
        ok: bool

    replies = iter(['{"ok": tru', '{"ok": true}'])
    judge = JudgeModel(FakeClient(lambda system, messages: next(replies)), "fake")
    assert judge.generate("p", Verdict) == Verdict(ok=True)
